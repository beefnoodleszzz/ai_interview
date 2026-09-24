"""Snapshot irreplaceable project inputs outside the repository and rehearse restore.

This local copy is not an offsite backup. Pass an independent destination for
offsite storage, then verify that destination separately.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "AGENTS.md", "README.md", "pyproject.toml", "uv.lock",
    ".beads/issues.jsonl",
    "00_management", "docs", "schemas", "config", "templates", "scripts",
    "orchestrator", "remote", "tests", "characters", "sets",
    "episodes/_template",
)
EPISODE_INPUTS = ("episode.yaml", "00_script", "02_refs", "03_h3_prompts", "voice_design_results", "voice_jobs")
SKIP_PARTS = {"__pycache__", ".git", ".venv", "graphify-out"}
SKIP_SUFFIXES = {".pyc", ".mp4", ".mov", ".mkv", ".safetensors", ".ckpt", ".pt", ".pth"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect() -> list[tuple[str, bytes]]:
    files: dict[str, bytes] = {}
    sources = list(SOURCE_PATHS)
    episode_root = ROOT / "episodes"
    if episode_root.is_dir():
        for episode in sorted(episode_root.iterdir()):
            if episode.is_dir() and episode.name != "_template" and not episode.is_symlink():
                sources.extend(f"episodes/{episode.name}/{item}" for item in EPISODE_INPUTS)
                sources.extend(path.relative_to(ROOT).as_posix() for path in episode.glob("*.md") if path.is_file())
    for relative in sources:
        path = ROOT / relative
        if not path.exists():
            continue
        entries = [path] if path.is_file() else path.rglob("*")
        for entry in entries:
            if not entry.is_file() or entry.is_symlink():
                continue
            name = entry.relative_to(ROOT).as_posix()
            if any(part in SKIP_PARTS for part in entry.relative_to(ROOT).parts):
                continue
            if entry.name.startswith(".env") or entry.name == ".DS_Store" or entry.suffix.lower() in SKIP_SUFFIXES:
                continue
            files[name] = entry.read_bytes()
    return sorted(files.items())


def snapshot(destination: Path) -> dict[str, object]:
    destination = destination.expanduser().resolve()
    if destination == ROOT or destination.is_relative_to(ROOT):
        raise ValueError("snapshot destination must be outside the project")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive = destination / f"ai_interview_{stamp}.tar.gz"
    manifest_path = destination / f"ai_interview_{stamp}.json"
    files = collect()
    if not files:
        raise RuntimeError("no source files found")
    inventory = [{"path": name, "bytes": len(data), "sha256": sha256(data)} for name, data in files]
    with tarfile.open(archive, "x:gz") as tar:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
    with tempfile.TemporaryDirectory(prefix="ai_interview_restore_") as temp:
        restored = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if len(members) != len(inventory):
                raise RuntimeError("archive member count differs from inventory")
            for member, expected in zip(members, inventory, strict=True):
                relative = Path(member.name)
                if not member.isfile() or relative.is_absolute() or ".." in relative.parts or member.name != expected["path"]:
                    raise RuntimeError("archive contains an unsafe or unexpected member")
                source = tar.extractfile(member)
                if source is None:
                    raise RuntimeError(f"cannot restore {member.name}")
                target = restored / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                if target.stat().st_size != expected["bytes"] or sha256(target.read_bytes()) != expected["sha256"]:
                    raise RuntimeError(f"restored file failed hash check: {member.name}")
    report: dict[str, object] = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(ROOT),
        "archive": str(archive),
        "archive_sha256": sha256(archive.read_bytes()),
        "file_count": len(inventory),
        "restore_check": "PASS",
        "offsite_status": "UNVERIFIED",
        "files": inventory,
    }
    manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {key: report[key] for key in ("archive", "archive_sha256", "file_count", "restore_check", "offsite_status")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(snapshot(arguments.destination), ensure_ascii=False, indent=2))
