"""Validation and path safety for the remote H3 job contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

PROTOCOL = "ai-interview-h3-remote-v1"
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
SHOT_RE = re.compile(r"^S\d{3}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_job_id(value: Any) -> str:
    if not isinstance(value, str) or not JOB_RE.fullmatch(value):
        raise ValueError("job_id contains unsafe characters")
    return value


def _asset_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("assets/") or "\\" in value:
        raise ValueError(f"{label} must point inside assets/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ValueError(f"{label} must be a safe relative assets path")
    return value


def validate_job_directory(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("job directory must be a real directory")
    job_file = directory / "job.json"
    if job_file.is_symlink() or not job_file.is_file():
        raise ValueError("job.json is missing or unsafe")
    job = json.loads(job_file.read_text(encoding="utf-8"))
    if not isinstance(job, dict):
        raise ValueError("job.json must contain an object")
    if job.get("schema_version") != PROTOCOL:
        raise ValueError(f"schema_version must be {PROTOCOL}")
    safe_job_id(job.get("job_id"))
    if directory.name != job["job_id"]:
        raise ValueError("job directory name must equal job_id")
    if not isinstance(job.get("episode_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", job["episode_id"]):
        raise ValueError("episode_id is invalid")
    if not isinstance(job.get("shot_id"), str) or not SHOT_RE.fullmatch(job["shot_id"]):
        raise ValueError("shot_id must match S001")
    if job.get("mode") not in {"i2va", "fl2va", "ref2va"}:
        raise ValueError("mode must be i2va, fl2va, or ref2va")
    count = job.get("candidate_count")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 8:
        raise ValueError("candidate_count must be an integer from 1 to 8")
    budget = job.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("budget must declare remaining shot and episode GPU minutes")
    for field in ("remaining_gpu_minutes_per_shot", "remaining_gpu_minutes_per_episode"):
        amount = budget.get(field)
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"budget.{field} must be a non-negative number")
    duration = job.get("duration_sec")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 < duration <= 30:
        raise ValueError("duration_sec must be greater than 0 and at most 30")
    if job.get("aspect_ratio") not in {"9:16", "16:9", "1:1"}:
        raise ValueError("unsupported aspect_ratio")
    if not isinstance(job.get("prompt"), str) or not job["prompt"].strip():
        raise ValueError("prompt is required")
    if not isinstance(job.get("preserve", []), list) or not all(isinstance(x, str) for x in job.get("preserve", [])):
        raise ValueError("preserve must be a list of strings")
    if not isinstance(job.get("avoid", []), list) or not all(isinstance(x, str) for x in job.get("avoid", [])):
        raise ValueError("avoid must be a list of strings")
    audio = job.get("audio")
    if not isinstance(audio, dict) or audio.get("generate_native_audio") is not True or not isinstance(audio.get("intent"), str) or not audio["intent"].strip():
        raise ValueError("native audio and audio.intent are required")
    inputs = job.get("inputs")
    hashes = job.get("input_sha256")
    if not isinstance(inputs, dict) or not isinstance(hashes, dict):
        raise ValueError("inputs and input_sha256 must be objects")
    paths: list[str] = []
    for field in ("first_frame", "last_frame"):
        value = inputs.get(field)
        if value is not None:
            path = _asset_path(value, f"inputs.{field}")
            if Path(path).suffix.lower() not in IMAGE_EXTS:
                raise ValueError(f"inputs.{field} must be an image")
            paths.append(path)
    limits = {"reference_images": 9, "reference_videos": 3, "reference_audio": 3}
    extensions = {
        "reference_images": IMAGE_EXTS,
        "reference_videos": VIDEO_EXTS,
        "reference_audio": AUDIO_EXTS,
    }
    counts: dict[str, int] = {}
    for field, limit in limits.items():
        values = inputs.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f"inputs.{field} must be a list")
        if len(values) > limit:
            raise ValueError(f"inputs.{field} supports at most {limit} files")
        counts[field] = len(values)
        for index, value in enumerate(values):
            path = _asset_path(value, f"inputs.{field}[{index}]")
            if Path(path).suffix.lower() not in extensions[field]:
                raise ValueError(f"unsupported file format in inputs.{field}: {path}")
            paths.append(path)
    if sum(counts.values()) > 12:
        raise ValueError("Ref2VA supports at most 12 mixed reference files")
    if job["mode"] == "i2va" and not inputs.get("first_frame"):
        raise ValueError("i2va requires inputs.first_frame")
    if job["mode"] == "fl2va" and not (inputs.get("first_frame") and inputs.get("last_frame")):
        raise ValueError("fl2va requires first_frame and last_frame")
    if job["mode"] == "ref2va" and not (inputs.get("reference_images") or inputs.get("reference_videos")):
        raise ValueError("ref2va requires at least one reference image or video")
    if job["mode"] != "ref2va" and any(counts.values()):
        raise ValueError("reference media lists are only supported in ref2va mode")
    if set(paths) != set(hashes):
        raise ValueError("input_sha256 must exactly match the declared image assets")
    for relative, expected in hashes.items():
        rel = _asset_path(relative, "input_sha256 key")
        asset = directory / rel
        if asset.is_symlink() or not asset.is_file() or not asset.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"asset is missing or unsafe: {relative}")
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected) or sha256_file(asset) != expected:
            raise ValueError(f"asset SHA-256 mismatch: {relative}")
    if not isinstance(job.get("output"), dict) or job["output"].get("video") is not True or job["output"].get("native_audio") is not True:
        raise ValueError("job must request a video with native audio")
    return job
