from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .manifest import load_manifest
from .hashing import sha256_file
from .state import _atomic_save


def probe(path: str | Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(Path(path)),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed: {path}")
    return json.loads(result.stdout)


def duration(path: str | Path) -> float:
    value = probe(path)
    seconds = float(value.get("format", {}).get("duration", 0) or 0)
    if seconds <= 0:
        raise ValueError(f"Media has no positive duration: {path}")
    return seconds


def build_roughcut_command(
    episode_root: Path, manifest: Mapping[str, Any], output: Path
) -> list[str]:
    episode_root = episode_root.resolve()
    locked_timing = manifest.get("edit_lock", {}).get("timing") if isinstance(manifest.get("edit_lock"), dict) else None
    if locked_timing is not None and locked_timing != _edit_timing(manifest):
        raise ValueError("shot edit timing differs from the edit lock")
    inputs: list[str] = []
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, shot in enumerate(manifest["shots"]):
        video_value = shot.get("upscaled_video") or shot.get("selected_video")
        audio_value = shot.get("edit_audio")
        if not isinstance(video_value, str) or not isinstance(audio_value, str):
            raise ValueError(f"{shot['id']} needs selected_video and a room-tone edit_audio mix")
        video = (episode_root / video_value).resolve()
        audio = (episode_root / audio_value).resolve()
        if not video.is_relative_to(episode_root) or not audio.is_relative_to(episode_root):
            raise ValueError(f"{shot['id']} media must stay inside the episode")
        if not video.is_file() or not audio.is_file():
            raise FileNotFoundError(f"{shot['id']} selected media is missing")
        if shot.get("upscaled_video"):
            selection = shot.get("upscale_selection", {})
            if selection.get("path") != video_value or selection.get("sha256") != sha256_file(video):
                raise ValueError(f"{shot['id']} upscaled video differs from its human selection")
            lock = manifest.get("edit_lock", {}).get("assets", [])
            for field in ("selected_video", "edit_audio"):
                entry = next((item for item in lock if item.get("shot_id") == shot["id"] and item.get("field") == field), None)
                source = (episode_root / shot[field]).resolve()
                if not entry or entry.get("path") != shot[field] or not source.is_file() or entry.get("sha256") != sha256_file(source):
                    raise ValueError(f"{shot['id']}.{field} differs from the edit lock")
        inputs += ["-i", str(video), "-i", str(audio)]
        seconds = float(shot["edit_duration_sec"])
        edit_in = float(shot.get("edit_in_sec", 0))
        if not math.isfinite(edit_in) or edit_in < 0:
            raise ValueError(f"{shot['id']} edit_in_sec must be a nonnegative finite number")
        tail_hold = max(0.0, edit_in + seconds - duration(video)) if edit_in else 0.0
        if tail_hold > 0.95:
            raise ValueError(f"{shot['id']} would need more than 0.95 seconds of held video")
        v_index, a_index = index * 2, index * 2 + 1
        tail_filter = f",tpad=stop_mode=clone:stop_duration={tail_hold + 1 / manifest['fps']:.6f}" if tail_hold else ""
        filters.append(
            f"[{v_index}:v]trim=start={edit_in}:duration={seconds},setpts=PTS-STARTPTS,fps={manifest['fps']},"
            f"{tail_filter.lstrip(',') + ',' if tail_filter else ''}"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
            f",trim=duration={seconds},setpts=PTS-STARTPTS"
            f"[v{index}]"
        )
        filters.append(
            f"[{a_index}:a]aresample=48000,asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={seconds},atrim=duration={seconds},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filters.append("".join(concat_inputs) + f"concat=n={len(concat_inputs)}:v=1:a=1[concat_v][a];[concat_v]fps={manifest['fps']}[v]")
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-n", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ]


def local_tool_status() -> dict[str, Any]:
    return {
        name: {"status": "PASS" if shutil.which(name) else "FAIL", "path": shutil.which(name)}
        for name in ("ffmpeg", "ffprobe", "ssh", "scp")
    }


def deterministic_qc(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    media = probe(path)
    streams = media.get("streams", [])
    kinds = {item.get("codec_type") for item in streams}
    checks: list[dict[str, Any]] = [
        {"name": "video_stream", "status": "PASS" if "video" in kinds else "FAIL"},
        {"name": "audio_stream", "status": "PASS" if "audio" in kinds else "FAIL"},
    ]
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    checks.append({
        "name": "full_decode", "status": "PASS" if decode.returncode == 0 else "FAIL",
        "detail": decode.stderr.strip()[-1000:],
    })
    analysis = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-vf", "blackdetect=d=0.2:pix_th=0.10,freezedetect=n=0.003:d=1",
            "-af", "silencedetect=n=-50dB:d=0.5", "-f", "null", "-",
        ], capture_output=True, text=True, check=False,
    )
    events = {
        "black": re.findall(r"black_start:[^\r\n]+", analysis.stderr),
        "freeze": re.findall(r"freeze_(?:start|end|duration):[^\r\n]+", analysis.stderr),
        "silence": re.findall(r"silence_(?:start|end|duration):[^\r\n]+", analysis.stderr),
    }
    checks.append({
        "name": "signal_analysis", "status": "PASS" if analysis.returncode == 0 else "FAIL",
        "events": events,
    })
    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "path": str(path), "probe": media, "checks": checks,
        "note": "Detected black/freeze/silence events require shot-aware review; they are not automatic rejection.",
    }


def final_master_qc(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.15,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    sample_rate: int = 48000,
) -> dict[str, Any]:
    path = Path(path).resolve()
    media = probe(path)
    streams = media.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    actual_duration = float(media.get("format", {}).get("duration", 0) or 0)
    try:
        actual_fps = float(Fraction(str(video.get("avg_frame_rate", "0/1"))))
    except (ValueError, ZeroDivisionError):
        actual_fps = 0.0
    checks: list[dict[str, Any]] = [
        {"name": "video_stream", "status": "PASS" if video else "FAIL"},
        {"name": "audio_stream", "status": "PASS" if audio else "FAIL"},
        {"name": "resolution", "status": "PASS" if (video.get("width"), video.get("height")) == (width, height) else "FAIL", "actual": [video.get("width"), video.get("height")], "expected": [width, height]},
        {"name": "fps", "status": "PASS" if abs(actual_fps - fps) < 0.02 else "FAIL", "actual": actual_fps, "expected": fps},
        {"name": "audio_sample_rate", "status": "PASS" if str(audio.get("sample_rate")) == str(sample_rate) else "FAIL", "actual": audio.get("sample_rate"), "expected": sample_rate},
    ]
    if expected_duration is not None:
        checks.append({"name": "duration", "status": "PASS" if abs(actual_duration - expected_duration) <= duration_tolerance else "FAIL", "actual": actual_duration, "expected": expected_duration, "tolerance": duration_tolerance})
    decode = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    checks.append({"name": "full_decode", "status": "PASS" if decode.returncode == 0 else "FAIL", "detail": decode.stderr.strip()[-1000:]})
    analysis = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-vf", "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=0.003:d=1", "-af", "ebur128=peak=true,silencedetect=n=-50dB:d=0.5", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    log = analysis.stderr
    black = re.findall(r"black_start:[^\r\n]+", log)
    freeze = re.findall(r"freeze_start:[^\r\n]+", log)
    silence = re.findall(r"silence_(?:start|end|duration):[^\r\n]+", log)
    integrated = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?) LUFS", log)
    checks.extend([
        {"name": "black_frames", "status": "PASS" if not black else "FAIL", "events": black},
        {"name": "freeze_frames", "status": "PASS" if not freeze else "FAIL", "events": freeze},
        {"name": "silence_review", "status": "PASS" if analysis.returncode == 0 else "FAIL", "events": silence, "detail": "Intentional pauses and room tone need director review."},
        {"name": "integrated_loudness", "status": "PASS" if integrated else "FAIL", "lufs": float(integrated[-1]) if integrated else None, "detail": "Measured for review; no project-wide numeric target is defined."},
    ])
    return {"status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL", "path": str(path), "duration_sec": actual_duration, "fps": actual_fps, "probe": media, "checks": checks}


def select_video_candidate(
    manifest_path: str | Path,
    shot_id: str,
    metadata_path: str | Path,
    selected_by: str,
    lip_grade: str,
) -> dict[str, Any]:
    if not selected_by.strip():
        raise ValueError("selected_by is required to record a human candidate selection")
    if lip_grade not in {"A", "B", "C", "D"}:
        raise ValueError("lip_grade must be A, B, C, or D")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    shot = next((item for item in manifest["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"Unknown shot id: {shot_id}")
    metadata_path = Path(metadata_path).resolve()
    if not metadata_path.is_relative_to(root) or not metadata_path.is_file():
        raise FileNotFoundError("candidate metadata must be inside the episode")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    active_job_id = shot.get("runtime", {}).get("job_id")
    candidate_id = metadata.get("candidate_id")
    if metadata.get("shot_id") is None and metadata.get("job_id") is None:
        # Candidates produced before explicit identity fields were added still
        # carry the immutable job id in their candidate id.
        valid_identity = isinstance(active_job_id, str) and isinstance(candidate_id, str) and re.fullmatch(
            re.escape(active_job_id) + r"_candidate_[0-9]{2}", candidate_id
        ) is not None
    else:
        valid_identity = metadata.get("shot_id") == shot_id and metadata.get("job_id") == active_job_id
    if not valid_identity:
        raise ValueError("candidate metadata does not match the selected shot's active job")
    filename = metadata.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("candidate metadata must include a safe video filename")
    video = metadata_path.parent / filename
    if not video.is_file() or video.is_symlink() or sha256_file(video) != metadata.get("sha256"):
        raise ValueError("candidate video is missing or its SHA-256 does not match")
    qc = deterministic_qc(video)
    if qc["status"] != "PASS":
        raise ValueError("candidate did not pass deterministic media QC")
    shot["selected_video"] = video.relative_to(root).as_posix()
    shot["candidate_selection"] = {
        "candidate_id": metadata.get("candidate_id"),
        "path": shot["selected_video"],
        "sha256": metadata["sha256"],
        "selected_by": selected_by.strip(),
        "selected_at": datetime.now(UTC).isoformat(),
        "lip_grade": lip_grade,
        "evidence": "director_selected; visual review and deterministic QC",
        "qc_status": qc["status"],
    }
    if lip_grade in {"A", "B"}:
        shot["status"] = "READY_FOR_EDIT"
    elif lip_grade == "C":
        shot["status"] = "NEEDS_LIPSYNC"
    else:
        shot["status"] = "NEEDS_RETRY"
    _atomic_save(manifest_path, manifest)
    return shot["candidate_selection"]


def lock_edit(manifest_path: str | Path, selected_by: str) -> dict[str, Any]:
    if not selected_by.strip():
        raise ValueError("selected_by is required for edit lock")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    entries = []
    for shot in manifest["shots"]:
        if shot.get("status") != "READY_FOR_EDIT":
            raise ValueError(f"{shot['id']} must be READY_FOR_EDIT before edit lock (got {shot.get('status')})")
        fields = ["selected_video", "edit_audio"]
        if shot.get("beat_ids"):
            fields.append("master_audio")
        for field in fields:
            value = shot.get(field)
            if not isinstance(value, str):
                raise ValueError(f"{shot['id']} is missing {field}")
            asset = (root / value).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                raise FileNotFoundError(f"{shot['id']} {field} is missing or unsafe: {value}")
            entries.append({"shot_id": shot["id"], "field": field, "path": value, "sha256": sha256_file(asset)})
    lock = {
        "locked_at": datetime.now(UTC).isoformat(),
        "locked_by": selected_by.strip(),
        "assets": entries,
        "timing": _edit_timing(manifest),
        "evidence": "director_selected; visual review and deterministic QC",
    }
    existing = manifest.get("edit_lock")
    if existing:
        if existing.get("assets") == entries and existing.get("timing") == lock["timing"] and existing.get("locked_by") == selected_by.strip():
            return existing
        raise FileExistsError("edit lock already exists; create a new revision to change selected media")
    manifest["edit_lock"] = lock
    _atomic_save(manifest_path, manifest)
    return lock


def _edit_timing(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"shot_id": shot["id"], "edit_in_sec": float(shot.get("edit_in_sec", 0)),
         "edit_duration_sec": float(shot["edit_duration_sec"])}
        for shot in manifest["shots"]
    ]
