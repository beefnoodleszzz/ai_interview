"""Durable episode state updates and GPU budget guards."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import yaml

from .hashing import sha256_file
from .manifest import find_shot, load_manifest


class BudgetExhausted(ValueError):
    pass


F = TypeVar("F", bound=Callable[..., Any])


@contextmanager
def _manifest_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _locked_manifest_update(function: F) -> F:
    @wraps(function)
    def wrapped(manifest_path: str | Path, *args: Any, **kwargs: Any) -> Any:
        path = Path(manifest_path).resolve()
        with _manifest_lock(path):
            return function(path, *args, **kwargs)

    return wrapped  # type: ignore[return-value]


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


def _postprocess_reservations(manifest: dict[str, Any], shot_id: str | None = None) -> float:
    active_states = {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
    jobs = manifest.get("postprocess_jobs", {})
    if not isinstance(jobs, dict):
        return 0.0
    return sum(
        float(item.get("reserved_gpu_minutes", 0.0))
        for item in jobs.values()
        if isinstance(item, dict)
        and (shot_id is None or item.get("shot_id") == shot_id)
        and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )


def _h3_reservations(manifest: dict[str, Any], shot_id: str | None = None) -> float:
    return sum(
        float(item.get("runtime", {}).get("h3_reserved_gpu_minutes", 0.0))
        for item in manifest.get("shots", [])
        if shot_id is None or item.get("id") == shot_id
    )


def _voice_reservations(manifest: dict[str, Any], shot_id: str | None = None) -> float:
    active_states = {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
    jobs = manifest.get("voice_jobs", {})
    if not isinstance(jobs, dict):
        return 0.0
    return sum(
        float(item.get("reserved_gpu_minutes_by_shot", {}).get(shot_id, 0.0)) if shot_id else float(item.get("reserved_gpu_minutes", 0.0))
        for item in jobs.values()
        if isinstance(item, dict)
        and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )


def _voice_design_reservations(manifest: dict[str, Any]) -> float:
    active_states = {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
    jobs = manifest.get("voice_design_jobs", {})
    if not isinstance(jobs, dict):
        return 0.0
    return sum(
        float(item.get("reserved_gpu_minutes", 0.0))
        for item in jobs.values()
        if isinstance(item, dict)
        and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )


def budget_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    limits = _limits(manifest)
    reserved_episode = _postprocess_reservations(manifest) + _h3_reservations(manifest) + _voice_reservations(manifest)
    used_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    shot_usage = {
        shot["id"]: {
            "attempt_count": int(shot.get("runtime", {}).get("attempt_count", 0)),
            "postprocess_attempt_count": int(shot.get("runtime", {}).get("postprocess_attempt_count", 0)),
            "voice_attempt_count": int(shot.get("runtime", {}).get("voice_attempt_count", 0)),
            "attempts_total": int(shot.get("runtime", {}).get("attempt_count", 0)) + int(shot.get("runtime", {}).get("postprocess_attempt_count", 0)) + int(shot.get("runtime", {}).get("voice_attempt_count", 0)),
            "gpu_minutes": round(float(shot.get("runtime", {}).get("gpu_minutes", 0.0)), 3),
            "gpu_minutes_reserved": round(_postprocess_reservations(manifest, shot["id"]) + _voice_reservations(manifest, shot["id"]), 3),
            "voice_gpu_minutes_reserved": round(_voice_reservations(manifest, shot["id"]), 3),
            "h3_gpu_minutes_reserved": round(_h3_reservations(manifest, shot["id"]), 3),
            "h3_job_reserved_gpu_minutes": round(float(shot.get("runtime", {}).get("h3_job_reserved_gpu_minutes", 0.0)), 3),
            "gpu_minutes_available": round(max(0.0, float(limits["max_gpu_minutes_per_shot"]) - float(shot.get("runtime", {}).get("gpu_minutes", 0.0)) - _postprocess_reservations(manifest, shot["id"]) - _h3_reservations(manifest, shot["id"]) - _voice_reservations(manifest, shot["id"])), 3),
            "job_id": shot.get("runtime", {}).get("job_id"),
            "prompt_id": shot.get("runtime", {}).get("prompt_id"),
            "status": shot["status"],
        }
        for shot in manifest["shots"]
    }
    return {
        "episode_id": manifest["episode_id"],
        "gpu_minutes_used": round(used_episode, 3),
        "gpu_minutes_reserved": round(reserved_episode, 3),
        "gpu_minutes_available": round(max(0.0, float(limits["max_gpu_minutes_per_episode"]) - used_episode - reserved_episode), 3),
        "gpu_minutes_limit": limits["max_gpu_minutes_per_episode"],
        "shots": shot_usage,
    }


@_locked_manifest_update
def begin_h3_attempt(manifest_path: str | Path, shot_id: str, job_id: str) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    runtime = _runtime(shot)
    if runtime.get("job_id") == job_id:
        active_states = {"SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
        if shot.get("status") == "H3_RENDERING" and float(runtime.get("h3_reserved_gpu_minutes", 0.0)) > 0 and str(runtime.get("last_remote_status") or "").upper() in active_states | {""}:
            return budget_snapshot(manifest)
        raise ValueError("H3 job_id has already been used; terminal or cleaned jobs require a new revision")
    if shot["status"] not in {"READY_FOR_H3", "NEEDS_RETRY"}:
        raise ValueError(f"{shot_id} must be READY_FOR_H3 or NEEDS_RETRY before a new H3 attempt")
    limits = _limits(manifest)
    snapshot = budget_snapshot(manifest)
    reasons = []
    if runtime["attempt_count"] + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0)) >= limits["max_attempts_per_shot"]:
        reasons.append("shot attempt limit reached")
    if runtime["gpu_minutes"] + _postprocess_reservations(manifest, shot_id) + _h3_reservations(manifest, shot_id) + _voice_reservations(manifest, shot_id) >= limits["max_gpu_minutes_per_shot"]:
        reasons.append("shot GPU-minute limit reached")
    if snapshot["gpu_minutes_used"] + snapshot["gpu_minutes_reserved"] >= limits["max_gpu_minutes_per_episode"]:
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
    reserve = min(
        limits["max_gpu_minutes_per_shot"] - float(runtime["gpu_minutes"]) - _postprocess_reservations(manifest, shot_id) - _h3_reservations(manifest, shot_id) - _voice_reservations(manifest, shot_id),
        limits["max_gpu_minutes_per_episode"] - snapshot["gpu_minutes_used"] - snapshot["gpu_minutes_reserved"],
    )
    if reserve <= 0:
        raise BudgetExhausted("no unreserved GPU-minute budget remains")
    runtime.update({
        "attempt_count": int(runtime["attempt_count"]) + 1,
        "job_revision": revision,
        "job_id": job_id,
        "prompt_id": None,
        "job_gpu_minutes": 0.0,
        "h3_job_reserved_gpu_minutes": reserve,
        "h3_reserved_gpu_minutes": reserve,
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


@_locked_manifest_update
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
    selection = shot.get("candidate_selection")
    selected_job = runtime.get("job_id")
    has_selected_candidate = (
        isinstance(selection, dict)
        and isinstance(shot.get("selected_video"), str)
        and isinstance(selection.get("candidate_id"), str)
        and isinstance(selected_job, str)
        and selection["candidate_id"].startswith(selected_job + "_candidate_")
    )
    previous_status = str(runtime.get("last_remote_status", "")).upper()
    terminal_statuses = {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}
    if previous_status in terminal_statuses and state != previous_status:
        state = previous_status
    gpu_minutes = remote.get("gpu_minutes")
    if isinstance(gpu_minutes, (int, float)) and not isinstance(gpu_minutes, bool):
        previous = float(runtime.get("job_gpu_minutes", 0.0))
        current = max(previous, float(gpu_minutes))
        runtime["gpu_minutes"] = float(runtime["gpu_minutes"]) + (current - previous)
        runtime["job_gpu_minutes"] = current
        runtime["h3_reserved_gpu_minutes"] = max(0.0, float(runtime.get("h3_job_reserved_gpu_minutes", 0.0)) - current)
    if isinstance(remote.get("prompt_id"), str):
        runtime["prompt_id"] = remote["prompt_id"]
    if state in {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}:
        runtime["h3_reserved_gpu_minutes"] = 0.0
    limits = _limits(manifest)
    used_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    exhausted = (
        int(runtime["attempt_count"]) + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0)) >= limits["max_attempts_per_shot"]
        or float(runtime["gpu_minutes"]) >= limits["max_gpu_minutes_per_shot"]
        or used_episode >= limits["max_gpu_minutes_per_episode"]
    )
    if has_selected_candidate:
        if shot["status"] not in {"DONE", "UPSCALED"}:
            grade = selection.get("lip_grade")
            shot["status"] = "READY_FOR_EDIT" if grade in {"A", "B"} else "NEEDS_LIPSYNC" if grade == "C" else "NEEDS_RETRY"
    elif exhausted and state not in {"COMPLETE", "DONE"}:
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


@_locked_manifest_update
def reserve_voice_attempt(manifest_path: str | Path, voice_job: dict[str, Any]) -> dict[str, Any]:
    """Atomically reserve shared episode/shot GPU time for one IndexTTS job."""
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    if not isinstance(job_id, str) or voice_job.get("episode_id") != manifest.get("episode_id"):
        raise ValueError("voice reservation must match the episode and include a job_id")
    jobs = manifest.setdefault("voice_jobs", {})
    existing = jobs.get(job_id)
    if isinstance(existing, dict):
        if int(existing.get("revision", voice_job.get("revision", 1))) != int(voice_job.get("revision", 1)):
            raise ValueError("voice job id is already reserved for a different revision")
        status = str(existing.get("remote_status", existing.get("status", ""))).upper()
        if status in {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
            by_shot = {key: float(value) for key, value in existing.get("reserved_gpu_minutes_by_shot", {}).items()}
            if not by_shot or sum(by_shot.values()) <= 0:
                raise RuntimeError("existing voice reservation has no usable GPU budget")
            return {
                "remaining_gpu_minutes_per_episode": sum(by_shot.values()),
                "remaining_gpu_minutes_by_shot": by_shot,
            }
        raise RuntimeError(f"voice job {job_id} is already terminal; create its next revision")

    expected_revision = int(manifest.get("voice_job_revision", 1))
    if int(voice_job.get("revision", 1)) != expected_revision:
        raise ValueError(f"stale voice revision: expected r{expected_revision:02d}")

    lines = voice_job.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("voice job has no lines to reserve")
    shot_ids = list(dict.fromkeys(line.get("shot_id") for line in lines if isinstance(line, dict)))
    if not shot_ids or any(not isinstance(shot_id, str) for shot_id in shot_ids):
        raise ValueError("every voice line must map to a shot before GPU budget reservation")
    shot_by_id = {item["id"]: item for item in manifest["shots"]}
    if any(shot_id not in shot_by_id for shot_id in shot_ids):
        raise ValueError("voice job refers to an unknown shot")

    limits = _limits(manifest)
    requested = voice_job.get("budget", {})
    requested_episode = float(requested.get("remaining_gpu_minutes_per_episode", 0.0))
    requested_by_shot = requested.get("remaining_gpu_minutes_by_shot", {})
    if not isinstance(requested_by_shot, dict):
        raise ValueError("voice job shot budget must be an object")
    spent_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    reserved_episode = _postprocess_reservations(manifest) + _h3_reservations(manifest) + _voice_reservations(manifest)
    episode_available = min(
        requested_episode,
        limits["max_gpu_minutes_per_episode"] - spent_episode - reserved_episode,
    )
    allocated: dict[str, float] = {}
    for shot_id in shot_ids:
        shot = shot_by_id[shot_id]
        runtime = _runtime(shot)
        attempts = int(runtime["attempt_count"]) + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0))
        if attempts >= limits["max_attempts_per_shot"]:
            shot["status"] = "NEEDS_MANUAL_REVIEW"
            runtime["last_error"] = "shot attempt limit reached before IndexTTS reservation"
            _atomic_save(path, manifest)
            raise BudgetExhausted(f"{shot_id} attempt limit reached; voice job was not reserved")
        shot_reserved = _postprocess_reservations(manifest, shot_id) + _h3_reservations(manifest, shot_id) + _voice_reservations(manifest, shot_id)
        shot_available = limits["max_gpu_minutes_per_shot"] - float(runtime.get("gpu_minutes", 0.0)) - shot_reserved
        amount = min(15.0, episode_available, shot_available, float(requested_by_shot.get(shot_id, 0.0)))
        if amount <= 0:
            raise BudgetExhausted(f"{shot_id} has no unreserved GPU minutes for IndexTTS")
        allocated[shot_id] = amount
        episode_available -= amount

    for shot_id, amount in allocated.items():
        runtime = _runtime(shot_by_id[shot_id])
        runtime["voice_attempt_count"] = int(runtime.get("voice_attempt_count", 0)) + 1
    jobs[job_id] = {
        "status": "RESERVED", "remote_status": "RESERVED",
        "revision": int(voice_job.get("revision", 1)),
        "reserved_gpu_minutes_by_shot": allocated,
        "reserved_gpu_minutes": sum(allocated.values()),
        "gpu_minutes_by_shot": {}, "updated_at": _now(),
    }
    _atomic_save(path, manifest)
    return {
        "remaining_gpu_minutes_per_episode": sum(allocated.values()),
        "remaining_gpu_minutes_by_shot": allocated,
    }


@_locked_manifest_update
def record_voice_remote(manifest_path: str | Path, voice_job: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    if not isinstance(job_id, str) or remote.get("job_id") not in {None, job_id}:
        raise ValueError("voice job id does not match its manifest request")
    status = str(remote.get("status", "")).upper()
    jobs = manifest.setdefault("voice_jobs", {})
    previous = jobs.get(job_id, {})
    previous_status = str(previous.get("status", "")).upper() if isinstance(previous, dict) else ""
    previous_remote_status = str(previous.get("remote_status", previous_status)).upper() if isinstance(previous, dict) else ""
    terminal_statuses = {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}
    if previous_status in terminal_statuses and status != previous_status:
        status = previous_status
    revision = int(voice_job.get("revision", previous.get("revision", 1) if isinstance(previous, dict) else 1))
    entry = dict(previous) if isinstance(previous, dict) else {}
    entry.update({"status": status, "remote_status": status, "revision": revision, "updated_at": _now(), "error": remote.get("error")})
    if status in {"FAILED", "ERROR"} and previous_status not in terminal_statuses:
        manifest["voice_job_revision"] = max(int(manifest.get("voice_job_revision", revision)), revision + 1)
    spent_by_shot = remote.get("gpu_minutes_by_shot", {})
    if isinstance(spent_by_shot, dict):
        for shot_id, amount in spent_by_shot.items():
            shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
            if shot is None or not isinstance(amount, (int, float)) or isinstance(amount, bool):
                continue
            runtime = _runtime(shot)
            usage = runtime.setdefault("voice_gpu_minutes_by_job", {})
            by_shot = float(amount)
            current = max(float(usage.get(job_id, 0.0)), by_shot)
            runtime["gpu_minutes"] = float(runtime["gpu_minutes"]) + current - float(usage.get(job_id, 0.0))
            usage[job_id] = current
            actual_by_shot = entry.setdefault("gpu_minutes_by_shot", {})
            actual_by_shot[shot_id] = current
            reserved = entry.setdefault("reserved_gpu_minutes_by_shot", {})
            reserved[shot_id] = max(0.0, float(reserved.get(shot_id, 0.0)) - max(0.0, current - float(entry.setdefault("accounted_gpu_minutes_by_shot", {}).get(shot_id, 0.0))))
            entry["accounted_gpu_minutes_by_shot"][shot_id] = current
    if status in {"SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
        entry["reserved_gpu_minutes"] = sum(float(value) for value in entry.get("reserved_gpu_minutes_by_shot", {}).values())
    elif status in terminal_statuses:
        entry["reserved_gpu_minutes"] = 0.0
        entry["reserved_gpu_minutes_by_shot"] = {}
    jobs[job_id] = entry
    shot_ids = {line.get("shot_id") for line in voice_job.get("lines", []) if isinstance(line.get("shot_id"), str)}
    for shot_id in shot_ids:
        shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
        if shot is None:
            continue
        runtime = _runtime(shot)
        if status in {"SUBMITTED", "QUEUED", "RUNNING"} and shot["status"] in {"PLANNED", "AUDIO_RENDERING", "NEEDS_RETRY"}:
            shot["status"] = "AUDIO_RENDERING"
        elif status == "COMPLETE" and shot["status"] == "AUDIO_RENDERING":
            shot["status"] = "AUDIO_QC"
        elif status in {"FAILED", "ERROR"} and shot["status"] == "AUDIO_RENDERING":
            limits = _limits(manifest)
            attempts = int(runtime.get("attempt_count", 0)) + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0))
            episode_used = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
            exhausted = (
                attempts >= limits["max_attempts_per_shot"]
                or float(runtime.get("gpu_minutes", 0.0)) >= limits["max_gpu_minutes_per_shot"]
                or episode_used >= limits["max_gpu_minutes_per_episode"]
            )
            shot["status"] = "NEEDS_MANUAL_REVIEW" if exhausted else "NEEDS_RETRY"
            if exhausted:
                runtime["last_error"] = remote.get("error") or "voice attempt or GPU-minute budget exhausted"
        elif status == "NEEDS_MANUAL_REVIEW":
            shot["status"] = "NEEDS_MANUAL_REVIEW"
            runtime["last_error"] = remote.get("error") or "voice worker paused at its GPU budget gate"
    _atomic_save(path, manifest)
    return budget_snapshot(manifest)


@_locked_manifest_update
def reserve_voice_design_attempt(manifest_path: str | Path, voice_job: dict[str, Any]) -> dict[str, float]:
    """Atomically reserve the episode-wide VoiceDesign GPU budget."""
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    character_id = voice_job.get("character_id")
    if not isinstance(job_id, str) or not isinstance(character_id, str) or voice_job.get("episode_id") != manifest.get("episode_id"):
        raise ValueError("VoiceDesign reservation must match episode and character")
    character = manifest.get("characters", {}).get(character_id)
    if not isinstance(character, dict):
        raise ValueError(f"unknown VoiceDesign character: {character_id}")
    revision = int(voice_job.get("revision", 1))
    if revision != int(character.get("voice_design_revision", 1)):
        raise ValueError("stale VoiceDesign revision")
    jobs = manifest.setdefault("voice_design_jobs", {})
    existing = jobs.get(job_id)
    if isinstance(existing, dict):
        if existing.get("character_id") != character_id or int(existing.get("revision", revision)) != revision:
            raise ValueError("VoiceDesign job id is already reserved for different work")
        status = str(existing.get("remote_status", existing.get("status", ""))).upper()
        if status in {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
            amount = float(existing.get("reserved_gpu_minutes", 0.0))
            if amount <= 0:
                raise RuntimeError("existing VoiceDesign reservation has no usable GPU budget")
            return {"remaining_gpu_minutes_per_episode": amount}
        raise RuntimeError(f"VoiceDesign job {job_id} is already terminal; create its next revision")

    budget_limit = float(manifest.get("voice_design_budget_minutes", 15.0))
    spent = sum(float(item.get("gpu_minutes", 0.0)) for item in jobs.values() if isinstance(item, dict))
    reserved = _voice_design_reservations(manifest)
    requested = float(voice_job.get("budget", {}).get("remaining_gpu_minutes_per_episode", 0.0))
    amount = min(requested, budget_limit - spent - reserved)
    if amount <= 0:
        raise BudgetExhausted("VoiceDesign GPU budget is exhausted or reserved by another active job")
    jobs[job_id] = {
        "status": "RESERVED", "remote_status": "RESERVED", "revision": revision,
        "character_id": character_id, "reserved_gpu_minutes": amount,
        "reservation_initial_gpu_minutes": amount, "gpu_minutes": 0.0, "updated_at": _now(),
    }
    _atomic_save(path, manifest)
    return {"remaining_gpu_minutes_per_episode": amount}


@_locked_manifest_update
def record_voice_design_remote(manifest_path: str | Path, voice_job: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = voice_job.get("job_id")
    if not isinstance(job_id, str) or remote.get("job_id") not in {None, job_id}:
        raise ValueError("VoiceDesign job id does not match its manifest request")
    jobs = manifest.setdefault("voice_design_jobs", {})
    entry = jobs.setdefault(job_id, {})
    status = str(remote.get("status", "")).upper()
    previous_status = str(entry.get("status", "")).upper()
    terminal_statuses = {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}
    if previous_status in terminal_statuses and status != previous_status:
        status = previous_status
    revision = int(voice_job.get("revision", entry.get("revision", 1)))
    current = max(float(entry.get("gpu_minutes", 0.0)), float(remote.get("gpu_minutes", 0.0) or 0.0))
    entry.update({"status": status, "remote_status": status, "revision": revision, "gpu_minutes": round(current, 3), "updated_at": _now(), "error": remote.get("error")})
    entry["character_id"] = voice_job.get("character_id")
    if status in {"SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
        entry["reserved_gpu_minutes"] = max(0.0, float(entry.get("reservation_initial_gpu_minutes", entry.get("reserved_gpu_minutes", 0.0))) - current)
    elif status in terminal_statuses:
        entry["reserved_gpu_minutes"] = 0.0
    spent = sum(float(item.get("gpu_minutes", 0.0)) for item in jobs.values() if isinstance(item, dict))
    limit = float(manifest.get("voice_design_budget_minutes", 15.0))
    if spent >= limit and status not in {"COMPLETE", "DONE"}:
        entry["status"] = "NEEDS_MANUAL_REVIEW"
    elif status in {"FAILED", "ERROR"} and previous_status not in terminal_statuses:
        character = manifest.get("characters", {}).get(str(voice_job.get("character_id")), {})
        if isinstance(character, dict):
            character["voice_design_revision"] = max(int(character.get("voice_design_revision", revision)), revision + 1)
    _atomic_save(path, manifest)
    return {"status": entry["status"], "gpu_minutes_used": round(spent, 3), "gpu_minutes_limit": limit}


@_locked_manifest_update
def record_postprocess_remote(manifest_path: str | Path, job: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Record a postprocess job without advancing director-owned shot states."""
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id = job.get("job_id")
    shot_id = job.get("shot_id")
    mode = job.get("mode")
    if not isinstance(job_id, str) or remote.get("job_id") not in {None, job_id}:
        raise ValueError("postprocess job id does not match its manifest request")
    if mode not in {"latentsync", "seedvr2"} or not isinstance(shot_id, str):
        raise ValueError("postprocess request must identify mode and shot")
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    status = str(remote.get("status", "")).upper()
    jobs = manifest.setdefault("postprocess_jobs", {})
    entry = jobs.get(job_id)
    first_seen = not isinstance(entry, dict)
    if first_seen:
        entry = {}
        jobs[job_id] = entry
    runtime = _runtime(shot)
    if first_seen:
        runtime["postprocess_attempt_count"] = int(runtime.get("postprocess_attempt_count", 0)) + 1
    previous_remote_status = str(entry.get("remote_status", "")).upper()
    previous_terminal = previous_remote_status in {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}
    if previous_terminal and status != previous_remote_status:
        status = previous_remote_status
    entry.update({
        "mode": mode, "shot_id": shot_id, "status": status, "remote_status": status,
        "revision": int(job.get("revision", 1)),
        "updated_at": _now(), "error": remote.get("error"),
        "gpu_minutes": max(float(entry.get("gpu_minutes", 0.0)), float(remote.get("gpu_minutes", 0.0) or 0.0)),
    })
    usage = runtime.setdefault("postprocess_gpu_minutes_by_job", {})
    previous = float(usage.get(job_id, 0.0))
    current = max(previous, entry["gpu_minutes"])
    runtime["gpu_minutes"] = float(runtime["gpu_minutes"]) + current - previous
    usage[job_id] = current
    limits = _limits(manifest)
    spent_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    terminal = status in {"COMPLETE", "DONE", "FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"}
    if terminal:
        entry["reserved_gpu_minutes"] = 0.0
        entry["terminal_at"] = entry.get("terminal_at") or _now()
    if terminal and not previous_terminal:
        revision_field = "lipsync_revision" if mode == "latentsync" else "upscale_revision"
        current_revision = int(shot.get(revision_field, 1))
        job_revision = int(job.get("revision", current_revision))
        if status in {"COMPLETE", "DONE"}:
            shot[revision_field] = max(current_revision, job_revision + 1)
        elif status in {"FAILED", "ERROR"}:
            attempt_count = int(runtime.get("attempt_count", 0)) + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0))
            budget_exhausted = (
                attempt_count >= limits["max_attempts_per_shot"]
                or float(runtime["gpu_minutes"]) >= limits["max_gpu_minutes_per_shot"]
                or spent_episode >= limits["max_gpu_minutes_per_episode"]
            )
            if budget_exhausted:
                entry["status"] = "NEEDS_MANUAL_REVIEW"
                entry["error"] = entry["error"] or "postprocess attempt or GPU-minute budget exhausted"
                shot["status"] = "NEEDS_MANUAL_REVIEW"
            else:
                shot[revision_field] = max(current_revision, job_revision + 1)
    if status == "NEEDS_MANUAL_REVIEW":
        entry["status"] = "NEEDS_MANUAL_REVIEW"
        entry["error"] = entry["error"] or "postprocess budget exhausted"
        shot["status"] = "NEEDS_MANUAL_REVIEW"
    runtime["updated_at"] = _now()
    _atomic_save(path, manifest)
    return {
        "status": entry["status"], "shot_gpu_minutes_used": round(float(runtime["gpu_minutes"]), 3),
        "episode_gpu_minutes_used": round(spent_episode, 3),
    }


@_locked_manifest_update
def reserve_postprocess_attempt(manifest_path: str | Path, job: dict[str, Any]) -> dict[str, float]:
    """Atomically reserve one postprocess attempt and its maximum GPU time."""
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    job_id, shot_id, mode = job.get("job_id"), job.get("shot_id"), job.get("mode")
    if not isinstance(job_id, str) or not isinstance(shot_id, str) or mode not in {"latentsync", "seedvr2"}:
        raise ValueError("postprocess reservation requires job_id, shot_id and supported mode")
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    if job.get("episode_id") != manifest.get("episode_id"):
        raise ValueError("postprocess job episode_id does not match its manifest")
    _verify_postprocess_manifest_contract(path.parent, manifest, shot, job)
    revision_field = "lipsync_revision" if mode == "latentsync" else "upscale_revision"
    revision = int(job.get("revision", 1))
    if revision != int(shot.get(revision_field, 1)):
        raise ValueError(f"stale postprocess revision: expected r{int(shot.get(revision_field, 1)):02d}")
    jobs = manifest.setdefault("postprocess_jobs", {})
    existing = jobs.get(job_id)
    if isinstance(existing, dict):
        if existing.get("mode") != mode or existing.get("shot_id") != shot_id or int(existing.get("revision", revision)) != revision:
            raise ValueError("postprocess job id is already reserved for different work")
        state = str(existing.get("remote_status", existing.get("status", ""))).upper()
        if state in {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}:
            amount = float(existing.get("reserved_gpu_minutes", 0.0))
            if amount <= 0:
                raise RuntimeError("existing postprocess reservation has no usable GPU budget")
            return {"remaining_gpu_minutes_per_shot": amount, "remaining_gpu_minutes_per_episode": amount}
        raise RuntimeError(f"postprocess job {job_id} is already terminal; create its next revision")

    runtime = _runtime(shot)
    limits = _limits(manifest)
    attempt_count = int(runtime.get("attempt_count", 0)) + int(runtime.get("postprocess_attempt_count", 0)) + int(runtime.get("voice_attempt_count", 0))
    if attempt_count >= limits["max_attempts_per_shot"]:
        shot["status"] = "NEEDS_MANUAL_REVIEW"
        runtime["last_error"] = "postprocess attempt limit reached"
        _atomic_save(path, manifest)
        raise BudgetExhausted("shot attempt limit reached; postprocess was not reserved")

    spent_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    reserved_episode = _postprocess_reservations(manifest) + _h3_reservations(manifest) + _voice_reservations(manifest)
    reserved_shot = _postprocess_reservations(manifest, shot_id) + _h3_reservations(manifest, shot_id) + _voice_reservations(manifest, shot_id)
    budget = job.get("budget", {})
    requested_shot = float(budget.get("remaining_gpu_minutes_per_shot", 0.0))
    requested_episode = float(budget.get("remaining_gpu_minutes_per_episode", 0.0))
    amount = min(
        15.0,
        limits["max_gpu_minutes_per_shot"] - float(runtime.get("gpu_minutes", 0.0)) - reserved_shot,
        limits["max_gpu_minutes_per_episode"] - spent_episode - reserved_episode,
        requested_shot,
        requested_episode,
    )
    if amount <= 0:
        actual_shot_remaining = limits["max_gpu_minutes_per_shot"] - float(runtime.get("gpu_minutes", 0.0))
        actual_episode_remaining = limits["max_gpu_minutes_per_episode"] - spent_episode
        if min(actual_shot_remaining, actual_episode_remaining) <= 0:
            shot["status"] = "NEEDS_MANUAL_REVIEW"
            runtime["last_error"] = "postprocess GPU-minute budget exhausted"
            _atomic_save(path, manifest)
            raise BudgetExhausted("postprocess GPU-minute budget exhausted; job was not reserved")
        raise RuntimeError("postprocess GPU budget is reserved by another active job")
    runtime["postprocess_attempt_count"] = int(runtime.get("postprocess_attempt_count", 0)) + 1
    jobs[job_id] = {
        "mode": mode, "shot_id": shot_id, "revision": revision,
        "status": "RESERVED", "remote_status": "RESERVED", "reserved_gpu_minutes": amount,
        "gpu_minutes": 0.0, "input_sha256": job.get("input_sha256", {}), "updated_at": _now(),
    }
    runtime["updated_at"] = _now()
    _atomic_save(path, manifest)
    return {"remaining_gpu_minutes_per_shot": amount, "remaining_gpu_minutes_per_episode": amount}


def _verify_postprocess_manifest_contract(
    episode_root: Path, manifest: dict[str, Any], shot: dict[str, Any], job: dict[str, Any],
) -> None:
    """Recheck human selection/edit-lock gates against canonical assets at submit time."""
    from .hashing import sha256_file

    mode = job.get("mode")
    if mode not in {"latentsync", "seedvr2"}:
        raise ValueError("unsupported postprocess mode")
    status = shot.get("status")
    if mode == "latentsync" and status != "NEEDS_LIPSYNC":
        raise ValueError("LatentSync submission requires current NEEDS_LIPSYNC status")
    if mode == "seedvr2" and status not in {"READY_FOR_EDIT", "UPSCALED"}:
        raise ValueError("SeedVR2 submission requires current edit-locked status")

    def canonical_asset(field: str, package_field: str) -> tuple[Path, str]:
        relative = shot.get(field)
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"canonical shot {field} is missing")
        raw_asset = episode_root / relative
        asset = raw_asset.resolve()
        if raw_asset.is_symlink() or not asset.is_relative_to(episode_root.resolve()) or not asset.is_file():
            raise ValueError(f"canonical shot {field} is missing or unsafe")
        digest = sha256_file(asset)
        expected = job.get("input_sha256", {}).get(package_field)
        if not isinstance(expected, str) or expected != digest:
            raise ValueError(f"postprocess {package_field} differs from the current canonical shot asset")
        return asset, digest

    _, video_sha = canonical_asset("selected_video", "assets/input.mp4")
    canonical_asset("edit_audio", "assets/edit_audio.wav")
    if mode == "latentsync":
        selection = shot.get("candidate_selection")
        if not isinstance(selection, dict) or selection.get("sha256") != video_sha:
            raise ValueError("LatentSync input no longer matches the human-selected candidate")
        return

    lock = manifest.get("edit_lock")
    if not isinstance(lock, dict) or not isinstance(lock.get("assets"), list):
        raise ValueError("SeedVR2 submission requires a current edit lock")
    for field in ("selected_video", "edit_audio"):
        entry = next((item for item in lock["assets"] if item.get("shot_id") == shot["id"] and item.get("field") == field), None)
        if not isinstance(entry, dict) or entry.get("path") != shot.get(field):
            raise ValueError(f"edit lock does not match current {shot['id']}.{field}")
        package_field = "assets/input.mp4" if field == "selected_video" else "assets/edit_audio.wav"
        if entry.get("sha256") != job.get("input_sha256", {}).get(package_field):
            raise ValueError(f"postprocess input no longer matches the edit lock for {field}")
    current_lock_sha = hashlib.sha256(json.dumps(lock, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    if job.get("edit_lock_sha256") != current_lock_sha:
        raise ValueError("SeedVR2 package edit lock is stale")
