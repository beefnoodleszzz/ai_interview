"""Qwen3-TTS VoiceDesign worker, isolated from ComfyUI and IndexTTS."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNTIME = Path(os.environ.get("AI_INTERVIEW_RUNTIME", "/root/autodl-tmp/ai_interview")).resolve()
QWEN_ROOT = Path(os.environ.get("AI_INTERVIEW_QWEN_TTS_ROOT", str(RUNTIME / "apps" / "Qwen3-TTS"))).resolve()
QWEN_PYTHON = os.environ.get("AI_INTERVIEW_QWEN_TTS_PYTHON", str(RUNTIME / "envs" / "qwen3-tts" / "bin" / "python"))
QWEN_MODEL = Path(os.environ.get("AI_INTERVIEW_QWEN_TTS_VOICEDESIGN_MODEL", str(RUNTIME / "models" / "voice" / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"))).resolve()
PROTOCOL = "ai-interview-voice-design-v1"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_job(job_dir: Path) -> dict[str, Any]:
    if job_dir.is_symlink() or not job_dir.is_dir() or not (job_dir / "job.json").is_file():
        raise ValueError("voice-design job must be a real directory containing job.json")
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if not isinstance(job, dict) or job.get("schema_version") != PROTOCOL or job.get("backend") != "Qwen3-TTS-VoiceDesign":
        raise ValueError("unsupported voice-design contract")
    if not isinstance(job.get("job_id"), str) or not ID_RE.fullmatch(job["job_id"]) or job_dir.name != job["job_id"]:
        raise ValueError("unsafe voice-design job_id")
    if not isinstance(job.get("episode_id"), str) or not ID_RE.fullmatch(job["episode_id"]):
        raise ValueError("unsafe voice-design episode_id")
    if not isinstance(job.get("character_id"), str) or not ID_RE.fullmatch(job["character_id"]):
        raise ValueError("unsafe character_id")
    if not isinstance(job.get("voice_id"), str) or not ID_RE.fullmatch(job["voice_id"]):
        raise ValueError("unsafe voice_id")
    if not 1 <= int(job.get("candidates", 0)) <= 6:
        raise ValueError("candidates must be 1-6")
    if len(str(job.get("description", "")).strip()) < 10 or len(str(job.get("sample_text", "")).strip()) < 50:
        raise ValueError("voice description and a 50+ character sample_text are required")
    if job.get("language") not in {"Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"}:
        raise ValueError("unsupported Qwen3-TTS language")
    budget = job.get("budget")
    if not isinstance(budget, dict) or not isinstance(budget.get("remaining_gpu_minutes_per_episode"), (int, float)) or budget["remaining_gpu_minutes_per_episode"] <= 0:
        raise ValueError("voice-design job has no remaining GPU budget")
    if not Path(QWEN_PYTHON).is_file() or not (QWEN_MODEL / "config.json").is_file():
        raise RuntimeError("isolated Qwen3-TTS VoiceDesign runtime or model is unavailable")
    return job


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed for VoiceDesign output")
    value = json.loads(result.stdout)
    audio = next((item for item in value.get("streams", []) if item.get("codec_type") == "audio"), None)
    if not audio:
        raise RuntimeError("VoiceDesign output has no audio stream")
    decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    if decoded.returncode:
        raise RuntimeError(decoded.stderr.strip() or "VoiceDesign audio failed full decode")
    return {"duration_sec": float(value.get("format", {}).get("duration", 0) or 0), "sample_rate": int(audio.get("sample_rate", 0)), "channels": int(audio.get("channels", 0))}


def _run(job_dir: Path) -> dict[str, Any]:
    job = validate_job(job_dir)
    job_id = job["job_id"]
    inbox = RUNTIME / "voice_design" / "jobs" / "inbox" / job_id
    running = RUNTIME / "voice_design" / "jobs" / "running" / job_id
    complete = RUNTIME / "voice_design" / "jobs" / "complete" / job_id
    failed = RUNTIME / "voice_design" / "jobs" / "failed" / job_id
    if job_dir.resolve() != running.resolve():
        raise ValueError("VoiceDesign worker runs only voice_design/jobs/running/<job_id>")
    if complete.exists() or failed.exists():
        raise FileExistsError("preserving existing completed or failed VoiceDesign job")
    result_dir = RUNTIME / "voice_design" / "jobs" / "work" / job_id
    state_file = running / "state.json"
    state: dict[str, Any] = {"job_id": job_id, "status": "RUNNING", "stage": "loading_model", "pid": os.getpid(), "started_at": _now()}
    _write(state_file, state)
    if result_dir.exists():
        raise FileExistsError(f"preserving existing VoiceDesign work directory: {result_dir}")
    result_dir.mkdir(parents=True)
    started = time.monotonic()
    candidates = []
    try:
        import importlib.util
        sys.path.insert(0, str(QWEN_ROOT))
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel

        model = Qwen3TTSModel.from_pretrained(
            str(QWEN_MODEL), device_map="cuda:0",
            dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
            attn_implementation="flash_attention_2" if importlib.util.find_spec("flash_attn") else "sdpa",
        )
        for index in range(1, int(job["candidates"]) + 1):
            elapsed_minutes = (time.monotonic() - started) / 60
            if elapsed_minutes >= float(job["budget"]["remaining_gpu_minutes_per_episode"]):
                state.update({"status": "NEEDS_MANUAL_REVIEW", "stage": "budget", "gpu_minutes": round(elapsed_minutes, 3), "updated_at": _now()})
                _write(state_file, state)
                return {"schema_version": PROTOCOL, "job_id": job_id, "status": "NEEDS_MANUAL_REVIEW", "candidates": candidates}
            name = f"{job['voice_id']}_candidate_{index:02d}.wav"
            output = result_dir / name
            if output.exists():
                raise FileExistsError(f"preserving existing VoiceDesign candidate: {output}")
            state.update({"stage": "voice_design", "candidate_index": index, "updated_at": _now()})
            _write(state_file, state)
            before = time.monotonic()
            wavs, sample_rate = model.generate_voice_design(
                text=job["sample_text"], language=job["language"], instruct=job["description"],
            )
            sf.write(str(output), wavs[0], sample_rate, subtype="PCM_16")
            seconds = max(0.0, time.monotonic() - before)
            media = _probe(output)
            if not 10 <= media["duration_sec"] <= 30:
                raise ValueError(f"candidate {index} duration must be 10-30 seconds, got {media['duration_sec']:.3f}")
            metadata = {
                "schema_version": PROTOCOL, "job_id": job_id, "episode_id": job["episode_id"],
                "character_id": job["character_id"], "voice_id": job["voice_id"], "revision": job["revision"],
                "candidate_id": f"{job_id}_candidate_{index:02d}", "filename": name,
                "sha256": _sha(output), "description": job["description"], "sample_text": job["sample_text"],
                "generation_seconds": round(seconds, 3), **media,
            }
            metadata_name = output.with_suffix(".json").name
            _write(result_dir / metadata_name, metadata)
            candidates.append({"candidate_id": metadata["candidate_id"], "filename": name, "metadata_file": metadata_name, "sha256": metadata["sha256"], "duration_sec": metadata["duration_sec"]})
            state.update({"candidates": candidates, "gpu_minutes": round((time.monotonic() - started) / 60, 3), "updated_at": _now()})
            _write(state_file, state)
        result = {"schema_version": PROTOCOL, "job_id": job_id, "episode_id": job["episode_id"], "status": "COMPLETE", "gpu_minutes": round((time.monotonic() - started) / 60, 3), "completed_at": _now(), "candidates": candidates}
        _write(result_dir / "result.json", result)
        state.update({"status": "COMPLETE", "gpu_minutes": result["gpu_minutes"], "updated_at": _now()})
        _write(state_file, state)
        complete.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result_dir), str(complete))
        shutil.rmtree(running)
        return result
    except Exception as exc:
        state.update({"status": "FAILED", "gpu_minutes": round((time.monotonic() - started) / 60, 3), "error": str(exc), "failure_class": "voice_design_error", "updated_at": _now()})
        _write(state_file, state)
        failed.parent.mkdir(parents=True, exist_ok=True)
        if failed.exists():
            raise FileExistsError("preserving existing failed VoiceDesign job") from exc
        shutil.move(str(running), str(failed))
        raise


def _status(job_id: str | None = None, episode_id: str | None = None) -> dict[str, Any]:
    jobs = []
    base = RUNTIME / "voice_design" / "jobs"
    for state_name in ("inbox", "running", "complete", "failed"):
        directory = base / state_name
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or not ID_RE.fullmatch(child.name) or (job_id and child.name != job_id):
                continue
            request = json.loads((child / "job.json").read_text(encoding="utf-8")) if (child / "job.json").is_file() else {}
            state = json.loads((child / "state.json").read_text(encoding="utf-8")) if (child / "state.json").is_file() else {}
            result = json.loads((child / "result.json").read_text(encoding="utf-8")) if (child / "result.json").is_file() else {}
            if episode_id and request.get("episode_id", result.get("episode_id")) != episode_id:
                continue
            jobs.append({"job_id": child.name, "episode_id": request.get("episode_id", result.get("episode_id")), "status": state.get("status") or {"inbox": "QUEUED", "running": "RUNNING", "complete": "COMPLETE", "failed": "FAILED"}[state_name], "gpu_minutes": state.get("gpu_minutes", result.get("gpu_minutes", 0.0)), "stage": state.get("stage"), "error": state.get("error")})
    return {"schema_version": PROTOCOL, "jobs": jobs}


def _submit(job_dir: Path) -> dict[str, Any]:
    job = validate_job(job_dir)
    job_id = job["job_id"]
    inbox = RUNTIME / "voice_design" / "jobs" / "inbox" / job_id
    running = RUNTIME / "voice_design" / "jobs" / "running" / job_id
    complete = RUNTIME / "voice_design" / "jobs" / "complete" / job_id
    failed = RUNTIME / "voice_design" / "jobs" / "failed" / job_id
    if complete.exists() or running.exists() or failed.exists():
        match = next(item for item in _status(job_id)["jobs"] if item["job_id"] == job_id)
        return {"schema_version": PROTOCOL, **match, "existing": True}
    if job_dir.resolve() != inbox.resolve():
        raise ValueError("submit path must be voice_design/jobs/inbox/<job_id>")
    running.parent.mkdir(parents=True, exist_ok=True)
    inbox.rename(running)
    _write(running / "state.json", {"job_id": job_id, "status": "QUEUED", "submitted_at": _now()})
    log_dir = RUNTIME / "voice_design" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{job_id}.log").open("ab") as log:
        process = subprocess.Popen([QWEN_PYTHON, "voice_design_worker.py", "run", "--job-dir", str(running)], cwd=Path(__file__).resolve().parent, env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return {"schema_version": PROTOCOL, "job_id": job_id, "status": "SUBMITTED", "pid": process.pid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit"); submit.add_argument("--job-dir", required=True)
    status = commands.add_parser("status"); status.add_argument("--job-id"); status.add_argument("--episode-id")
    run = commands.add_parser("run"); run.add_argument("--job-dir", required=True)
    for command in (submit, status, run): command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "submit": result = _submit(Path(args.job_dir).resolve())
        elif args.command == "status": result = _status(args.job_id, args.episode_id)
        else:
            with (RUNTIME / "jobs" / ".gpu.lock").open("w", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                result = _run(Path(args.job_dir).resolve())
        print(json.dumps(result, ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"schema_version": PROTOCOL, "status": "FAILED", "error": str(exc)}, ensure_ascii=False)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
