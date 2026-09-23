#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/root/autodl-tmp/ai_interview")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    usage = shutil.disk_usage(ROOT)
    models = []
    for path in sorted((ROOT / "models").rglob("*")):
        if path.is_file() and not path.is_symlink():
            models.append({
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path) if path.suffix in {".safetensors", ".pth", ".pt"} else None,
            })
    report = {
        "schema_version": "ai-interview-inventory-v1",
        "persistent_mount": os.path.ismount("/root/autodl-tmp"),
        "storage": {"total": usage.total, "used": usage.used, "free": usage.free},
        "models": models,
    }
    destination = ROOT / "reports" / "inventory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
