from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .package import sha256_file
from .remote import _scp, _ssh, _worker, load_remote_config, resolve_connection

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
PROTOCOL = "ai-interview-voice-v1"


def _status_worker(config: dict[str, Any], connection: dict[str, Any], *arguments: str) -> dict[str, Any]:
    return _worker(config, connection, list(arguments), expected_schema=PROTOCOL, entrypoint="voice_worker.py")


def submit_voice(package: str | Path, root: str | Path, on_submit: Callable[[], Any] | None = None) -> dict[str, Any]:
    package = Path(package).resolve()
    job_path = package / "job.json"
    if not package.is_dir() or not job_path.is_file():
        raise FileNotFoundError("voice package must contain job.json")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id) or package.name != job_id:
        raise ValueError("voice package directory and safe job_id must match")
    for relative, digest in job.get("input_sha256", {}).items():
        asset = (package / relative).resolve()
        if not asset.is_relative_to(package) or not asset.is_file() or sha256_file(asset) != digest:
            raise ValueError(f"packaged voice reference failed hash validation: {relative}")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    existing = _status_worker(config, connection, "status", "--job-id", job_id, "--json")
    match = next((item for item in existing.get("jobs", []) if item.get("job_id") == job_id), None)
    if match:
        return {**match, "existing": True}
    base = f"{config['remote']['root']}/voice/jobs/inbox"
    destination = f"{base}/{job_id}"
    staging = f"{base}/.{job_id}.upload-{uuid.uuid4().hex}"
    made = subprocess.run(_ssh(connection, f"mkdir -p {shlex.quote(base)} && mkdir {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
    if made.returncode:
        raise RuntimeError(made.stderr.strip() or "unable to create unique voice upload directory")
    rsync = shutil.which("rsync")
    if rsync:
        command = [rsync, "-az", "-e", shlex.join(["ssh", "-p", str(connection["port"]), "-o", "BatchMode=yes"]), str(package) + "/", f"{connection['target']}:{staging}/"]
    else:
        command = [*_scp(connection), "-r", str(package) + "/.", f"{connection['target']}:{staging}/"]
    transfer = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if transfer.returncode:
        subprocess.run(_ssh(connection, f"rm -rf -- {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
        raise RuntimeError(transfer.stderr.strip()[-2000:] or "voice package upload failed")
    publish = subprocess.run(_ssh(connection, f"if test -e {shlex.quote(destination)}; then exit 73; fi; mv {shlex.quote(staging)} {shlex.quote(destination)}"), capture_output=True, text=True, timeout=30, check=False)
    if publish.returncode:
        raise RuntimeError(publish.stderr.strip() or "voice job id already exists; refusing overwrite")
    if on_submit:
        on_submit()
    response = _status_worker(config, connection, "submit", "--job-dir", destination, "--json")
    if response.get("job_id") != job_id:
        raise RuntimeError("remote voice response job_id mismatch")
    return response


def voice_status(root: str | Path, episode_id: str, job_id: str | None = None) -> dict[str, Any]:
    config = load_remote_config(root)
    connection = resolve_connection(config)
    arguments = ["status", "--episode-id", episode_id]
    if job_id:
        if not SAFE_ID.fullmatch(job_id):
            raise ValueError("unsafe voice job_id")
        arguments += ["--job-id", job_id]
    arguments.append("--json")
    return _status_worker(config, connection, *arguments)


def pull_voice(root: str | Path, job_id: str, destination: str | Path) -> Path:
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe voice job_id")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"preserve existing voice result directory: {destination}")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    remote_dir = f"{config['remote']['root']}/voice/jobs/complete/{job_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        raw = temp / "remote"
        raw.mkdir()
        first = subprocess.run([*_scp(connection), "-r", f"{connection['target']}:{remote_dir}/.", str(raw)], capture_output=True, text=True, timeout=300, check=False)
        if first.returncode:
            raise RuntimeError(first.stderr.strip() or "voice result download failed")
        remote_result = raw / "result.json"
        if not remote_result.is_file() or remote_result.is_symlink():
            raise FileNotFoundError("remote voice result.json is missing or unsafe")
        result = json.loads(remote_result.read_text(encoding="utf-8"))
        if result.get("schema_version") != PROTOCOL or result.get("job_id") != job_id or result.get("status") != "COMPLETE":
            raise ValueError("remote voice result identity/status mismatch")
        validated = temp / "validated"
        validated.mkdir()
        for candidate in result.get("candidates", []):
            for field in ("filename", "metadata_file"):
                name = candidate.get(field)
                if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or Path(name).name != name:
                    raise ValueError(f"unsafe voice result {field}")
                path = raw / name
                if path.is_symlink() or not path.is_file():
                    raise FileNotFoundError(f"voice result file is missing or unsafe: {name}")
            metadata = json.loads((raw / candidate["metadata_file"]).read_text(encoding="utf-8"))
            wav = raw / candidate["filename"]
            if metadata.get("job_id") != job_id or metadata.get("filename") != wav.name or sha256_file(wav) != metadata.get("sha256") or metadata.get("sha256") != candidate.get("sha256"):
                raise ValueError(f"voice candidate hash/identity mismatch: {wav.name}")
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(wav)], capture_output=True, text=True, check=False)
            if probe.returncode:
                raise RuntimeError(probe.stderr.strip() or f"ffprobe failed: {wav.name}")
            streams = json.loads(probe.stdout).get("streams", [])
            if not any(item.get("codec_type") == "audio" for item in streams):
                raise ValueError(f"voice candidate has no audio stream: {wav.name}")
            decode = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-f", "null", "-"], capture_output=True, text=True, check=False)
            if decode.returncode:
                raise ValueError(f"voice candidate failed full decode: {wav.name}")
            shutil.copy2(wav, validated / wav.name)
            shutil.copy2(raw / candidate["metadata_file"], validated / candidate["metadata_file"])
        (validated / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validated.rename(destination)
    return destination
