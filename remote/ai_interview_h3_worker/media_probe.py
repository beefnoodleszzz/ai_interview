"""Machine-level media checks; no aesthetic scoring."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def tool_version(name: str) -> str | None:
    executable = shutil.which(name)
    if not executable:
        return None
    result = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=10, check=False)
    first = (result.stdout or result.stderr).splitlines()
    return first[0].strip() if first else None


def probe(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is not installed")
    result = subprocess.run(
        [executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-1200:]}")
    return json.loads(result.stdout)


def decode_check(path: Path) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is not installed")
    result = subprocess.run(
        [executable, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ffmpeg decode check failed: {result.stderr[-1200:]}")


def summarize(path: Path) -> dict[str, Any]:
    data = probe(path)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video is None or audio is None:
        raise RuntimeError("candidate must contain video and native audio streams")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = video.get("width"), video.get("height")
    if duration <= 0 or not isinstance(width, int) or not isinstance(height, int):
        raise RuntimeError("candidate has invalid duration or dimensions")
    decode_check(path)
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": video.get("avg_frame_rate") or video.get("r_frame_rate"),
        "has_audio": True,
        "audio_codec": audio.get("codec_name"),
        "audio_duration": float(audio.get("duration") or duration),
        "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        "channels": audio.get("channels"),
        "frame_count": int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None,
    }
