"""IndexTTS-2.5 voice job worker for the isolated AutoDL voice runtime."""

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
from pathlib import Path, PurePosixPath
from typing import Any

RUNTIME = Path(os.environ.get("AI_INTERVIEW_RUNTIME", "/root/autodl-tmp/ai_interview")).resolve()
INDEX_ROOT = Path(os.environ.get("AI_INTERVIEW_INDEX_TTS_ROOT", str(RUNTIME / "apps" / "index-tts"))).resolve()
INDEX_MODEL = Path(os.environ.get("AI_INTERVIEW_INDEX_TTS_MODEL_DIR", str(RUNTIME / "models" / "voice" / "IndexTTS-2.5"))).resolve()
INDEX_PYTHON = os.environ.get("AI_INTERVIEW_INDEX_TTS_PYTHON", str(INDEX_ROOT / ".venv" / "bin" / "python"))
ASR_PYTHON = os.environ.get("AI_INTERVIEW_ASR_PYTHON", str(RUNTIME / "envs" / "asr" / "bin" / "python"))
ASR_MODEL = Path(os.environ.get("AI_INTERVIEW_ASR_MODEL", str(RUNTIME / "models" / "asr" / "whisper-tiny.pt"))).resolve()
PROTOCOL = "ai-interview-voice-v1"
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _queue_lock():
    path = RUNTIME / "voice" / "jobs" / ".queue.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("a+", encoding="utf-8")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    return lock


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


def _safe_asset(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("assets/") or "\\" in value:
        raise ValueError("voice reference must point inside assets/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ValueError("voice reference path is unsafe")
    return value


def validate_job(job_dir: Path) -> dict[str, Any]:
    if job_dir.is_symlink() or not job_dir.is_dir() or not (job_dir / "job.json").is_file():
        raise ValueError("voice package must be a real directory containing job.json")
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if not isinstance(job, dict) or job.get("schema_version") != PROTOCOL:
        raise ValueError(f"schema_version must be {PROTOCOL}")
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id) or job_dir.name != job_id:
        raise ValueError("voice job_id is unsafe or does not match its directory")
    if job.get("backend") != "IndexTTS-2.5":
        raise ValueError("voice worker only accepts IndexTTS-2.5 jobs")
    count = job.get("candidates_per_line")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 8:
        raise ValueError("candidates_per_line must be 1-8")
    lines = job.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("voice job requires lines")
    hashes = job.get("input_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("input_sha256 must be an object")
    declared = set()
    ids = set()
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("id"), str) or line["id"] in ids:
            raise ValueError("voice line ids must be unique strings")
        ids.add(line["id"])
        if not isinstance(line.get("text"), str) or not line["text"].strip():
            raise ValueError(f"voice line {line['id']} has no text")
        if line.get("language") not in {"ZH", "EN", "JA", "ES", "AR"}:
            raise ValueError(f"voice line {line['id']} has an unsupported language")
        reference = _safe_asset(line.get("voice_reference"))
        declared.add(reference)
        if not isinstance(line.get("voice_reference_sha256"), str) or not HASH_RE.fullmatch(line["voice_reference_sha256"]):
            raise ValueError(f"voice line {line['id']} has an invalid reference hash")
        if float(line.get("pause_after", 0)) < 0:
            raise ValueError(f"voice line {line['id']} pause_after must be non-negative")
    if declared != set(hashes):
        raise ValueError("input_sha256 must exactly match the declared voice references")
    for relative, expected in hashes.items():
        _safe_asset(relative)
        asset = job_dir / relative
        if asset.is_symlink() or not asset.is_file() or not HASH_RE.fullmatch(str(expected)) or _sha(asset) != expected:
            raise ValueError(f"voice reference missing or hash mismatch: {relative}")
    for line in lines:
        if hashes.get(line["voice_reference"]) != line["voice_reference_sha256"]:
            raise ValueError(f"voice line {line['id']} reference hash does not match input_sha256")
    budget = job.get("budget")
    if not isinstance(budget, dict) or not isinstance(budget.get("remaining_gpu_minutes_per_episode"), (int, float)) or budget["remaining_gpu_minutes_per_episode"] < 0:
        raise ValueError("voice job must include remaining episode GPU minutes")
    if not Path(INDEX_PYTHON).is_file() or not (INDEX_MODEL / "config.yaml").is_file():
        raise RuntimeError("IndexTTS-2.5 runtime or model is unavailable")
    if not Path(ASR_PYTHON).is_file() or not ASR_MODEL.is_file():
        raise RuntimeError("isolated ASR interpreter or Whisper model is unavailable")
    return job


def _probe_audio(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed for generated WAV")
    value = json.loads(result.stdout)
    audio = next((item for item in value.get("streams", []) if item.get("codec_type") == "audio"), None)
    if not audio:
        raise RuntimeError("IndexTTS output has no audio stream")
    decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    if decoded.returncode:
        raise RuntimeError(decoded.stderr.strip() or "generated WAV decode failed")
    return {"duration_sec": float(value.get("format", {}).get("duration", 0) or 0), "sample_rate": int(audio.get("sample_rate", 0)), "channels": int(audio.get("channels", 0))}


def _transcribe(path: Path, language: str) -> str:
    adapter = Path(__file__).with_name("voice_asr.py")
    language_code = {"ZH": "zh", "EN": "en", "JA": "ja", "ES": "es", "AR": "ar"}[language]
    completed = subprocess.run([ASR_PYTHON, str(adapter), str(path), "--model", str(ASR_MODEL), "--language", language_code], capture_output=True, text=True, timeout=900, check=False)
    if completed.returncode:
        raise RuntimeError("isolated ASR failed: " + completed.stderr.strip()[-1000:])
    value = json.loads(completed.stdout)
    return str(value.get("text", "")).strip()


def _run(job_dir: Path) -> dict[str, Any]:
    job = validate_job(job_dir)
    job_id = job["job_id"]
    running = RUNTIME / "voice" / "jobs" / "running" / job_id
    complete = RUNTIME / "voice" / "jobs" / "complete" / job_id
    failed = RUNTIME / "voice" / "jobs" / "failed" / job_id
    if job_dir.resolve() != running.resolve():
        raise ValueError("voice worker runs only jobs/voice/running/<job_id>")
    if complete.exists() or failed.exists():
        raise FileExistsError("preserving existing completed or failed voice job")
    result_dir = RUNTIME / "voice" / "jobs" / "work" / job_id
    state_file = running / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    state.update({"job_id": job_id, "status": "RUNNING", "stage": "loading_model", "pid": os.getpid(), "updated_at": _now()})
    state.setdefault("gpu_seconds_by_shot", {})
    state.setdefault("candidates", [])
    _write(state_file, state)
    try:
        if result_dir.exists():
            raise FileExistsError(f"preserving existing incomplete voice work directory: {result_dir}")
        result_dir.mkdir(parents=True)
        sys.path.insert(0, str(INDEX_ROOT))
        import torch
        from indextts.infer_v2_5 import IndexTTS2

        tts = IndexTTS2(
            model_dir=str(INDEX_MODEL), cfg_path=str(INDEX_MODEL / "config.yaml"),
            use_bf16=bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported()), use_qwen_emo=True,
        )
        started = time.monotonic()
        candidates = []
        for line in job["lines"]:
            shot_id = line.get("shot_id")
            line_budget = float(job["budget"].get("remaining_gpu_minutes_by_shot", {}).get(shot_id, job["budget"]["remaining_gpu_minutes_per_episode"]))
            episode_budget = float(job["budget"]["remaining_gpu_minutes_per_episode"])
            used_episode = (time.monotonic() - started) / 60
            used_shot = float(state["gpu_seconds_by_shot"].get(shot_id, 0.0)) / 60
            if used_episode >= episode_budget or used_shot >= line_budget:
                state.update({"status": "NEEDS_MANUAL_REVIEW", "stage": "budget", "gpu_minutes": round(used_episode, 3), "updated_at": _now()})
                _write(state_file, state)
                return _manual_review_result(job_id, candidates, state, used_episode)
            reference = running / line["voice_reference"]
            for index in range(1, job["candidates_per_line"] + 1):
                filename = f"{line['id']}_candidate_{index:02d}.wav"
                output = result_dir / filename
                if output.exists():
                    raise FileExistsError(f"preserving existing voice candidate: {output}")
                state.update({"stage": "tts", "line_id": line["id"], "candidate_index": index, "updated_at": _now()})
                _write(state_file, state)
                start = time.monotonic()
                emotion = str(line.get("emotion", "neutral"))
                tts.infer(
                    spk_audio_prompt=str(reference), text=line["text"], output_path=str(output), lang=line["language"],
                    use_emo_text=emotion.casefold() not in {"", "neutral", "none"},
                    emo_text=None if emotion.casefold() in {"", "neutral", "none"} else emotion,
                    use_random=index > 1, verbose=False,
                )
                elapsed = max(0.0, time.monotonic() - start)
                state["gpu_seconds_by_shot"][shot_id] = float(state["gpu_seconds_by_shot"].get(shot_id, 0.0)) + elapsed
                media = _probe_audio(output)
                asr_text = _transcribe(output, line["language"])
                metadata = {
                    "schema_version": PROTOCOL, "job_id": job_id, "line_id": line["id"],
                    "candidate_id": f"{job_id}_{line['id']}_candidate_{index:02d}",
                    "filename": filename, "sha256": _sha(output), "text": line["text"],
                    "asr_text": asr_text, "voice_id": line["voice_id"], "emotion": emotion,
                    "generation_seconds": round(elapsed, 3), **media,
                }
                meta_name = f"{line['id']}_candidate_{index:02d}.json"
                _write(result_dir / meta_name, metadata)
                candidates.append({"line_id": line["id"], "candidate_id": metadata["candidate_id"], "filename": filename, "metadata_file": meta_name, "sha256": metadata["sha256"]})
                state.update({"candidates": candidates, "gpu_minutes": round((time.monotonic() - started) / 60, 3), "updated_at": _now()})
                _write(state_file, state)
        result = {"schema_version": PROTOCOL, "job_id": job_id, "episode_id": job["episode_id"], "status": "COMPLETE", "gpu_minutes": round((time.monotonic() - started) / 60, 3), "gpu_minutes_by_shot": {key: round(float(value) / 60, 3) for key, value in state["gpu_seconds_by_shot"].items()}, "completed_at": _now(), "candidates": candidates}
        _write(result_dir / "result.json", result)
        state.update({"status": "COMPLETE", "gpu_minutes": result["gpu_minutes"], "completed_at": _now(), "updated_at": _now()})
        _write(state_file, state)
        complete.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result_dir), str(complete))
        shutil.rmtree(running)
        return result
    except Exception as exc:
        state.update({"status": "FAILED", "error": str(exc), "failure_class": "asr_or_tts_error", "failed_at": _now(), "updated_at": _now()})
        _write(state_file, state)
        if result_dir.exists():
            shutil.move(str(result_dir), str(running / "partial_work"))
        failed.parent.mkdir(parents=True, exist_ok=True)
        if failed.exists():
            raise FileExistsError("preserving existing failed voice job") from exc
        shutil.move(str(running), str(failed))
        raise


def _manual_review_result(job_id: str, candidates: list[dict[str, Any]], state: dict[str, Any], elapsed_minutes: float) -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL, "job_id": job_id, "status": "NEEDS_MANUAL_REVIEW",
        "lines": candidates, "gpu_minutes": round(elapsed_minutes, 3),
        "gpu_minutes_by_shot": {key: round(float(value) / 60, 3) for key, value in state.get("gpu_seconds_by_shot", {}).items()},
    }


def _status(job_id: str | None = None, episode_id: str | None = None) -> dict[str, Any]:
    jobs = []
    base = RUNTIME / "voice" / "jobs"
    for name in ("inbox", "running", "complete", "failed"):
        directory = base / name
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or not JOB_RE.fullmatch(child.name) or (job_id and child.name != job_id):
                continue
            job_file = child / ("result.json" if name == "complete" else "job.json")
            state_file = child / "state.json"
            state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
            job = json.loads(job_file.read_text(encoding="utf-8")) if job_file.is_file() else {}
            if episode_id and job.get("episode_id") != episode_id:
                continue
            status = state.get("status") or {"inbox": "QUEUED", "running": "RUNNING", "complete": "COMPLETE", "failed": "FAILED"}[name]
            by_shot = job.get("gpu_minutes_by_shot") or {key: round(float(value) / 60, 3) for key, value in state.get("gpu_seconds_by_shot", {}).items()}
            request_file = child / "job.json"
            jobs.append({"job_id": child.name, "episode_id": job.get("episode_id"), "status": status, "location": name,
                         "request_sha256": _sha(request_file) if request_file.is_file() else None,
                         "gpu_minutes": state.get("gpu_minutes", job.get("gpu_minutes", 0.0)), "gpu_minutes_by_shot": by_shot, "stage": state.get("stage"), "error": state.get("error")})
    return {"schema_version": PROTOCOL, "jobs": jobs}


def _doctor() -> dict[str, Any]:
    model_files = {
        "config": INDEX_MODEL / "config.yaml",
        "gpt": INDEX_MODEL / "gpt.pth",
        "codec": INDEX_MODEL / "codec.pth",
        "s2mel": INDEX_MODEL / "s2mel.pth",
        "bigvgan": INDEX_MODEL / "hf_cache/bigvgan/bigvgan_generator.pt",
        "semantic_codec": INDEX_MODEL / "hf_cache/semantic_codec/model.safetensors",
        "asr_model": ASR_MODEL,
    }
    checks = {name: "PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL" for name, path in model_files.items()}
    checks["index_python"] = "PASS" if Path(INDEX_PYTHON).is_file() else "FAIL"
    checks["asr_python"] = "PASS" if Path(ASR_PYTHON).is_file() else "FAIL"
    checks["ffmpeg"] = "PASS" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "FAIL"
    if checks["index_python"] == "PASS":
        try:
            imported = subprocess.run(
                [INDEX_PYTHON, "-c", "import torch; from indextts.infer_v2_5 import IndexTTS2; print(int(torch.cuda.is_available()))"],
                cwd=INDEX_ROOT, capture_output=True, text=True, timeout=90, check=False,
            )
            checks["index_runtime"] = "PASS" if imported.returncode == 0 and imported.stdout.strip()[-1:] in {"0", "1"} else "FAIL"
            cuda_ready = imported.returncode == 0 and imported.stdout.strip().endswith("1")
        except (OSError, subprocess.TimeoutExpired):
            checks["index_runtime"] = "FAIL"
            cuda_ready = False
    else:
        checks["index_runtime"] = "FAIL"
        cuda_ready = False
    if checks["asr_python"] == "PASS":
        try:
            asr = subprocess.run([ASR_PYTHON, "-c", "import whisper"], capture_output=True, text=True, timeout=60, check=False)
            checks["asr_runtime"] = "PASS" if asr.returncode == 0 else "FAIL"
        except (OSError, subprocess.TimeoutExpired):
            checks["asr_runtime"] = "FAIL"
    else:
        checks["asr_runtime"] = "FAIL"
    status = "FAIL" if "FAIL" in checks.values() else "READY" if cuda_ready else "CONFIGURED_NO_GPU"
    return {"schema_version": PROTOCOL, "backend": "IndexTTS-2.5", "status": status, "checks": checks}


def _submit(job_dir: Path) -> dict[str, Any]:
    lock = _queue_lock()
    try:
        return _submit_locked(job_dir)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _submit_locked(job_dir: Path) -> dict[str, Any]:
    job = validate_job(job_dir)
    job_id = job["job_id"]
    inbox = RUNTIME / "voice" / "jobs" / "inbox" / job_id
    running = RUNTIME / "voice" / "jobs" / "running" / job_id
    complete = RUNTIME / "voice" / "jobs" / "complete" / job_id
    failed = RUNTIME / "voice" / "jobs" / "failed" / job_id
    if complete.exists() or running.exists() or failed.exists():
        return {"schema_version": PROTOCOL, "job_id": job_id, "status": _status(job_id)["jobs"][0]["status"], "existing": True}
    if job_dir.resolve() != inbox.resolve():
        raise ValueError("submit path must be voice/jobs/inbox/<job_id>")
    running.parent.mkdir(parents=True, exist_ok=True)
    inbox.rename(running)
    _write(running / "state.json", {"job_id": job_id, "status": "QUEUED", "submitted_at": _now()})
    log_dir = RUNTIME / "voice" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{job_id}.log").open("ab") as log:
        process = subprocess.Popen([INDEX_PYTHON, "voice_worker.py", "run", "--job-dir", str(running)], cwd=Path(__file__).resolve().parent, env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return {"schema_version": PROTOCOL, "job_id": job_id, "status": "SUBMITTED", "pid": process.pid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("--job-dir", required=True)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--job-id")
    status_parser.add_argument("--episode-id")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--job-dir", required=True)
    submit_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = _doctor()
        elif args.command == "submit":
            result = _submit(Path(args.job_dir).resolve())
        elif args.command == "status":
            result = _status(args.job_id, args.episode_id)
        else:
            with (RUNTIME / "jobs" / ".gpu.lock").open("w", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                result = _run(Path(args.job_dir).resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"schema_version": PROTOCOL, "status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
