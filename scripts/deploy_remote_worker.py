#!/usr/bin/env python3
"""Deploy the project worker code and fixed workflows without deleting remote data."""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from ai_interview.remote import _scp, _ssh, load_remote_config, resolve_connection


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    config = load_remote_config(project)
    connection = resolve_connection(config)
    root = config["remote"]["root"]
    mappings: list[tuple[Path, str]] = [
        (project / "remote" / "worker.py", "worker/worker.py"),
        (project / "remote" / "voice_worker.py", "worker/voice_worker.py"),
        (project / "remote" / "voice_asr.py", "worker/voice_asr.py"),
        (project / "remote" / "voice_design_worker.py", "worker/voice_design_worker.py"),
    ]
    mappings.extend((path, f"worker/ai_interview_h3_worker/{path.name}") for path in sorted((project / "remote" / "ai_interview_h3_worker").glob("*.py")))
    mappings.extend((path, f"workflows/{path.name}") for path in sorted((project / "remote" / "workflows").glob("*.json")))
    for source, _ in mappings:
        if not source.is_file():
            raise FileNotFoundError(source)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"ai-interview-worker-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="ai-interview-worker-") as temp:
        archive = Path(temp) / archive_name
        with tarfile.open(archive, "w:gz") as bundle:
            for index, (source, destination) in enumerate(mappings):
                bundle.add(source, arcname=f"payload/{index}")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        upload_path = f"{root}/worker/.upload-{stamp}.tar.gz"
        upload = subprocess.run([*_scp(connection), str(archive), f"{connection['target']}:{upload_path}"], capture_output=True, text=True, timeout=300, check=False)
        if upload.returncode:
            raise RuntimeError(upload.stderr.strip()[-1500:] or "worker package upload failed")
        source_map = ",\n".join(f"    ({index}, {destination!r})" for index, (_, destination) in enumerate(mappings))
        remote_script = f'''import hashlib, os, pathlib, shutil, sys, tarfile, tempfile
archive = pathlib.Path(sys.argv[1]); root = pathlib.Path(sys.argv[2]); backup = root / "worker" / "backups" / "deploy-{stamp}"
expected = {digest!r}
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
if digest != expected: raise SystemExit("uploaded worker archive SHA-256 mismatch")
mapping = [\n{source_map}\n]
stage = pathlib.Path(tempfile.mkdtemp(prefix=".worker-deploy-", dir=root / "worker"))
try:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or not member.name.startswith("payload/") or "/../" in member.name:
                raise SystemExit("unsafe worker archive member")
        bundle.extractall(stage, filter="data")
    for index, relative in mapping:
        source = stage / "payload" / str(index)
        data = source.read_bytes()
        if relative.endswith(".py"): compile(data, relative, "exec")
    for index, relative in mapping:
        source = stage / "payload" / str(index)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() == source.read_bytes(): continue
        if target.exists():
            saved = backup / relative; saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, saved)
        temporary = target.with_name(target.name + ".deploy-tmp")
        shutil.copy2(source, temporary); os.replace(temporary, target)
finally:
    shutil.rmtree(stage, ignore_errors=True); archive.unlink(missing_ok=True)
print("deployed " + str(len(mapping)) + " worker/workflow files; previous versions backed up under " + str(backup))
'''
        worker_python = str(config.get("worker_python", "/root/miniconda3/bin/python"))
        command = f"{shlex.quote(worker_python)} -c {shlex.quote(remote_script)} {shlex.quote(upload_path)} {shlex.quote(root)}"
        result = subprocess.run(_ssh(connection, command), capture_output=True, text=True, timeout=180, check=False)
        if result.returncode:
            subprocess.run(_ssh(connection, f"rm -f -- {shlex.quote(upload_path)}"), capture_output=True, text=True, timeout=30, check=False)
            raise RuntimeError(result.stderr.strip()[-2000:] or "remote worker deployment failed")
        print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
