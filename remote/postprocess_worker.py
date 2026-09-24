"""GPU-gated LatentSync and SeedVR2 worker for the AutoDL runtime."""

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
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

RUNTIME = Path(os.environ.get("AI_INTERVIEW_RUNTIME", "/root/autodl-tmp/ai_interview")).resolve()
PROTOCOL = "ai-interview-postprocess-v1"
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
STATE_DIRS = ("inbox", "running", "complete", "failed")
LATENTSYNC_PYTHON = RUNTIME / "envs" / "latentsync" / "bin" / "python"
LATENTSYNC_ROOT = RUNTIME / "apps" / "LatentSync"
SEEDVR2_PYTHON = Path("/root/miniconda3/bin/python")
SEEDVR2_ROOT = RUNTIME / "apps" / "ComfyUI" / "custom_nodes" / "comfyui_seedvr2"
SEEDVR2_MODEL_DIR = RUNTIME / "apps" / "ComfyUI" / "models" / "SEEDVR2"
SEEDVR2_SHA256 = {
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors": "3bf1e43ebedd570e7e7a0b1b60d6a02e105978f505c8128a241cde99a8240cff",
    "ema_vae_fp16.safetensors": "20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1",
}


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
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _job_lock():
    """Serialize postprocess queue mutations without waiting for GPU inference."""
    path = RUNTIME / "postprocess" / ".state.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _gpu_lock():
    """Share the GPU mutex with H3, TTS and VoiceDesign workers."""
    path = RUNTIME / "jobs" / ".gpu.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _safe_asset(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("assets/") or "\\" in value:
        raise ValueError("postprocess input must be inside assets/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ValueError("postprocess input path is unsafe")
    return value


def validate_job(job_dir: Path, *, allow_upload_name: bool = False) -> dict[str, Any]:
    if job_dir.is_symlink() or not job_dir.is_dir() or not (job_dir / "job.json").is_file():
        raise ValueError("postprocess package must be a real directory containing job.json")
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if not isinstance(job, dict) or job.get("schema_version") != PROTOCOL:
        raise ValueError(f"schema_version must be {PROTOCOL}")
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id) or (not allow_upload_name and job_dir.name != job_id):
        raise ValueError("unsafe postprocess job_id or directory mismatch")
    mode = job.get("mode")
    if mode not in {"latentsync", "seedvr2"}:
        raise ValueError("postprocess mode must be latentsync or seedvr2")
    if not isinstance(job.get("episode_id"), str) or not re.fullmatch(r"EP\d{4}_[a-z0-9][a-z0-9_-]*", job["episode_id"]):
        raise ValueError("invalid postprocess episode_id")
    if not isinstance(job.get("shot_id"), str) or not re.fullmatch(r"S\d{3}", job["shot_id"]):
        raise ValueError("invalid postprocess shot_id")
    if not isinstance(job.get("selected_by"), str) or not job["selected_by"].strip():
        raise ValueError("postprocess job must record selected_by")
    if not isinstance(job.get("timing"), dict) or job["timing"].get("audio_sample_rate") != 48000:
        raise ValueError("postprocess job must declare 48 kHz edit audio")
    inputs = job.get("inputs")
    hashes = job.get("input_sha256")
    if not isinstance(inputs, dict) or set(inputs) != {"video", "audio"} or not isinstance(hashes, dict):
        raise ValueError("postprocess job must declare video/audio input hashes")
    if set(hashes) != set(inputs.values()):
        raise ValueError("input_sha256 must exactly match declared inputs")
    for kind in ("video", "audio"):
        relative = _safe_asset(inputs[kind])
        expected = hashes.get(relative)
        asset = job_dir / relative
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
            raise ValueError(f"invalid input hash for {kind}")
        if asset.is_symlink() or not asset.is_file() or _sha(asset) != expected:
            raise ValueError(f"missing or hash-mismatched {kind} input")
    budget = job.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("postprocess job must declare GPU budget")
    for key in ("remaining_gpu_minutes_per_shot", "remaining_gpu_minutes_per_episode"):
        value = budget.get(key)
        limit = 15 if key.endswith("per_shot") else 300
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= limit:
            raise ValueError(f"{key} must be positive and at most {limit}")
    if mode == "latentsync":
        if job.get("parameters") != {"inference_steps": 20, "guidance_scale": 1.5}:
            raise ValueError("LatentSync parameters must match the reviewed fixed profile")
        if not LATENTSYNC_PYTHON.is_file() or not (LATENTSYNC_ROOT / "scripts" / "inference.py").is_file():
            raise RuntimeError("LatentSync runtime is unavailable")
        if not (RUNTIME / "models/lipsync/LatentSync-1.6/latentsync_unet.pt").is_file():
            raise RuntimeError("LatentSync checkpoint is unavailable")
    else:
        expected_parameters = {
            "model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "resolution": 2160,
            "batch_size": 5,
        }
        if job.get("parameters") != expected_parameters:
            raise ValueError("SeedVR2 parameters must match the reviewed fixed profile")
        if not SEEDVR2_PYTHON.is_file() or not (SEEDVR2_ROOT / "inference_cli.py").is_file():
            raise RuntimeError("SeedVR2 runtime is unavailable")
        for filename, digest in SEEDVR2_SHA256.items():
            model_path = SEEDVR2_MODEL_DIR / filename
            if not model_path.is_file():
                raise RuntimeError(f"SeedVR2 model is unavailable: {filename}")
            if _sha(model_path) != digest:
                raise RuntimeError(f"SeedVR2 model hash mismatch; refusing inference auto-download: {filename}")
    return job


def _probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed for postprocess output")
    value = json.loads(result.stdout)
    video = next((item for item in value.get("streams", []) if item.get("codec_type") == "video"), None)
    audio = next((item for item in value.get("streams", []) if item.get("codec_type") == "audio"), None)
    if not video or not audio or audio.get("sample_rate") != "48000":
        raise ValueError("postprocess output must contain video and 48 kHz audio")
    decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    if decoded.returncode:
        raise RuntimeError(decoded.stderr.strip() or "postprocess output decode failed")
    return {
        "duration_sec": float(value.get("format", {}).get("duration", 0) or 0),
        "width": int(video.get("width", 0)), "height": int(video.get("height", 0)),
        "fps": video.get("r_frame_rate"), "audio_sample_rate": int(audio["sample_rate"]),
    }


def _require_allocated_gpu(mode: str) -> None:
    python = LATENTSYNC_PYTHON if mode == "latentsync" else SEEDVR2_PYTHON
    check = subprocess.run(
        [str(python), "-c", "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if check.returncode:
        raise RuntimeError("inference refused: selected runtime cannot access an allocated CUDA GPU")


def _validate_media_inputs(job_dir: Path, job: dict[str, Any]) -> None:
    for kind in ("video", "audio"):
        path = job_dir / job["inputs"][kind]
        checked = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=False,
        )
        if checked.returncode:
            raise RuntimeError(checked.stderr.strip() or f"ffprobe failed for {kind} input")
        probe = json.loads(checked.stdout)
        streams = probe.get("streams", [])
        if kind == "video" and not any(stream.get("codec_type") == "video" for stream in streams):
            raise ValueError("postprocess video input has no video stream")
        if kind == "audio":
            audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
            if len(audio) != 1 or audio[0].get("sample_rate") != "48000":
                raise ValueError("postprocess edit audio must contain one 48 kHz stream")


def _command(job: dict[str, Any], job_dir: Path, output_no_audio: Path) -> tuple[list[str], Path, dict[str, str]]:
    video = job_dir / job["inputs"]["video"]
    audio = job_dir / job["inputs"]["audio"]
    env = os.environ.copy()
    if job["mode"] == "latentsync":
        model = RUNTIME / "models/lipsync/LatentSync-1.6/latentsync_unet.pt"
        command = [
            str(LATENTSYNC_PYTHON), "-m", "scripts.inference",
            "--unet_config_path", "configs/unet/stage2_512.yaml",
            "--inference_ckpt_path", str(model),
            "--inference_steps", str(int(job["parameters"]["inference_steps"])),
            "--guidance_scale", str(float(job["parameters"]["guidance_scale"])),
            "--video_path", str(video), "--audio_path", str(audio),
            "--video_out_path", str(output_no_audio), "--enable_deepcache",
        ]
        return command, LATENTSYNC_ROOT, env
    command = [
        str(SEEDVR2_PYTHON), str(SEEDVR2_ROOT / "inference_cli.py"),
        "--video_path", str(video), "--seed", str(int(job["parameters"].get("seed", 100))),
        "--resolution", str(int(job["parameters"]["resolution"])),
        "--batch_size", str(int(job["parameters"]["batch_size"])),
        "--model", str(job["parameters"]["model"]), "--model_dir", str(SEEDVR2_MODEL_DIR),
        "--output", str(output_no_audio), "--output_format", "video", "--cuda_device", "0",
    ]
    env["PYTHONPATH"] = str(SEEDVR2_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return command, SEEDVR2_ROOT, env


def _run(job_dir: Path) -> dict[str, Any]:
    job = validate_job(job_dir)
    job_id = job["job_id"]
    mode = job["mode"]
    base = RUNTIME / "postprocess" / "jobs" / mode
    running = base / "running" / job_id
    complete = base / "complete" / job_id
    failed = base / "failed" / job_id
    if job_dir.resolve() != running.resolve():
        raise ValueError("postprocess worker runs only jobs/<mode>/running/<job_id>")
    if complete.exists() or failed.exists():
        raise FileExistsError("preserving existing complete or failed postprocess job")
    state_file = running / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    state.update({"job_id": job_id, "status": "RUNNING", "stage": "loading_model", "pid": os.getpid(), "updated_at": _now()})
    _write(state_file, state)
    started = time.monotonic()
    try:
        _validate_media_inputs(running, job)
        _require_allocated_gpu(mode)
        work = running / "work"
        if work.exists():
            raise FileExistsError(f"preserving existing partial postprocess output: {work}")
        work.mkdir()
        output_no_audio = work / "video_no_audio.mp4"
        output = work / "result.mp4"
        command, cwd, env = _command(job, running, output_no_audio)
        budget = min(
            15.0,
            float(job["budget"]["remaining_gpu_minutes_per_shot"]),
            float(job["budget"]["remaining_gpu_minutes_per_episode"]),
        )
        state.update({"stage": "inference", "updated_at": _now()})
        _write(state_file, state)
        with (running / "worker.log").open("ab") as log:
            subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=max(1, int(budget * 60)), check=True)
        if not output_no_audio.is_file() or output_no_audio.stat().st_size == 0:
            raise RuntimeError("inference returned without a video output")
        audio = running / job["inputs"]["audio"]
        mux = subprocess.run([
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(output_no_audio), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000",
            "-shortest", str(output),
        ], capture_output=True, text=True, check=False)
        if mux.returncode:
            raise RuntimeError(mux.stderr.strip()[-1500:] or "failed to mux edit audio")
        metadata = _probe_video(output)
        digest = _sha(output)
        elapsed = round((time.monotonic() - started) / 60, 3)
        result = {
            "schema_version": PROTOCOL, "job_id": job_id, "episode_id": job["episode_id"],
            "shot_id": job["shot_id"], "mode": mode, "status": "COMPLETE",
            "gpu_minutes": elapsed, "output": "work/result.mp4", "sha256": digest,
            "completed_at": _now(), **metadata,
        }
        _write(running / "result.json", result)
        state.update({"status": "COMPLETE", "gpu_minutes": elapsed, "completed_at": _now(), "updated_at": _now()})
        _write(state_file, state)
        complete.parent.mkdir(parents=True, exist_ok=True)
        if complete.exists():
            raise FileExistsError("preserving existing completed postprocess job")
        shutil.move(str(running), str(complete))
        return result
    except Exception as exc:
        elapsed = round((time.monotonic() - started) / 60, 3)
        state.update({"status": "FAILED", "gpu_minutes": elapsed, "error": str(exc),
                      "failure_class": "postprocess_error", "failed_at": _now(), "updated_at": _now()})
        _write(state_file, state)
        failed.parent.mkdir(parents=True, exist_ok=True)
        if failed.exists():
            raise FileExistsError("preserving existing failed postprocess job") from exc
        shutil.move(str(running), str(failed))
        raise


def _status(job_id: str | None = None, episode_id: str | None = None) -> dict[str, Any]:
    jobs = []
    for mode in ("latentsync", "seedvr2"):
        base = RUNTIME / "postprocess" / "jobs" / mode
        for state_name in STATE_DIRS:
            directory = base / state_name
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_dir() or child.is_symlink() or not JOB_RE.fullmatch(child.name) or (job_id and child.name != job_id):
                    continue
                request = json.loads((child / "job.json").read_text(encoding="utf-8")) if (child / "job.json").is_file() else {}
                state = json.loads((child / "state.json").read_text(encoding="utf-8")) if (child / "state.json").is_file() else {}
                result = json.loads((child / "result.json").read_text(encoding="utf-8")) if (child / "result.json").is_file() else {}
                candidate_episode = request.get("episode_id", result.get("episode_id"))
                if episode_id and candidate_episode != episode_id:
                    continue
                request_file = child / "job.json"
                jobs.append({"job_id": child.name, "episode_id": candidate_episode, "shot_id": request.get("shot_id", result.get("shot_id")),
                             "mode": mode, "status": state.get("status") or {"inbox": "QUEUED", "running": "RUNNING", "complete": "COMPLETE", "failed": "FAILED"}[state_name],
                             "location": state_name, "request_sha256": _sha(request_file) if request_file.is_file() else None,
                             "gpu_minutes": state.get("gpu_minutes", result.get("gpu_minutes", 0.0)), "stage": state.get("stage"), "error": state.get("error")})
    return {"schema_version": PROTOCOL, "jobs": jobs}


def _find_job_locations(job_id: str) -> list[tuple[str, str, Path]]:
    found = []
    for mode in ("latentsync", "seedvr2"):
        for state_name in STATE_DIRS:
            candidate = RUNTIME / "postprocess" / "jobs" / mode / state_name / job_id
            if candidate.exists() or candidate.is_symlink():
                found.append((mode, state_name, candidate))
    return found


def _same_job_request(left: Path, right: Path) -> bool:
    left_file, right_file = left / "job.json", right / "job.json"
    if left_file.is_symlink() or right_file.is_symlink() or not left_file.is_file() or not right_file.is_file():
        return False
    return json.loads(left_file.read_text(encoding="utf-8")) == json.loads(right_file.read_text(encoding="utf-8"))


def _publish(staging: Path, job_id: str, mode: str) -> dict[str, Any]:
    if mode not in {"latentsync", "seedvr2"} or not JOB_RE.fullmatch(job_id):
        raise ValueError("unsafe postprocess job identity")
    inbox_dir = RUNTIME / "postprocess" / "jobs" / mode / "inbox"
    staging = staging.absolute()
    if staging.is_symlink() or staging.parent.resolve() != inbox_dir.resolve() or not staging.name.startswith(f".{job_id}.upload-"):
        raise ValueError("publish requires a unique staging directory inside the selected inbox")
    staged_job = validate_job(staging, allow_upload_name=True)
    if staged_job.get("job_id") != job_id or staged_job.get("mode") != mode:
        raise ValueError("staged package identity does not match publish request")

    with _job_lock():
        locations = _find_job_locations(job_id)
        if len(locations) > 1:
            raise FileExistsError("job id already exists in multiple state directories; preserving upload staging")
        if locations:
            existing_mode, state_name, existing = locations[0]
            if existing_mode != mode or not _same_job_request(existing, staging):
                raise FileExistsError("job id already exists with a different package; preserving upload staging")
            validate_job(existing)
            shutil.rmtree(staging)
            match = next((item for item in _status(job_id=job_id)["jobs"]
                          if item["mode"] == existing_mode and item["location"] == state_name), None)
            if match is None:
                raise RuntimeError("existing postprocess job became unreadable while publishing")
            return {"schema_version": PROTOCOL, **match, "existing": True}

        inbox_dir.mkdir(parents=True, exist_ok=True)
        destination = inbox_dir / job_id
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("job inbox destination appeared during publish; preserving upload staging")
        os.rename(staging, destination)
        return {
            "schema_version": PROTOCOL, "job_id": job_id, "episode_id": staged_job["episode_id"],
            "shot_id": staged_job["shot_id"], "mode": mode, "status": "QUEUED",
            "location": "inbox", "existing": False,
        }


def _submit(job_dir: Path) -> dict[str, Any]:
    requested = job_dir.absolute()
    job_id, mode = requested.name, requested.parent.parent.name
    if not JOB_RE.fullmatch(job_id) or mode not in {"latentsync", "seedvr2"}:
        raise ValueError("submit path must be postprocess/jobs/<mode>/inbox/<job_id>")
    base = RUNTIME / "postprocess" / "jobs" / mode
    inbox, running = base / "inbox" / job_id, base / "running" / job_id
    with _job_lock():
        locations = _find_job_locations(job_id)
        if len(locations) > 1:
            raise FileExistsError("job id already exists in multiple state directories")
        if locations:
            existing_mode, state_name, existing = locations[0]
            if existing_mode != mode:
                raise FileExistsError("job id is already registered for a different postprocess mode")
            if state_name != "inbox":
                match = next((item for item in _status(job_id=job_id)["jobs"]
                              if item["mode"] == mode and item["location"] == state_name), None)
                if match is None:
                    raise FileExistsError("job id exists without a readable status")
                return {"schema_version": PROTOCOL, **match, "existing": True}
            if requested.resolve() != inbox.resolve():
                raise ValueError("submit path must be postprocess/jobs/<mode>/inbox/<job_id>")
            job = validate_job(inbox)
            running.parent.mkdir(parents=True, exist_ok=True)
            if running.exists() or running.is_symlink():
                raise FileExistsError("running destination appeared; preserving inbox job")
            os.rename(inbox, running)
            _write(running / "state.json", {"job_id": job_id, "status": "QUEUED", "submitted_at": _now()})
            python = LATENTSYNC_PYTHON if mode == "latentsync" else SEEDVR2_PYTHON
            log_dir = base / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / f"{job_id}.log").open("ab") as log:
                process = subprocess.Popen([str(python), str(Path(__file__).resolve()), "run", "--job-dir", str(running)],
                                           cwd=Path(__file__).resolve().parent, env=os.environ.copy(), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            return {"schema_version": PROTOCOL, "job_id": job_id, "episode_id": job["episode_id"], "shot_id": job["shot_id"],
                    "mode": mode, "status": "SUBMITTED", "location": "running", "pid": process.pid}
        if requested.resolve() != inbox.resolve():
            raise ValueError("submit path must be postprocess/jobs/<mode>/inbox/<job_id>")
        raise FileNotFoundError("postprocess job is not present in inbox or another state directory")


def _cleanup(job_id: str, expected_sha256: str) -> dict[str, Any]:
    if not JOB_RE.fullmatch(job_id) or not HASH_RE.fullmatch(expected_sha256):
        raise ValueError("unsafe job id or output SHA-256")
    matches = []
    for mode in ("latentsync", "seedvr2"):
        complete = RUNTIME / "postprocess" / "jobs" / mode / "complete" / job_id
        if complete.exists():
            matches.append(complete)
    if len(matches) != 1:
        raise FileNotFoundError("cleanup requires exactly one completed postprocess job")
    complete = matches[0]
    result = json.loads((complete / "result.json").read_text(encoding="utf-8"))
    output = complete / result.get("output", "")
    if result.get("status") != "COMPLETE" or result.get("job_id") != job_id:
        raise ValueError("remote result identity/status mismatch")
    if output.is_symlink() or not output.is_file() or _sha(output) != expected_sha256 or result.get("sha256") != expected_sha256:
        raise ValueError("refusing cleanup: imported result SHA-256 does not match remote result")
    shutil.rmtree(complete)
    return {"schema_version": PROTOCOL, "job_id": job_id, "status": "CLEANED", "verified_sha256": expected_sha256}


def _doctor(mode: str) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    python = LATENTSYNC_PYTHON if mode == "latentsync" else SEEDVR2_PYTHON
    app = LATENTSYNC_ROOT if mode == "latentsync" else SEEDVR2_ROOT
    if mode == "latentsync":
        expected = {
            "python": LATENTSYNC_PYTHON,
            "inference": LATENTSYNC_ROOT / "scripts" / "inference.py",
            "config": LATENTSYNC_ROOT / "configs" / "unet" / "stage2_512.yaml",
            "unet": RUNTIME / "models/lipsync/LatentSync-1.6/latentsync_unet.pt",
            "whisper": RUNTIME / "models/lipsync/LatentSync-1.6/whisper/tiny.pt",
        }
        expected_sizes = {"unet": 5_072_222_488, "whisper": 75_572_083}
    else:
        expected = {
            "python": SEEDVR2_PYTHON,
            "inference": SEEDVR2_ROOT / "inference_cli.py",
            "model": SEEDVR2_MODEL_DIR / "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
            "vae": SEEDVR2_MODEL_DIR / "ema_vae_fp16.safetensors",
        }
        expected_sizes = {"model": 3_391_544_696, "vae": 501_324_814}
        expected_hashes = {
            "model": SEEDVR2_SHA256["seedvr2_ema_3b_fp8_e4m3fn.safetensors"],
            "vae": SEEDVR2_SHA256["ema_vae_fp16.safetensors"],
        }
    for name, path in expected.items():
        exists = path.is_file() if name in {"python", "inference", "config", "unet", "whisper", "model", "vae"} else path.exists()
        checks.append({"name": name, "status": "PASS" if exists else "FAIL", "detail": str(path)})
        if name in expected_sizes and exists:
            size_ok = path.stat().st_size == expected_sizes[name]
            checks.append({"name": f"{name}_size", "status": "PASS" if size_ok else "FAIL", "detail": str(path.stat().st_size)})
        if mode == "seedvr2" and name in expected_hashes and exists:
            hash_ok = _sha(path) == expected_hashes[name]
            checks.append({"name": f"{name}_sha256", "status": "PASS" if hash_ok else "FAIL", "detail": "official hash verified" if hash_ok else "hash mismatch"})
    import_command = (
        [str(python), "-m", "scripts.inference", "--help"]
        if mode == "latentsync" else [str(python), str(app / "inference_cli.py"), "--help"]
    )
    try:
        imported = subprocess.run(import_command, cwd=app, capture_output=True, text=True, timeout=90, check=False)
        ok = imported.returncode == 0 and "usage:" in (imported.stdout + imported.stderr).lower()
        detail = "CLI imports and argument contract pass" if ok else (imported.stderr.strip()[-800:] or "CLI help returned unexpected output")
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, detail = False, str(exc)
    checks.append({"name": "runtime_cli", "status": "PASS" if ok else "FAIL", "detail": detail})
    cuda = subprocess.run([str(python), "-c", "import torch; print(int(torch.cuda.is_available()))"], capture_output=True, text=True, timeout=45, check=False)
    cuda_ready = cuda.returncode == 0 and cuda.stdout.strip() == "1"
    checks.append({"name": "cuda", "status": "PASS" if cuda_ready else "CONFIGURED_NO_GPU" if cuda.returncode == 0 else "FAIL",
                   "detail": "allocated CUDA device visible" if cuda_ready else "no allocated GPU" if cuda.returncode == 0 else cuda.stderr.strip()[-800:]})
    if any(check["status"] == "FAIL" for check in checks):
        status = "FAIL"
    elif not cuda_ready:
        status = "CONFIGURED_NO_GPU"
    else:
        status = "READY"
    return {"schema_version": PROTOCOL, "mode": mode, "status": status, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--staging-dir", required=True)
    publish.add_argument("--job-id", required=True)
    publish.add_argument("--mode", choices=("latentsync", "seedvr2"), required=True)
    submit = commands.add_parser("submit"); submit.add_argument("--job-dir", required=True)
    status = commands.add_parser("status"); status.add_argument("--job-id"); status.add_argument("--episode-id")
    doctor = commands.add_parser("doctor"); doctor.add_argument("--mode", choices=("latentsync", "seedvr2"), required=True)
    run = commands.add_parser("run"); run.add_argument("--job-dir", required=True)
    cleanup = commands.add_parser("cleanup"); cleanup.add_argument("--job-id", required=True); cleanup.add_argument("--sha256", required=True)
    for command in (publish, submit, status, doctor, run, cleanup): command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "publish": result = _publish(Path(args.staging_dir), args.job_id, args.mode)
        elif args.command == "submit": result = _submit(Path(args.job_dir))
        elif args.command == "status": result = _status(args.job_id, args.episode_id)
        elif args.command == "doctor": result = _doctor(args.mode)
        elif args.command == "cleanup": result = _cleanup(args.job_id, args.sha256)
        else:
            with _gpu_lock():
                result = _run(Path(args.job_dir).resolve())
        print(json.dumps(result, ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"schema_version": PROTOCOL, "status": "FAILED", "error": str(exc)}, ensure_ascii=False)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
