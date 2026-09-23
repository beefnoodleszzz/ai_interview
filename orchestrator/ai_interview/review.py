"""Manual sign-off gate for final delivery after deterministic media QC."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .hashing import sha256_file
from .manifest import load_manifest
from .media import final_master_qc
from .state import _atomic_save

FINAL_CHECKS = {
    "script", "voice_acting", "asr_text", "character_continuity",
    "studio_continuity", "lip_sync", "reaction_timing", "room_tone",
    "subtitles", "mobile_review", "ai_label_and_rights",
}


def validate_final_checklist(value: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    if set(value) != FINAL_CHECKS:
        missing, extra = sorted(FINAL_CHECKS - set(value)), sorted(set(value) - FINAL_CHECKS)
        raise ValueError(f"final checklist keys mismatch; missing={missing}, extra={extra}")
    normalized: dict[str, dict[str, str]] = {}
    for name, entry in value.items():
        if not isinstance(entry, Mapping) or str(entry.get("status", "")).upper() != "PASS":
            raise ValueError(f"final checklist item {name} must be manually marked PASS")
        notes = entry.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError(f"final checklist item {name} needs review notes")
        normalized[name] = {"status": "PASS", "notes": notes.strip()}
    return normalized


def approve_final(
    manifest_path: str | Path,
    final_path: str | Path,
    checklist: Mapping[str, Any],
    selected_by: str,
    *,
    expected_duration: float,
    fps: int = 24,
) -> dict[str, Any]:
    if not selected_by.strip():
        raise ValueError("selected_by is required for final sign-off")
    checks = validate_final_checklist(checklist)
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    if not isinstance(manifest.get("edit_lock"), dict):
        raise ValueError("final approval requires a locked edit")
    raw_path = Path(final_path)
    if raw_path.is_symlink():
        raise ValueError("final master cannot be a symbolic link")
    path = raw_path.resolve()
    if not path.is_relative_to(root / "09_final") or not path.is_file():
        raise FileNotFoundError("final master must be an existing file inside episode 09_final/")
    qc = final_master_qc(path, expected_duration=expected_duration, fps=fps)
    if qc["status"] != "PASS":
        failed = [item["name"] for item in qc["checks"] if item["status"] != "PASS"]
        raise ValueError("deterministic final QC failed: " + ", ".join(failed))
    record = {
        "path": path.relative_to(root).as_posix(), "sha256": sha256_file(path),
        "reviewed_by": selected_by.strip(), "reviewed_at": datetime.now(UTC).isoformat(),
        "checklist": checks, "deterministic_qc": qc,
    }
    existing = manifest.get("final_review")
    if existing:
        if existing.get("path") == record["path"] and existing.get("sha256") == record["sha256"] and existing.get("reviewed_by") == record["reviewed_by"] and existing.get("checklist") == checks:
            return existing
        raise FileExistsError("final review already exists; create a new final revision to replace it")
    manifest["final_review"] = record
    manifest["status"] = "DONE"
    for shot in manifest["shots"]:
        shot["status"] = "DONE"
    _atomic_save(manifest_path, manifest)
    return record


def load_checklist(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("final checklist JSON must be an object")
    return value
