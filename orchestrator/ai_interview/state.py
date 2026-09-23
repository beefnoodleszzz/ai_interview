"""Durable episode state updates and GPU budget guards."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .manifest import find_shot, load_manifest


class BudgetExhausted(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _limits(manifest: dict[str, Any]) -> dict[str, float]:
    return {key: float(value) for key, value in manifest["retry_policy"].items()}


def _runtime(shot: dict[str, Any]) -> dict[str, Any]:
    value = shot.setdefault("runtime", {})
    value.setdefault("attempt_count", 0)
    value.setdefault("gpu_minutes", 0.0)
    value.setdefault("job_revision", int(shot.get("job_revision", 1)))
    return value


def budget_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    limits = _limits(manifest)
    shot_usage = {
        shot["id"]: {
            "attempt_count": int(shot.get("runtime", {}).get("attempt_count", 0)),
            "gpu_minutes": round(float(shot.get("runtime", {}).get("gpu_minutes", 0.0)), 3),
            "job_id": shot.get("runtime", {}).get("job_id"),
            "prompt_id": shot.get("runtime", {}).get("prompt_id"),
            "status": shot["status"],
        }
        for shot in manifest["shots"]
    }
    return {
        "episode_id": manifest["episode_id"],
        "gpu_minutes_used": round(sum(item["gpu_minutes"] for item in shot_usage.values()), 3),
        "gpu_minutes_limit": limits["max_gpu_minutes_per_episode"],
        "shots": shot_usage,
    }


def begin_h3_attempt(manifest_path: str | Path, shot_id: str, job_id: str) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    runtime = _runtime(shot)
    if runtime.get("job_id") == job_id:
        return budget_snapshot(manifest)
    if shot["status"] not in {"READY_FOR_H3", "NEEDS_RETRY"}:
        raise ValueError(f"{shot_id} must be READY_FOR_H3 or NEEDS_RETRY before a new H3 attempt")
    limits = _limits(manifest)
    snapshot = budget_snapshot(manifest)
    reasons = []
    if runtime["attempt_count"] >= limits["max_attempts_per_shot"]:
        reasons.append("shot attempt limit reached")
    if runtime["gpu_minutes"] >= limits["max_gpu_minutes_per_shot"]:
        reasons.append("shot GPU-minute limit reached")
    if snapshot["gpu_minutes_used"] >= limits["max_gpu_minutes_per_episode"]:
        reasons.append("episode GPU-minute limit reached")
    if reasons:
        shot["status"] = "NEEDS_MANUAL_REVIEW"
        runtime["last_error"] = "; ".join(reasons)
        runtime["updated_at"] = _now()
        _atomic_save(path, manifest)
        raise BudgetExhausted("; ".join(reasons))
    revision = int(runtime["job_revision"])
    if job_id.rsplit("_r", 1)[-1] != f"{revision:02d}":
        raise ValueError(f"job revision mismatch: expected r{revision:02d} for {shot_id}")
    runtime.update({
        "attempt_count": int(runtime["attempt_count"]) + 1,
        "job_revision": revision,
        "job_id": job_id,
        "prompt_id": None,
        "job_gpu_minutes": 0.0,
        "last_remote_status": None,
        "last_error": None,
        "last_failure_class": None,
        "started_at": _now(),
        "updated_at": _now(),
    })
    shot["job_revision"] = revision
    shot["status"] = "H3_RENDERING"
    _atomic_save(path, manifest)
    return budget_snapshot(manifest)


def record_remote_job(manifest_path: str | Path, shot_id: str, remote: dict[str, Any]) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    runtime = _runtime(shot)
    if remote.get("job_id") and runtime.get("job_id") not in {None, remote["job_id"]}:
        raise ValueError("remote job does not match the manifest's active revision")
    if remote.get("job_id") and runtime.get("job_id") is None and str(remote.get("status", "")).upper() not in {"FAILED", "ERROR"}:
        runtime["attempt_count"] = int(runtime["attempt_count"]) + 1
        runtime["job_id"] = remote["job_id"]
        runtime["started_at"] = remote.get("started_at") or _now()
    state = str(remote.get("status", "")).upper()
    gpu_minutes = remote.get("gpu_minutes")
    if isinstance(gpu_minutes, (int, float)) and not isinstance(gpu_minutes, bool):
        previous = float(runtime.get("job_gpu_minutes", 0.0))
        current = max(previous, float(gpu_minutes))
        runtime["gpu_minutes"] = float(runtime["gpu_minutes"]) + (current - previous)
        runtime["job_gpu_minutes"] = current
    if isinstance(remote.get("prompt_id"), str):
        runtime["prompt_id"] = remote["prompt_id"]
    limits = _limits(manifest)
    used_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    exhausted = (
        int(runtime["attempt_count"]) >= limits["max_attempts_per_shot"]
        or float(runtime["gpu_minutes"]) >= limits["max_gpu_minutes_per_shot"]
        or used_episode >= limits["max_gpu_minutes_per_episode"]
    )
    if exhausted and state not in {"COMPLETE", "DONE"}:
        shot["status"] = "NEEDS_MANUAL_REVIEW"
    elif state in {"SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
        shot["status"] = "H3_RENDERING"
    elif state in {"COMPLETE", "DONE"}:
        shot["status"] = "H3_QC"
    elif state in {"FAILED", "ERROR"}:
        if runtime.get("last_remote_status") not in {"FAILED", "ERROR"}:
            shot["status"] = "NEEDS_MANUAL_REVIEW" if exhausted else "NEEDS_RETRY"
            runtime["last_failure_class"] = str(remote.get("failure_class", "unknown"))
            runtime["last_error"] = remote.get("error")
            if shot["status"] == "NEEDS_RETRY":
                runtime["job_revision"] = int(runtime["job_revision"]) + 1
                shot["job_revision"] = runtime["job_revision"]
    runtime["last_remote_status"] = state
    runtime["updated_at"] = _now()
    _atomic_save(path, manifest)
    return budget_snapshot(manifest)


def read_budget(manifest_path: str | Path) -> dict[str, Any]:
    return budget_snapshot(load_manifest(manifest_path))


def record_voice_remote(manifest_path: str | Path, voice_job: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    if not isinstance(job_id, str) or remote.get("job_id") not in {None, job_id}:
        raise ValueError("voice job id does not match its manifest request")
    status = str(remote.get("status", "")).upper()
    manifest.setdefault("voice_jobs", {})[job_id] = {
        "status": status,
        "updated_at": _now(),
        "error": remote.get("error"),
    }
    spent_by_shot = remote.get("gpu_minutes_by_shot", {})
    if isinstance(spent_by_shot, dict):
        for shot_id, amount in spent_by_shot.items():
            shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
            if shot is None or not isinstance(amount, (int, float)) or isinstance(amount, bool):
                continue
            runtime = _runtime(shot)
            usage = runtime.setdefault("voice_gpu_minutes_by_job", {})
            current = max(float(usage.get(job_id, 0.0)), float(amount))
            runtime["gpu_minutes"] = float(runtime["gpu_minutes"]) + current - float(usage.get(job_id, 0.0))
            usage[job_id] = current
    shot_ids = {line.get("shot_id") for line in voice_job.get("lines", []) if isinstance(line.get("shot_id"), str)}
    for shot_id in shot_ids:
        shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
        if shot is None:
            continue
        if status in {"SUBMITTED", "QUEUED", "RUNNING"} and shot["status"] in {"PLANNED", "AUDIO_RENDERING", "NEEDS_RETRY"}:
            shot["status"] = "AUDIO_RENDERING"
        elif status == "COMPLETE" and shot["status"] == "AUDIO_RENDERING":
            shot["status"] = "AUDIO_QC"
        elif status in {"FAILED", "ERROR"} and shot["status"] == "AUDIO_RENDERING":
            shot["status"] = "NEEDS_RETRY"
    _atomic_save(path, manifest)
    return budget_snapshot(manifest)


def record_voice_design_remote(manifest_path: str | Path, voice_job: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    if not isinstance(job_id, str) or remote.get("job_id") not in {None, job_id}:
        raise ValueError("VoiceDesign job id does not match its manifest request")
    jobs = manifest.setdefault("voice_design_jobs", {})
    entry = jobs.setdefault(job_id, {})
    status = str(remote.get("status", "")).upper()
    current = max(float(entry.get("gpu_minutes", 0.0)), float(remote.get("gpu_minutes", 0.0) or 0.0))
    entry.update({"status": status, "gpu_minutes": round(current, 3), "updated_at": _now(), "error": remote.get("error")})
    entry["character_id"] = voice_job.get("character_id")
    spent = sum(float(item.get("gpu_minutes", 0.0)) for item in jobs.values() if isinstance(item, dict))
    limit = float(manifest.get("voice_design_budget_minutes", 15.0))
    if spent >= limit and status not in {"COMPLETE", "DONE"}:
        entry["status"] = "NEEDS_MANUAL_REVIEW"
    _atomic_save(path, manifest)
    return {"status": entry["status"], "gpu_minutes_used": round(spent, 3), "gpu_minutes_limit": limit}
