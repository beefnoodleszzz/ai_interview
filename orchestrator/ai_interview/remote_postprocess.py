"""AutoDL transport and verified result import for postprocess jobs."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .media import deterministic_qc
from .package import sha256_file
from .remote import _scp, _ssh, _worker as _remote_worker, load_remote_config, resolve_connection

PROTOCOL = "ai-interview-postprocess-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")


def _worker(config: dict[str, Any], connection: dict[str, Any], *arguments: str) -> dict[str, Any]:
    return _remote_worker(
        config, connection, list(arguments), expected_schema=PROTOCOL,
        entrypoint="postprocess_worker.py",
    )


def postprocess_status(root: str | Path, episode_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    config = load_remote_config(root)
    connection = resolve_connection(config)
    arguments = ["status"]
    if job_id:
        if not SAFE_ID.fullmatch(job_id):
            raise ValueError("unsafe postprocess job_id")
        arguments += ["--job-id", job_id]
    if episode_id:
        arguments += ["--episode-id", episode_id]
    arguments.append("--json")
    return _worker(config, connection, *arguments)


def postprocess_doctor(root: str | Path, mode: str) -> dict[str, Any]:
    if mode not in {"latentsync", "seedvr2"}:
        raise ValueError("postprocess mode must be latentsync or seedvr2")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    return _worker(config, connection, "doctor", "--mode", mode, "--json")


def submit_postprocess(
    package: str | Path,
    root: str | Path,
    *,
    on_reserve: Any | None = None,
) -> dict[str, Any]:
    package = Path(package).resolve()
    job_file = package / "job.json"
    if package.is_symlink() or not package.is_dir() or not job_file.is_file():
        raise FileNotFoundError("postprocess package must contain job.json")
    job = json.loads(job_file.read_text(encoding="utf-8"))
    job_id, mode = job.get("job_id"), job.get("mode")
    if job.get("schema_version") != PROTOCOL or not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id) or package.name != job_id:
        raise ValueError("invalid postprocess package identity")
    if mode not in {"latentsync", "seedvr2"}:
        raise ValueError("unsupported postprocess mode")
    hashes = job.get("input_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("postprocess package has no input hashes")
    for relative, expected in hashes.items():
        asset = (package / relative).resolve()
        if not asset.is_relative_to(package) or not asset.is_file() or sha256_file(asset) != expected:
            raise ValueError(f"packaged postprocess input failed hash validation: {relative}")

    config = load_remote_config(root)
    connection = resolve_connection(config)
    existing = _worker(config, connection, "status", "--job-id", job_id, "--json")
    matches = [item for item in existing.get("jobs", []) if item.get("job_id") == job_id]
    if len(matches) > 1:
        raise FileExistsError("remote postprocess job_id exists in multiple states; refusing a new reservation")
    match = matches[0] if matches else None
    if match:
        if match.get("mode") != mode:
            raise FileExistsError("remote postprocess job_id is already registered for a different mode")
        if match.get("request_sha256") != sha256_file(job_file):
            raise FileExistsError("remote postprocess job_id exists with a different package")
    # Inbox entries are resumable. Upload a fresh uniquely named stage and let
    # the locked worker verify it matches the queued request before reuse.
    if match and match.get("location") != "inbox" and not (match.get("location") is None and match.get("status") == "QUEUED"):
        return {**match, "existing": True}
    ready = postprocess_doctor(root, mode)
    if ready.get("status") != "READY":
        raise RuntimeError(f"{mode} worker is not ready for GPU submission: {ready.get('status', 'UNKNOWN')}")
    if on_reserve is None:
        raise RuntimeError("postprocess submission requires an atomic local budget reservation")
    reservation = on_reserve(job)
    if not isinstance(reservation, dict):
        raise ValueError("local postprocess reservation did not return a budget")
    shot_budget = float(reservation.get("remaining_gpu_minutes_per_shot", 0.0))
    episode_budget = float(reservation.get("remaining_gpu_minutes_per_episode", 0.0))
    if not 0 < shot_budget <= 15 or not 0 < episode_budget <= 300:
        raise ValueError("local postprocess reservation returned an invalid GPU budget")
    job.setdefault("budget", {}).update({
        "remaining_gpu_minutes_per_shot": shot_budget,
        "remaining_gpu_minutes_per_episode": episode_budget,
        "max_gpu_minutes_per_shot": 15.0,
    })
    temporary_job = job_file.with_name(f".{job_file.name}.{uuid.uuid4().hex}.tmp")
    temporary_job.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_job.replace(job_file)

    base = f"{config['remote']['root']}/postprocess/jobs/{mode}/inbox"
    staging = f"{base}/.{job_id}.upload-{uuid.uuid4().hex}"
    destination = f"{base}/{job_id}"
    made = subprocess.run(
        _ssh(connection, f"mkdir -p {shlex.quote(base)} && mkdir {shlex.quote(staging)}"),
        capture_output=True, text=True, timeout=30, check=False,
    )
    if made.returncode:
        raise RuntimeError(made.stderr.strip() or "unable to create unique postprocess upload directory")
    rsync = shutil.which("rsync")
    if rsync:
        command = [rsync, "-az", "-e", shlex.join(["ssh", "-p", str(connection["port"]), "-o", "BatchMode=yes"]), str(package) + "/", f"{connection['target']}:{staging}/"]
    else:
        command = [*_scp(connection, legacy_protocol=True), "-r", str(package) + "/.", f"{connection['target']}:{staging}/"]
    transfer = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if transfer.returncode:
        subprocess.run(_ssh(connection, f"rm -rf -- {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
        raise RuntimeError(transfer.stderr.strip()[-2000:] or "postprocess package upload failed")
    published = _worker(
        config, connection, "publish", "--staging-dir", staging,
        "--job-id", job_id, "--mode", mode, "--json",
    )
    if published.get("job_id") != job_id or published.get("mode") != mode:
        raise RuntimeError("remote postprocess publish response identity mismatch; preserving staging directory")
    if published.get("location") != "inbox":
        return {**published, "existing": True}
    response = _worker(config, connection, "submit", "--job-dir", destination, "--json")
    if response.get("job_id") != job_id or response.get("mode") != mode:
        raise RuntimeError("remote postprocess response identity mismatch")
    return response


def pull_postprocess(root: str | Path, job_id: str, destination: str | Path) -> Path:
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe postprocess job_id")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"preserve existing postprocess result directory: {destination}")
    status_result = postprocess_status(root, job_id=job_id)
    matches = [item for item in status_result.get("jobs", []) if item.get("job_id") == job_id]
    if len(matches) != 1 or matches[0].get("status") != "COMPLETE":
        raise ValueError("postprocess result can only be imported from one COMPLETE remote job")
    mode = matches[0].get("mode")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    remote_dir = f"{config['remote']['root']}/postprocess/jobs/{mode}/complete/{job_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        stage = Path(temporary)
        raw = stage / "remote"
        raw.mkdir()
        transfer = subprocess.run([*_scp(connection), "-r", f"{connection['target']}:{remote_dir}/.", str(raw)], capture_output=True, text=True, timeout=600, check=False)
        if transfer.returncode:
            raise RuntimeError(transfer.stderr.strip()[-1500:] or "postprocess result download failed")
        result_file = raw / "result.json"
        if result_file.is_symlink() or not result_file.is_file():
            raise FileNotFoundError("remote postprocess result.json is missing or unsafe")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("schema_version") != PROTOCOL or result.get("job_id") != job_id or result.get("mode") != mode or result.get("status") != "COMPLETE":
            raise ValueError("remote postprocess result identity/status mismatch")
        output_name = result.get("output")
        if not isinstance(output_name, str) or not output_name.startswith("work/") or ".." in Path(output_name).parts:
            raise ValueError("unsafe postprocess output path")
        output = raw / output_name
        if output.is_symlink() or not output.is_file() or sha256_file(output) != result.get("sha256"):
            raise ValueError("postprocess output is missing or SHA-256 mismatched")
        qc = deterministic_qc(output)
        if qc.get("status") != "PASS":
            raise ValueError("imported postprocess output failed deterministic QC")
        imported = stage / "validated"
        shutil.copytree(raw, imported, symlinks=False)
        (imported / "local_qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        imported.rename(destination)
    return destination


def cleanup_postprocess(root: str | Path, job_id: str, local_import: str | Path) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe postprocess job_id")
    imported = Path(local_import).resolve()
    result_file = imported / "result.json"
    if imported.is_symlink() or not imported.is_dir() or result_file.is_symlink() or not result_file.is_file():
        raise FileNotFoundError("verified local postprocess import is required before cleanup")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    output_name = result.get("output")
    if result.get("job_id") != job_id or result.get("status") != "COMPLETE" or not isinstance(output_name, str):
        raise ValueError("local postprocess import identity/status mismatch")
    output = (imported / output_name).resolve()
    if not output.is_relative_to(imported) or output.is_symlink() or not output.is_file() or sha256_file(output) != result.get("sha256"):
        raise ValueError("local imported postprocess output SHA-256 mismatch")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    remote = _worker(config, connection, "cleanup", "--job-id", job_id, "--sha256", result["sha256"], "--json")
    if remote.get("job_id") != job_id or remote.get("verified_sha256") != result["sha256"] or remote.get("status") != "CLEANED":
        raise RuntimeError("remote postprocess cleanup did not verify imported result hash")
    return remote
