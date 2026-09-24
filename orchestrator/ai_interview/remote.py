from __future__ import annotations

import json
import ipaddress
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .config import load_yaml
from .media import deterministic_qc
from .package import sha256_file

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")


def load_remote_config(root: str | Path) -> dict[str, Any]:
    project_root = Path(root).resolve()
    value = load_yaml(project_root / "config" / "remote_h3.yaml")
    if value.get("schema_version") != "ai-interview-remote-h3-v1":
        raise ValueError("unsupported remote H3 config schema")
    if value.get("comfyui", {}).get("endpoint") != "http://127.0.0.1:8188":
        raise ValueError("ComfyUI must remain on remote localhost")
    remote_root = value.get("remote", {}).get("root")
    if not isinstance(remote_root, str) or not remote_root.startswith("/") or ".." in Path(remote_root).parts:
        raise ValueError("remote root must be a safe absolute path")
    value["_project_root"] = str(project_root)
    return value


def _dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _connection_from_command(command: str) -> dict[str, Any]:
    tokens = shlex.split(command)
    if not tokens or Path(tokens[0]).name != "ssh":
        raise ValueError("AUTODL_COMMAND must start with ssh")
    port = 22
    target: str | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-p", "-l", "-i", "-o", "-F", "-J"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"SSH option {token} is missing its value")
            if token == "-p":
                port = int(tokens[index + 1])
            elif token == "-l":
                # Normalized below when the host token is found.
                pass
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        target = token
        break
    if not target:
        raise ValueError("AUTODL_COMMAND does not contain an SSH target")
    login_user = None
    for position, token in enumerate(tokens[:-1]):
        if token == "-l":
            login_user = tokens[position + 1]
    if "@" in target:
        user, host = target.rsplit("@", 1)
    else:
        user, host = login_user or "root", target
    return {"host": host, "user": user, "port": port}


def resolve_connection(config: Mapping[str, Any]) -> dict[str, Any]:
    remote = config["remote"]
    dotenv_name = str(remote.get("dotenv_file", ".env.local"))
    dotenv = _dotenv(Path(str(config.get("_project_root", "."))) / dotenv_name)
    command_env = str(remote.get("command_env", "AUTODL_COMMAND"))
    command = os.environ.get(command_env) or dotenv.get(command_env)
    if command:
        values = _connection_from_command(command)
        if not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", str(values["host"])):
            raise ValueError("unsupported remote host")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", str(values["user"])):
            raise ValueError("unsupported remote user")
        if not 1 <= int(values["port"]) <= 65535:
            raise ValueError("SSH port must be 1-65535")
        values["target"] = f"{values['user']}@{values['host']}"
        return values
    values: dict[str, Any] = {}
    missing: list[str] = []
    for key, default in (("host", None), ("user", None), ("port", "22")):
        env_name = remote.get(f"{key}_env")
        value = os.environ.get(str(env_name), default) if env_name else None
        if not value:
            missing.append(str(env_name or key))
        else:
            values[key] = value
    if missing:
        raise RuntimeError("missing remote connection environment: " + ", ".join(missing))
    if not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", str(values["host"])):
        raise ValueError("unsupported remote host")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", str(values["user"])):
        raise ValueError("unsupported remote user")
    port = int(values["port"])
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be 1-65535")
    values.update(port=port, target=f"{values['user']}@{values['host']}")
    return values


def _ssh(connection: Mapping[str, Any], command: str) -> list[str]:
    return [
        "ssh", "-p", str(connection["port"]), "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20", "-o", "ConnectionAttempts=1",
        *_direct_host_options(connection),
        connection["target"], command,
    ]


def _direct_host_options(connection: Mapping[str, Any]) -> list[str]:
    """Use a session-only real IP when a local proxy's fake DNS breaks SSH."""
    address = os.environ.get("AI_INTERVIEW_H3_SSH_CONNECT_IP")
    if not address:
        return []
    ipaddress.ip_address(address)
    alias = f"[{connection['host']}]:{connection['port']}"
    return ["-o", f"HostName={address}", "-o", f"HostKeyAlias={alias}",
            "-o", "StrictHostKeyChecking=yes"]


def _publish_remote_directory(
    connection: Mapping[str, Any], staging: str, destination: str, queue_lock: str, label: str,
) -> None:
    guarded = (
        f"if test -e {shlex.quote(destination)} || test -L {shlex.quote(destination)}; "
        f"then exit 73; fi; mv -T -- {shlex.quote(staging)} {shlex.quote(destination)}"
    )
    command = f"flock -x {shlex.quote(queue_lock)} sh -c {shlex.quote(guarded)}"
    result = subprocess.run(_ssh(connection, command), capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"remote {label} job id already exists; refusing overwrite")


def _scp(connection: Mapping[str, Any], *, legacy_protocol: bool = False) -> list[str]:
    # AutoDL's SFTP upload path acknowledged PUTs but left created files at 0 bytes.
    # Legacy SCP is required for writes; downloads continue using modern SFTP.
    protocol = ["-O"] if legacy_protocol else []
    return ["scp", *protocol, "-P", str(connection["port"]), "-o", "BatchMode=yes",
            *_direct_host_options(connection)]


def _worker(
    config: Mapping[str, Any],
    connection: Mapping[str, Any],
    arguments: list[str],
    timeout: int = 180,
    *,
    accept_nonzero_json: bool = False,
    expected_schema: str = "ai-interview-h3-remote-v1",
    entrypoint: str | None = None,
) -> dict[str, Any]:
    runtime = config["remote"]["root"]
    python = config.get("worker_python", "/root/miniconda3/bin/python")
    entry = entrypoint or config.get("worker_entrypoint", "worker.py")
    command = f"cd {shlex.quote(runtime + '/worker')} && PYTHONPATH=. {shlex.join([python, entry, *arguments])}"
    result = subprocess.run(_ssh(connection, command), capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode and not accept_nonzero_json:
        raise RuntimeError(result.stderr.strip()[-2000:] or "remote worker command failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("remote worker returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != expected_schema:
        raise RuntimeError("remote worker returned an incompatible response")
    return value


def doctor(root: str | Path) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    try:
        config = load_remote_config(root)
        checks.append({"name": "configuration", "status": "PASS", "detail": "remote localhost contract valid"})
        connection = resolve_connection(config)
    except RuntimeError as exc:
        checks.append({"name": "SSH configuration", "status": "BLOCKED", "detail": str(exc)})
        return {"status": "BLOCKED", "checks": checks}
    except (OSError, ValueError) as exc:
        checks.append({"name": "configuration", "status": "FAIL", "detail": str(exc)})
        return {"status": "FAIL", "checks": checks}
    try:
        result = subprocess.run(_ssh(connection, "true"), capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            checks.append({"name": "SSH", "status": "FAIL", "detail": result.stderr.strip()[-500:]})
            return {"status": "FAIL", "checks": checks}
        checks.append({"name": "SSH", "status": "PASS", "detail": connection["target"]})
        response = _worker(
            config,
            connection,
            ["doctor", "--json"],
            timeout=60,
            accept_nonzero_json=True,
        )
        worker_status = response.get("status")
        configured = worker_status in {"READY", "CONFIGURED_NO_GPU"}
        checks.append({
            "name": "H3 worker",
            "status": "PASS" if configured else "FAIL",
            "detail": f"{worker_status}: {response.get('checks', {})}",
        })
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        checks.append({"name": "remote runtime", "status": "FAIL", "detail": str(exc)})
    return {"status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL", "checks": checks}


_REMOTE_AUDIT_PATH_HELPER = '''def _display_audit_path(path, root):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
'''


def require_live_gpu(root: str | Path) -> dict[str, Any]:
    """Refuse every remote inference submission unless a live GPU is allocated."""
    config = load_remote_config(root)
    connection = resolve_connection(config)
    result = _worker(config, connection, ["doctor", "--json"], timeout=60, accept_nonzero_json=True)
    if result.get("status") != "READY":
        raise RuntimeError(
            "GPU submission refused: AutoDL worker must report READY with an allocated GPU; "
            f"current status is {result.get('status', 'UNKNOWN')}"
        )
    return result


def component_worker_doctor(
    root: str | Path, entrypoint: str, expected_schema: str,
) -> dict[str, Any]:
    """Run an isolated worker's read-only doctor without requiring H3/ComfyUI."""
    config = load_remote_config(root)
    connection = resolve_connection(config)
    return _worker(
        config,
        connection,
        ["doctor", "--json"],
        timeout=120,
        accept_nonzero_json=True,
        expected_schema=expected_schema,
        entrypoint=entrypoint,
    )


def require_worker_ready(
    root: str | Path, entrypoint: str, expected_schema: str, component: str,
) -> dict[str, Any]:
    """Gate an isolated worker using its own doctor, without requiring H3/ComfyUI."""
    result = component_worker_doctor(root, entrypoint, expected_schema)
    if result.get("status") != "READY":
        raise RuntimeError(
            f"GPU submission refused: {component} worker must report READY; "
            f"current status is {result.get('status', 'UNKNOWN')}"
        )
    return result


def audit_remote(root: str | Path) -> dict[str, Any]:
    """Read-only AutoDL inventory for disk, partial downloads, jobs, processes and links."""
    config = load_remote_config(root)
    connection = resolve_connection(config)
    runtime = config["remote"]["root"]
    python = str(config.get("worker_python", "/root/miniconda3/bin/python"))
    script = r'''import json, os, pathlib, shutil, subprocess
root = pathlib.Path(os.environ.get("AI_INTERVIEW_RUNTIME", "/root/autodl-tmp/ai_interview"))
''' + _REMOTE_AUDIT_PATH_HELPER + r'''
usage = shutil.disk_usage(root)
jobs = {}
for family in ("jobs", "voice/jobs", "voice_design/jobs", "postprocess/jobs/latentsync", "postprocess/jobs/seedvr2"):
    for name in ("inbox", "running", "work", "complete", "failed"):
        path = root / family / name
        jobs[f"{family}/{name}"] = sorted(p.name for p in path.iterdir()) if path.is_dir() else []
partial = []
home_cache = pathlib.Path.home() / ".cache"
for base in (root / "models", root / "staging", root / "cache", home_cache / "pip", home_cache / "huggingface", home_cache / "uv", pathlib.Path("/root/miniconda3/pkgs")):
    if base.exists():
        for directory, _, files in os.walk(base):
            for name in files:
                if name.endswith((".part", ".partial", ".incomplete", ".download", ".tmp")):
                    path = pathlib.Path(directory) / name
                    try: partial.append({"path": _display_audit_path(path, root), "bytes": path.stat().st_size})
                    except OSError: pass
models = []
base = root / "models"
if base.is_dir():
    for path in sorted(base.rglob("*")):
        if path.is_file() and not path.is_symlink():
            try: models.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
            except OSError: pass
process = subprocess.run(["ps", "-eo", "pid=,etime=,args="], capture_output=True, text=True, check=False)
terms = ("index-tts", " worker.py", "voice_worker.py", "voice_design_worker.py", "postprocess_worker.py", "ComfyUI/main.py", "remote_deploy_models.sh", "remote_install.sh", "remote_install_asr.sh", "huggingface")
active = [line.strip() for line in process.stdout.splitlines() if "ai-interview-remote-audit-v1" not in line and any(term in line for term in terms)]
broken = []
for base in (root / "apps", root / "models"):
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_symlink() and not path.exists(): broken.append(str(path.relative_to(root)))
deployment_artifacts = []
worker = root / "worker"
if worker.is_dir():
    for pattern in (".upload-*.tar.gz", "*.deploy-tmp"):
        for path in worker.rglob(pattern):
            if path.is_file() and not path.is_symlink():
                try: deployment_artifacts.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
                except OSError: pass
caches = {}
cache_paths = {
    "project_pip": root / "cache" / "pip",
    "project_uv": root / "cache" / "xdg" / "uv",
    "wheelhouse": root / "cache" / "wheelhouse",
    "project_huggingface": root / "cache" / "huggingface",
    "staging": root / "staging",
    "user_pip": home_cache / "pip",
    "user_huggingface": home_cache / "huggingface",
    "user_uv": home_cache / "uv",
    "conda_packages": pathlib.Path("/root/miniconda3/pkgs"),
}
for name, path in cache_paths.items():
    if path.exists():
        result = subprocess.run(["du", "-sx", "-B1", str(path)], capture_output=True, text=True, check=False)
        caches[name] = int(result.stdout.split()[0]) if result.returncode == 0 and result.stdout.split() else None
print(json.dumps({"schema_version":"ai-interview-remote-audit-v1", "root":str(root), "storage":{"total":usage.total,"used":usage.used,"free":usage.free}, "jobs":jobs, "active_processes":active, "partial_downloads":partial, "broken_symlinks":broken, "deployment_artifacts":deployment_artifacts, "cache_bytes":caches, "models":models}, ensure_ascii=False))
'''
    command = f"cd {shlex.quote(runtime)} && AI_INTERVIEW_RUNTIME={shlex.quote(runtime)} {shlex.quote(python)} -c {shlex.quote(script)}"
    try:
        ssh_command = _ssh(connection, command)
        result = subprocess.run(ssh_command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "schema_version": "ai-interview-remote-audit-v1",
            "status": "UNREACHABLE",
            "error": str(exc).replace(str(connection.get("target", "")), "<remote>"),
        }
    if result.returncode:
        return {
            "schema_version": "ai-interview-remote-audit-v1",
            "status": "UNREACHABLE",
            "error": (result.stderr.strip()[-1500:] or "remote read-only audit failed").replace(str(connection.get("target", "")), "<remote>"),
        }
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"schema_version": "ai-interview-remote-audit-v1", "status": "INVALID_RESPONSE", "error": str(exc)}
    if not isinstance(report, dict) or report.get("schema_version") != "ai-interview-remote-audit-v1":
        return {"schema_version": "ai-interview-remote-audit-v1", "status": "INVALID_RESPONSE", "error": "incompatible response"}
    report["status"] = "PASS"
    return report


def submit(package: str | Path, root: str | Path, on_submit: Any | None = None) -> dict[str, Any]:
    package = Path(package).resolve()
    job_path = package / "job.json"
    if not package.is_dir() or not job_path.is_file():
        raise FileNotFoundError("H3 package must contain job.json")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id) or package.name != job_id:
        raise ValueError("package directory and safe job_id must match")
    for relative, digest in job.get("input_sha256", {}).items():
        asset = (package / relative).resolve()
        if not asset.is_relative_to(package) or not asset.is_file() or sha256_file(asset) != digest:
            raise ValueError(f"packaged asset failed hash validation: {relative}")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    destination = f"{config['remote']['root']}/jobs/inbox/{job_id}"
    existing = _worker(config, connection, ["status", "--job-id", job_id, "--json"])
    match = next((item for item in existing.get("jobs", []) if item.get("job_id") == job_id), None)
    if match:
        if match.get("location") == "inbox":
            if match.get("request_sha256") != sha256_file(job_path):
                raise FileExistsError("remote H3 inbox job_id exists with a different package")
            require_live_gpu(root)
            response = _worker(config, connection, ["submit", "--job-dir", destination, "--json"])
            if response.get("job_id") != job_id:
                raise RuntimeError("remote H3 inbox resume response job_id mismatch")
            return response
        return {**match, "schema_version": existing["schema_version"], "existing": True}
    require_live_gpu(root)
    if on_submit:
        reservation = on_submit()
        shot_state = reservation.get("shots", {}).get(job.get("shot_id"), {}) if isinstance(reservation, dict) else {}
        amount = float(shot_state.get("h3_job_reserved_gpu_minutes", 0.0))
        if not 0 < amount <= 15 or not 0 < amount <= float(reservation.get("gpu_minutes_limit", 0.0)):
            raise ValueError("H3 submission did not receive a valid atomic GPU-minute reservation")
        job.setdefault("budget", {}).update({
            "remaining_gpu_minutes_per_shot": amount,
            "remaining_gpu_minutes_per_episode": amount,
        })
        temporary_job = job_path.with_name(f".{job_path.name}.{uuid.uuid4().hex}.tmp")
        temporary_job.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_job.replace(job_path)
    staging = f"{config['remote']['root']}/jobs/inbox/.{job_id}.upload-{uuid.uuid4().hex}"
    mkdir = subprocess.run(_ssh(connection, f"mkdir -p {shlex.quote(config['remote']['root'] + '/jobs/inbox')} && mkdir {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
    if mkdir.returncode:
        raise RuntimeError(mkdir.stderr.strip() or "unable to create a unique remote upload directory")
    rsync = shutil.which("rsync")
    if rsync:
        command = [rsync, "-az", "-e", shlex.join(["ssh", "-p", str(connection["port"]), "-o", "BatchMode=yes", *_direct_host_options(connection)]), str(package) + "/", f"{connection['target']}:{staging}/"]
    else:
        command = [*_scp(connection, legacy_protocol=True), "-r", str(package) + "/.", f"{connection['target']}:{staging}/"]
    transfer = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if transfer.returncode:
        subprocess.run(_ssh(connection, f"rm -rf -- {shlex.quote(staging)}"), capture_output=True, text=True, timeout=30, check=False)
        raise RuntimeError(transfer.stderr.strip()[-2000:] or "H3 package upload failed")
    published = _worker(config, connection, [
        "publish", "--staging-dir", staging, "--job-id", job_id, "--json",
    ])
    if published.get("job_id") != job_id:
        raise RuntimeError("H3 worker publish response job_id mismatch; preserving staging directory")
    if published.get("location") != "inbox":
        return {**published, "existing": True}
    response = _worker(config, connection, ["submit", "--job-dir", destination, "--json"])
    if response.get("job_id") != job_id:
        raise RuntimeError("remote response job_id mismatch")
    return response


def status(root: str | Path, episode_id: str, shot_id: str | None = None) -> dict[str, Any]:
    config = load_remote_config(root)
    connection = resolve_connection(config)
    arguments = ["status", "--episode-id", episode_id]
    if shot_id:
        arguments += ["--shot-id", shot_id]
    arguments.append("--json")
    return _worker(config, connection, arguments)


def resume(root: str | Path, job_id: str) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe job_id")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    return _worker(config, connection, ["resume", job_id, "--json"])


def cleanup_completed(root: str | Path, job_id: str, local_import: str | Path) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe job_id")
    local_import = Path(local_import).resolve()
    result_file = local_import / "result.json"
    if not result_file.is_file() or result_file.is_symlink():
        raise FileNotFoundError("verified local import must contain result.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if result.get("schema_version") != "ai-interview-h3-remote-v1" or result.get("job_id") != job_id or result.get("status") != "COMPLETE":
        raise ValueError("local import result identity/status does not match the completed job")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("verified local import must contain at least one QC'd candidate")
    for candidate in candidates:
        filename = candidate.get("filename")
        metadata_name = candidate.get("metadata_file")
        if not isinstance(filename, str) or not SAFE_NAME.fullmatch(filename) or not isinstance(metadata_name, str) or not SAFE_NAME.fullmatch(metadata_name):
            raise ValueError("local import candidate filenames are unsafe")
        media = local_import / filename
        metadata_path = local_import / metadata_name
        qc_path = local_import / f"{Path(filename).stem}.local_qc.json"
        if not all(path.is_file() and not path.is_symlink() for path in (media, metadata_path, qc_path)):
            raise FileNotFoundError(f"local import is missing verified candidate assets for {filename}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if metadata.get("job_id") != job_id or metadata.get("sha256") != sha256_file(media) or qc.get("status") != "PASS":
            raise ValueError(f"local candidate hash/QC does not pass for {filename}")
    digest = sha256_file(result_file)
    config = load_remote_config(root)
    connection = resolve_connection(config)
    return _worker(config, connection, [
        "cleanup", "--job-id", job_id, "--confirm-result-sha256", digest,
        "--local-import-verified", "--json",
    ])


def pull(root: str | Path, job_path: str | Path, destination: str | Path) -> Path:
    root, job_path, destination = Path(root).resolve(), Path(job_path).resolve(), Path(destination).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id):
        raise ValueError("unsafe job_id")
    if destination.exists():
        raise FileExistsError(f"preserve existing result directory: {destination}")
    config = load_remote_config(root)
    connection = resolve_connection(config)
    remote_dir = f"{config['remote']['root']}/jobs/complete/{job_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        first = subprocess.run([*_scp(connection), f"{connection['target']}:{remote_dir}/result.json", str(temp / "result.json")], capture_output=True, text=True, timeout=120, check=False)
        if first.returncode:
            raise RuntimeError(first.stderr.strip() or "result.json download failed")
        result = json.loads((temp / "result.json").read_text(encoding="utf-8"))
        if result.get("schema_version") != "ai-interview-h3-remote-v1" or result.get("job_id") != job_id or result.get("status") != "COMPLETE":
            raise ValueError("remote result identity/status mismatch")
        for candidate in result.get("candidates", []):
            for key in ("filename", "metadata_file"):
                name = candidate.get(key)
                if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or Path(name).name != name:
                    raise ValueError(f"unsafe result {key}")
                fetched = subprocess.run([*_scp(connection), f"{connection['target']}:{remote_dir}/{name}", str(temp / name)], capture_output=True, text=True, timeout=900, check=False)
                if fetched.returncode:
                    raise RuntimeError(fetched.stderr.strip() or f"download failed: {name}")
        for candidate in result.get("candidates", []):
            video = temp / candidate["filename"]
            metadata = json.loads((temp / candidate["metadata_file"]).read_text(encoding="utf-8"))
            if metadata.get("filename") != video.name or sha256_file(video) != metadata.get("sha256"):
                raise ValueError(f"candidate hash/identity mismatch: {video.name}")
            qc = deterministic_qc(video)
            if qc["status"] != "PASS":
                raise ValueError(f"candidate failed deterministic local QC: {video.name}")
            (temp / f"{video.stem}.local_qc.json").write_text(
                json.dumps(qc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        temp.rename(destination)
    return destination
