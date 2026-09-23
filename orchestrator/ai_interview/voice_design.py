"""Human-approved, immutable voice reference creation from Qwen candidates."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .manifest import load_manifest
from .media import duration, probe
from .state import _atomic_save


def freeze_voice_reference(
    manifest_path: str | Path,
    character_id: str,
    metadata_path: str | Path,
    selected_by: str,
) -> dict[str, Any]:
    if not selected_by.strip():
        raise ValueError("selected_by is required after listening to the Qwen candidates")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    character = manifest["characters"].get(character_id)
    if not isinstance(character, dict):
        raise ValueError(f"unknown character: {character_id}")
    metadata_path = Path(metadata_path).resolve()
    if not metadata_path.is_relative_to(root) or not metadata_path.is_file():
        raise FileNotFoundError("voice-design metadata must be inside the episode")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema_version") != "ai-interview-voice-design-v1" or metadata.get("character_id") != character_id:
        raise ValueError("voice-design metadata does not match the requested character")
    filename = metadata.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("voice-design metadata must include a safe WAV filename")
    source = metadata_path.parent / filename
    if not source.is_file() or source.is_symlink() or sha256_file(source) != metadata.get("sha256"):
        raise ValueError("voice-design candidate is missing or its SHA-256 does not match")
    source_seconds = duration(source)
    if not 10 <= source_seconds <= 30:
        raise ValueError(f"frozen voice reference must be 10-30 seconds; candidate is {source_seconds:.3f}s")
    desired = f"02_refs/voices/{character['voice_id']}_r{int(metadata.get('revision', 1)):02d}.wav"
    current = character.get("voice_reference")
    revision = int(metadata.get("revision", 1))
    previous_revision = int(character.get("voice_reference_revision", 0))
    if current and current != desired:
        expected_pattern = f"02_refs/voices/{character['voice_id']}_r"
        if not isinstance(current, str) or not current.startswith(expected_pattern) or not current.endswith(".wav") or revision <= previous_revision:
            raise FileExistsError("a voice reference is already frozen; use a higher voice_design_revision and preserve the previous file")
    target = root / desired
    provenance = {
        "schema_version": "ai-interview-voice-design-v1", "job_id": metadata["job_id"],
        "character_id": character_id, "voice_id": character["voice_id"],
        "candidate_id": metadata.get("candidate_id"), "source": source.relative_to(root).as_posix(),
        "source_sha256": metadata["sha256"], "source_duration_sec": round(source_seconds, 4),
        "selected_by": selected_by.strip(), "selected_at": datetime.now(UTC).isoformat(),
        "evidence": "owner_listened_and_selected; ASR is not voice-quality approval",
    }
    sidecar = target.with_suffix(target.suffix + ".json")
    if target.exists() or sidecar.exists():
        if target.is_file() and sidecar.is_file():
            existing = json.loads(sidecar.read_text(encoding="utf-8"))
            if existing.get("source_sha256") == metadata["sha256"] and existing.get("selected_by") == selected_by.strip() and existing.get("output_sha256") == sha256_file(target):
                return {"path": desired, "sha256": existing["output_sha256"], "sample_rate": 48000, "channels": 1, "duration_sec": duration(target)}
        raise FileExistsError(f"Preserve existing non-identical voice reference: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.stem}.", dir=target.parent) as temporary:
        staged = Path(temporary) / target.name
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-n", "-i", str(source), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(staged)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode or not staged.is_file():
            raise RuntimeError(result.stderr.strip()[-1500:] or "ffmpeg failed to normalize voice reference")
        media = probe(staged)
        audio = next((item for item in media.get("streams", []) if item.get("codec_type") == "audio"), {})
        if int(audio.get("sample_rate", 0)) != 48000 or int(audio.get("channels", 0)) != 1:
            raise RuntimeError("normalized voice reference must be 48 kHz mono")
        staged.replace(target)
    output_hash = sha256_file(target)
    provenance.update({"output_sha256": output_hash, "sample_rate": 48000, "channels": 1, "duration_sec": duration(target)})
    sidecar.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    character["voice_reference"] = desired
    character["voice_reference_frozen"] = True
    character["voice_reference_revision"] = revision
    character["voice_reference_sha256"] = output_hash
    character["voice_reference_selected_by"] = selected_by.strip()
    _atomic_save(manifest_path, manifest)
    return {"path": desired, "sha256": output_hash, "sample_rate": 48000, "channels": 1, "duration_sec": duration(target)}
