from __future__ import annotations

import json
import hashlib
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import write_json
from .manifest import load_manifest
from .media import deterministic_qc, probe
from .package import sha256_file
from .state import _atomic_save, _locked_manifest_update, _verify_postprocess_manifest_contract

PROTOCOL = "ai-interview-postprocess-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")


def _episode_asset(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"{label} is missing or unsafe: {value}")
    return path


def _remaining_budget(manifest: dict[str, Any], shot: dict[str, Any]) -> dict[str, float]:
    policy = manifest["retry_policy"]
    used_episode = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    used_shot = float(shot.get("runtime", {}).get("gpu_minutes", 0.0))
    postprocess_jobs = manifest.get("postprocess_jobs", {})
    active_states = {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
    reserved_episode = sum(
        float(item.get("reserved_gpu_minutes", 0.0))
        for item in postprocess_jobs.values()
        if isinstance(item, dict) and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )
    reserved_episode += sum(
        float(item.get("runtime", {}).get("h3_reserved_gpu_minutes", 0.0))
        for item in manifest["shots"]
    )
    voice_jobs = manifest.get("voice_jobs", {})
    reserved_episode += sum(
        float(item.get("reserved_gpu_minutes", 0.0)) for item in voice_jobs.values()
        if isinstance(item, dict) and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )
    reserved_shot = sum(
        float(item.get("reserved_gpu_minutes", 0.0))
        for item in postprocess_jobs.values()
        if isinstance(item, dict) and item.get("shot_id") == shot["id"]
        and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )
    reserved_shot += float(shot.get("runtime", {}).get("h3_reserved_gpu_minutes", 0.0))
    reserved_shot += sum(
        float(item.get("reserved_gpu_minutes_by_shot", {}).get(shot["id"], 0.0))
        for item in voice_jobs.values()
        if isinstance(item, dict) and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )
    attempts = int(shot.get("runtime", {}).get("attempt_count", 0)) + int(
        shot.get("runtime", {}).get("postprocess_attempt_count", 0)
    ) + int(shot.get("runtime", {}).get("voice_attempt_count", 0))
    attempt_limit = int(policy["max_attempts_per_shot"])
    if attempts >= attempt_limit:
        raise RuntimeError("postprocess attempt budget exhausted; manual review is required")
    shot_limit = float(policy["max_gpu_minutes_per_shot"])
    episode_limit = float(policy["max_gpu_minutes_per_episode"])
    remaining = {
        "per_shot": max(0.0, min(15.0, shot_limit - used_shot - reserved_shot)),
        "per_episode": max(0.0, min(300.0, episode_limit - used_episode - reserved_episode)),
    }
    if min(remaining.values()) <= 0:
        raise RuntimeError("postprocess budget exhausted; job was not created")
    return remaining


def _edit_lock_entry(manifest: dict[str, Any], shot_id: str, field: str) -> dict[str, Any]:
    lock = manifest.get("edit_lock")
    if not isinstance(lock, dict) or not isinstance(lock.get("assets"), list):
        raise ValueError("SeedVR2 packaging requires an existing edit lock")
    entry = next((item for item in lock["assets"] if item.get("shot_id") == shot_id and item.get("field") == field), None)
    if not isinstance(entry, dict):
        raise ValueError(f"edit lock does not contain {shot_id}.{field}")
    return entry


def _package(
    manifest_path: str | Path,
    shot_id: str,
    mode: str,
    *,
    selected_by: str,
) -> Path:
    if not selected_by.strip():
        raise ValueError("selected_by is required for a postprocess job")
    if mode not in {"latentsync", "seedvr2"}:
        raise ValueError(f"unsupported postprocess mode: {mode}")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    if mode == "latentsync" and shot.get("status") != "NEEDS_LIPSYNC":
        raise ValueError(f"{shot_id} must be NEEDS_LIPSYNC before LatentSync packaging")
    if mode == "seedvr2":
        if shot.get("status") not in {"READY_FOR_EDIT", "UPSCALED"}:
            raise ValueError(f"{shot_id} must be edit-locked before SeedVR2 packaging")
        if not manifest.get("edit_lock"):
            raise ValueError("SeedVR2 packaging requires an existing edit lock")

    video = _episode_asset(root, shot.get("selected_video"), f"{shot_id}.selected_video")
    audio = _episode_asset(root, shot.get("edit_audio"), f"{shot_id}.edit_audio")
    video_sha = sha256_file(video)
    audio_sha = sha256_file(audio)
    candidate = shot.get("candidate_selection", {})
    if mode == "latentsync" and candidate.get("sha256") != video_sha:
        raise ValueError("selected video hash differs from its human candidate selection")
    if mode == "seedvr2":
        for field, asset, digest in (("selected_video", video, video_sha), ("edit_audio", audio, audio_sha)):
            locked = _edit_lock_entry(manifest, shot_id, field)
            if locked.get("path") != shot.get(field) or locked.get("sha256") != digest or sha256_file(asset) != locked.get("sha256"):
                raise ValueError(f"{shot_id}.{field} changed after edit lock")

    qc = deterministic_qc(video)
    if qc.get("status") != "PASS":
        raise ValueError(f"selected input video did not pass deterministic QC: {shot_id}")
    audio_info = probe(audio)
    audio_streams = [item for item in audio_info.get("streams", []) if item.get("codec_type") == "audio"]
    if len(audio_streams) != 1 or str(audio_streams[0].get("sample_rate")) != "48000":
        raise ValueError(f"{shot_id}.edit_audio must contain one 48 kHz audio stream")
    remaining = _remaining_budget(manifest, shot)

    revision_field = "lipsync_revision" if mode == "latentsync" else "upscale_revision"
    revision = int(shot.get(revision_field, 1))
    job_id = f"{manifest['episode_id']}_{shot_id}_{mode}_r{revision:02d}"
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("generated postprocess job_id is unsafe")
    input_video = "assets/input.mp4"
    input_audio = "assets/edit_audio.wav"
    files = {input_video: video, input_audio: audio}
    job = {
        "schema_version": PROTOCOL,
        "job_id": job_id,
        "episode_id": manifest["episode_id"],
        "shot_id": shot_id,
        "mode": mode,
        "revision": revision,
        "selected_by": selected_by.strip(),
        "inputs": {"video": input_video, "audio": input_audio},
        "input_sha256": {relative: sha256_file(path) for relative, path in files.items()},
        "timing": {
            "duration_sec": float(shot["edit_duration_sec"]),
            "fps": int(manifest["fps"]),
            "width": 1080,
            "height": 1920,
            "audio_sample_rate": 48000,
        },
        "budget": {
            "remaining_gpu_minutes_per_shot": remaining["per_shot"],
            "remaining_gpu_minutes_per_episode": remaining["per_episode"],
            "max_gpu_minutes_per_shot": 15.0,
        },
        "parameters": (
            {"inference_steps": 20, "guidance_scale": 1.5}
            if mode == "latentsync"
            else {"model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "resolution": 2160, "batch_size": 5}
        ),
    }
    if mode == "seedvr2":
        job["edit_lock_sha256"] = hashlib.sha256(
            json.dumps(manifest["edit_lock"], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    destination = root / "postprocess_jobs" / mode / job_id
    if destination.exists():
        existing = destination / "job.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")) == job:
            return destination
        raise FileExistsError(f"Preserve existing non-identical postprocess job: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for relative, source in files.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256_file(target) != job["input_sha256"][relative]:
                raise IOError(f"asset changed while packaging: {source}")
        write_json(job, stage / "job.json")
        stage.rename(destination)
    return destination


def package_latentsync(manifest_path: str | Path, shot_id: str, *, selected_by: str) -> Path:
    return _package(manifest_path, shot_id, "latentsync", selected_by=selected_by)


def package_seedvr2(manifest_path: str | Path, shot_id: str, *, selected_by: str) -> Path:
    return _package(manifest_path, shot_id, "seedvr2", selected_by=selected_by)


@_locked_manifest_update
def select_postprocess_result(
    manifest_path: str | Path, shot_id: str, result_dir: str | Path,
    *, selected_by: str, lip_grade: str | None = None,
) -> dict[str, Any]:
    """Record a human-approved, locally imported postprocess result."""
    if not selected_by.strip():
        raise ValueError("selected_by is required")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    raw_dir = Path(result_dir).absolute()
    imported = raw_dir.resolve()
    if raw_dir.is_symlink() or not imported.is_relative_to(root) or not imported.is_dir():
        raise FileNotFoundError("postprocess import must be a directory inside the episode")
    result_path = imported / "result.json"
    qc_path = imported / "local_qc.json"
    if any(path.is_symlink() or not path.is_file() for path in (result_path, qc_path)):
        raise FileNotFoundError("verified postprocess result and local QC are required")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    mode, job_id = result.get("mode"), result.get("job_id")
    if (mode not in {"latentsync", "seedvr2"} or not isinstance(job_id, str)
            or not SAFE_ID.fullmatch(job_id) or imported != root / "postprocess_results" / mode / job_id):
        raise ValueError("postprocess import location or identity is invalid")
    if any(result.get(key) != expected for key, expected in {
        "schema_version": PROTOCOL, "episode_id": manifest["episode_id"],
        "shot_id": shot_id, "status": "COMPLETE",
    }.items()):
        raise ValueError("postprocess result identity or status mismatch")
    if result.get("output") != "work/result.mp4":
        raise ValueError("postprocess output must be work/result.mp4")
    video = imported / "work" / "result.mp4"
    if video.is_symlink() or not video.is_file() or sha256_file(video) != result.get("sha256"):
        raise ValueError("postprocess result video SHA-256 mismatch")
    if json.loads(qc_path.read_text(encoding="utf-8")).get("status") != "PASS":
        raise ValueError("imported postprocess QC did not pass")
    if deterministic_qc(video).get("status") != "PASS":
        raise ValueError("postprocess result no longer passes deterministic QC")
    job_path = root / "postprocess_jobs" / mode / job_id / "job.json"
    if job_path.is_symlink() or not job_path.is_file():
        raise FileNotFoundError("matching local postprocess package is required")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if any(job.get(key) != result.get(key) for key in ("schema_version", "job_id", "episode_id", "shot_id", "mode")):
        raise ValueError("postprocess result does not match its local package")
    selection = {
        "path": video.relative_to(root).as_posix(), "sha256": result["sha256"],
        "source_job_id": job_id, "selected_by": selected_by.strip(),
        "selected_at": datetime.now(UTC).isoformat(), "qc_status": "PASS",
        "evidence": "owner_selected; not independent ground truth",
    }
    if mode == "latentsync":
        if lip_grade not in {"A", "B"}:
            raise ValueError("LatentSync result requires human lip grade A or B")
        if shot.get("status") == "READY_FOR_EDIT" and shot.get("candidate_selection", {}).get("source_job_id") == job_id:
            return shot["candidate_selection"]
        if shot.get("status") != "NEEDS_LIPSYNC":
            raise ValueError("LatentSync selection requires NEEDS_LIPSYNC")
        _verify_postprocess_manifest_contract(root, manifest, shot, job)
        shot["pre_lipsync_selection"] = shot["candidate_selection"]
        selection["lip_grade"] = lip_grade
        shot["selected_video"] = selection["path"]
        shot["candidate_selection"] = selection
        shot["status"] = "READY_FOR_EDIT"
    else:
        if lip_grade is not None:
            raise ValueError("lip_grade applies only to LatentSync")
        if shot.get("status") == "UPSCALED" and shot.get("upscale_selection", {}).get("source_job_id") == job_id:
            return shot["upscale_selection"]
        if shot.get("status") != "READY_FOR_EDIT":
            raise ValueError("SeedVR2 selection requires READY_FOR_EDIT and an edit lock")
        _verify_postprocess_manifest_contract(root, manifest, shot, job)
        if shot.get("upscale_selection"):
            raise FileExistsError("an upscale is already selected; create a new revision")
        shot["upscaled_video"] = selection["path"]
        shot["upscale_selection"] = selection
        shot["status"] = "UPSCALED"
    _atomic_save(manifest_path, manifest)
    return selection
