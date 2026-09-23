from __future__ import annotations

import json
import re
import shutil
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .package import sha256_file
from .remote import _scp, _ssh, _worker, load_remote_config, resolve_connection

PROTOCOL = "ai-interview-voice-design-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")


def voice_design_status(root: str | Path, episode_id: str, job_id: str | None = None) -> dict[str, Any]:
    config = load_remote_config(root)
    connection = resolve_connection(config)
    args = ["status", "--episode-id", episode_id]
    if job_id:
        args += ["--job-id", job_id]
    args.append("--json")
    return _worker(config, connection, args, expected_schema=PROTOCOL, entrypoint="voice_design_worker.py")


def submit_voice_design(package: str | Path, root: str | Path, on_submit: Callable[[], Any] | None = None) -> dict[str, Any]:
    package = Path(package).resolve()
    job_path = package / "job.json"
    if not package.is_dir() or not job_path.is_file():
        raise FileNotFoundError("voice-design package must contain job.json")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id")
    if job.get("schema_version") != PROTOCOL or not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id) or package.name != job_id:
        raise ValueError("voice-design package identity is invalid")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    existing = voice_design_status(root, job["episode_id"], job_id)
    match = next((item for item in existing.get("jobs", []) if item.get("job_id") == job_id), None)
    if match:
        return {**match, "existing": True}
    base = f"{config['remote']['root']}/voice_design/jobs/inbox"
    destination = f"{base}/{job_id}"
    staging = f"{base}/.{job_id}.upload-{uuid.uuid4().hex}"
    made = subprocess.run(_ssh(connection, f"mkdir -p {shlex.quote(base)} && mkdir {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
    if made.returncode:
        raise RuntimeError(made.stderr.strip() or "unable to create unique VoiceDesign upload directory")
    rsync = shutil.which("rsync")
    if rsync:
        command = [rsync, "-az", "-e", f"ssh -p {connection['port']} -o BatchMode=yes", str(package) + "/", f"{connection['target']}:{staging}/"]
    else:
        command = [*_scp(connection), "-r", str(package) + "/.", f"{connection['target']}:{staging}/"]
    transfer = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if transfer.returncode:
        subprocess.run(_ssh(connection, f"rm -rf -- {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
        raise RuntimeError(transfer.stderr.strip()[-2000:] or "VoiceDesign package upload failed")
    publish = subprocess.run(_ssh(connection, f"if test -e {shlex.quote(destination)}; then exit 73; fi; mv {shlex.quote(staging)} {shlex.quote(destination)}"), capture_output=True, text=True, timeout=30, check=False)
    if publish.returncode:
        raise RuntimeError(publish.stderr.strip() or "VoiceDesign job already exists; refusing overwrite")
    if on_submit:
        on_submit()
    result = _worker(config, connection, ["submit", "--job-dir", destination, "--json"], expected_schema=PROTOCOL, entrypoint="voice_design_worker.py")
    if result.get("job_id") != job_id:
        raise RuntimeError("remote VoiceDesign response job_id mismatch")
    return result


def pull_voice_design(root: str | Path, job_id: str, destination: str | Path) -> Path:
    if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe VoiceDesign job_id")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"preserve existing VoiceDesign result directory: {destination}")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    remote_dir = f"{config['remote']['root']}/voice_design/jobs/complete/{job_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        raw = temp / "remote"
        raw.mkdir()
        fetched = subprocess.run([*_scp(connection), "-r", f"{connection['target']}:{remote_dir}/.", str(raw)], capture_output=True, text=True, timeout=300, check=False)
        if fetched.returncode:
            raise RuntimeError(fetched.stderr.strip() or "VoiceDesign result download failed")
        result_path = raw / "result.json"
        if not result_path.is_file() or result_path.is_symlink():
            raise FileNotFoundError("remote VoiceDesign result.json is missing or unsafe")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("schema_version") != PROTOCOL or result.get("job_id") != job_id or result.get("status") != "COMPLETE":
            raise ValueError("remote VoiceDesign result identity/status mismatch")
        validated = temp / "validated"
        validated.mkdir()
        for candidate in result.get("candidates", []):
            names = [candidate.get("filename"), candidate.get("metadata_file")]
            for name in names:
                if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", name):
                    raise ValueError(f"unsafe VoiceDesign result file: {name}")
                source = raw / name
                if source.is_symlink() or not source.is_file():
                    raise FileNotFoundError(f"VoiceDesign candidate file is missing or unsafe: {name}")
            metadata = json.loads((raw / candidate["metadata_file"]).read_text(encoding="utf-8"))
            wav = raw / candidate["filename"]
            if metadata.get("job_id") != job_id or metadata.get("filename") != wav.name or sha256_file(wav) != metadata.get("sha256") or metadata.get("sha256") != candidate.get("sha256"):
                raise ValueError(f"VoiceDesign candidate hash/identity mismatch: {wav.name}")
            if not 10 <= float(metadata.get("duration_sec", 0)) <= 30:
                raise ValueError(f"VoiceDesign candidate must be 10-30 seconds: {wav.name}")
            decode = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-f", "null", "-"], capture_output=True, text=True, check=False)
            if decode.returncode:
                raise ValueError(f"VoiceDesign candidate failed full decode: {wav.name}")
            shutil.copy2(wav, validated / wav.name)
            shutil.copy2(raw / candidate["metadata_file"], validated / candidate["metadata_file"])
        (validated / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validated.rename(destination)
    return destination
