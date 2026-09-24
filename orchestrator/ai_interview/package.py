from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .config import write_json
from .hashing import sha256_file
from .manifest import find_shot, load_manifest
from .media import duration
from .prompt import validate_prompt
from .state import budget_snapshot


def _episode_file(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError(f"{label} is missing or unsafe: {value}")
    return path


def _copy_spec(episode_root: Path, shot: dict[str, Any]) -> tuple[list[tuple[str, Path]], dict[str, Any]]:
    files: list[tuple[str, Path]] = []
    inputs: dict[str, Any] = {
        "first_frame": None, "last_frame": None,
        "reference_images": [], "reference_videos": [], "reference_audio": [],
    }
    for field in ("first_frame", "last_frame"):
        value = shot["references"].get(field)
        if isinstance(value, str):
            source = _episode_file(episode_root, value, f"references.{field}")
            relative = f"assets/{field}{source.suffix.lower()}"
            files.append((relative, source))
            inputs[field] = relative
    mapping = (("images", "reference_images"), ("videos", "reference_videos"), ("audios", "reference_audio"))
    counts = {"images": 0, "videos": 0, "audios": 0}
    durations = {"videos": 0.0, "audios": 0.0}
    used: set[str] = set()
    for source_key, target_key in mapping:
        values = shot["references"].get(source_key, [])
        if source_key == "audios" and isinstance(shot.get("master_audio"), str):
            # A short dialogue master may need trailing silence to meet Ref2VA's
            # minimum reference length. Keep the timing authority unchanged.
            values = [shot.get("h3_reference_audio") or shot["master_audio"]]
        for index, value in enumerate(values, start=1):
            source = _episode_file(episode_root, value, f"references.{source_key}[{index - 1}]")
            suffix = source.suffix.lower()
            supported = {
                "images": {".png", ".jpg", ".jpeg", ".webp"},
                "videos": {".mp4", ".mov", ".mkv", ".webm"},
                "audios": {".wav", ".mp3", ".m4a", ".flac", ".ogg"},
            }
            if suffix not in supported[source_key]:
                raise ValueError(f"unsupported {source_key} reference format: {source.name}")
            filename = f"{source_key[:-1]}_{index:02d}{source.suffix.lower()}"
            if filename in used:
                raise ValueError(f"duplicate packaged filename: {filename}")
            used.add(filename)
            relative = f"assets/{filename}"
            files.append((relative, source))
            inputs[target_key].append(relative)
            counts[source_key] += 1
            if source_key in durations:
                seconds = duration(source)
                if not 2 <= seconds <= 15:
                    raise ValueError(f"each Ref2VA {source_key[:-1]} must be 2-15 seconds: {source.name} ({seconds:.3f}s)")
                durations[source_key] += seconds
    if counts["images"] > 9 or counts["videos"] > 3 or counts["audios"] > 3:
        raise ValueError("Ref2VA supports at most 9 images, 3 videos, and 3 audio references")
    if sum(counts.values()) > 12:
        raise ValueError("Ref2VA supports at most 12 mixed reference assets")
    if shot["h3_mode"] == "ref2va" and inputs["reference_audio"] and not (inputs["reference_images"] or inputs["reference_videos"]):
        raise ValueError("Ref2VA audio cannot be packaged without an image or video reference")
    if durations["audios"] > 15.05:
        raise ValueError("reference audio exceeds 15 seconds")
    if durations["videos"] > 15.05:
        raise ValueError("reference video exceeds 15 seconds")
    return files, inputs


def package_h3(manifest_path: str | Path, shot_id: str) -> Path:
    manifest_path = Path(manifest_path).resolve()
    episode_root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    shot = find_shot(manifest, shot_id)
    if shot["status"] not in {"READY_FOR_H3", "NEEDS_RETRY"}:
        raise ValueError(f"{shot_id} must be READY_FOR_H3 or NEEDS_RETRY before packaging")
    prompt_path = _episode_file(episode_root, shot.get("prompt", ""), "prompt")
    prompt = prompt_path.read_text(encoding="utf-8")
    beat_text = [beat["text"] for beat in manifest["beats"] if beat["id"] in shot.get("beat_ids", [])]
    prompt_errors = validate_prompt(shot["h3_mode"], prompt, beat_text)
    if prompt_errors:
        raise ValueError("Invalid H3 prompt:\n- " + "\n- ".join(prompt_errors))
    files, inputs = _copy_spec(episode_root, shot)
    revision = int(shot.get("job_revision", 1))
    job_id = f"{manifest['episode_id']}_{shot_id}_r{revision:02d}"
    job = {
        "schema_version": "ai-interview-h3-remote-v1",
        "job_id": job_id,
        "episode_id": manifest["episode_id"],
        "shot_id": shot_id,
        "mode": shot["h3_mode"],
        "candidate_count": int(shot.get("candidate_count", 3)),
        "edit_duration_sec": float(shot["edit_duration_sec"]),
        "generation_duration_sec": float(shot["generation_duration_sec"]),
        "duration_sec": float(shot["generation_duration_sec"]),
        "aspect_ratio": manifest["format"],
        "prompt": prompt,
        "prompt_artifact": {
            "path": str(PurePosixPath(shot["prompt"])),
            "sha256": sha256_file(prompt_path),
            "official_skill": {"skill_path": "skills/h3-prompt-writing"},
        },
        "inputs": inputs,
        "audio": {
            "generate_native_audio": True,
            "intent": shot.get("audio_intent", "quiet interview room ambience; dialogue follows supplied reference audio"),
            "narration_requested": False,
            "dialogue_requested": bool(beat_text),
        },
        "non_diegetic_music_allowed": False,
        "preserve": list(shot.get("preserve", [])),
        "avoid": list(shot.get("avoid", [])),
        "output": {"video": True, "native_audio": True},
        "input_sha256": {relative: sha256_file(source) for relative, source in files},
    }
    runtime = shot.get("runtime", {})
    shot_budget = float(manifest["retry_policy"]["max_gpu_minutes_per_shot"])
    episode_budget = float(manifest["retry_policy"]["max_gpu_minutes_per_episode"])
    episode_used = sum(float(item.get("runtime", {}).get("gpu_minutes", 0.0)) for item in manifest["shots"])
    job["budget"] = {
        "remaining_gpu_minutes_per_shot": max(0.0, shot_budget - float(runtime.get("gpu_minutes", 0.0))),
        "remaining_gpu_minutes_per_episode": max(0.0, episode_budget - episode_used),
    }
    destination = episode_root / "remote_jobs" / job_id
    if destination.exists():
        existing = destination / "job.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")) == job:
            return destination
        raise FileExistsError(f"Preserve existing non-identical job: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        for relative, source in files:
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256_file(target) != job["input_sha256"][relative]:
                raise IOError(f"asset changed while packaging: {source}")
        (temp / "prompt.txt").write_text(prompt, encoding="utf-8")
        write_json(job, temp / "job.json")
        temp.rename(destination)
    return destination


def package_voice(manifest_path: str | Path) -> Path:
    manifest_path = Path(manifest_path).resolve()
    episode_root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    lines = []
    files: list[tuple[str, Path]] = []
    reference_assets: dict[str, str] = {}
    beat_shots = {
        beat_id: shot for shot in manifest["shots"] for beat_id in shot.get("beat_ids", [])
    }
    for beat in manifest["beats"]:
        character = manifest["characters"][beat["speaker"]]
        reference_value = character.get("voice_reference")
        if not isinstance(reference_value, str):
            raise ValueError(f"{beat['speaker']} needs a frozen voice_reference")
        reference = _episode_file(episode_root, reference_value, f"characters.{beat['speaker']}.voice_reference")
        relative = reference_assets.get(beat["speaker"])
        if relative is None:
            relative = f"assets/voice_{beat['speaker']}{reference.suffix.lower()}"
            files.append((relative, reference))
            reference_assets[beat["speaker"]] = relative
        lines.append({
            "id": beat["id"], "speaker": beat["speaker"], "text": beat["text"],
            "emotion": beat.get("emotion", "neutral"), "voice_id": character["voice_id"],
            "voice_reference": relative, "voice_reference_sha256": sha256_file(reference),
            "language": beat.get("language", "ZH"),
            "shot_id": beat_shots.get(beat["id"], {}).get("id"),
            "pause_after": float(beat.get("pause_after", 0)),
        })
    if "voice_job_revision" in manifest:
        revision = int(manifest["voice_job_revision"])
    else:
        history = manifest.get("voice_jobs", {})
        parsed = []
        if isinstance(history, dict):
            for previous_id, previous in history.items():
                match = re.search(r"_voice_r(\d+)$", str(previous_id))
                if match:
                    parsed.append((int(match.group(1)), str(previous.get("status", "")).upper() if isinstance(previous, dict) else ""))
        latest_revision = max((revision for revision, _ in parsed), default=1)
        latest_status = next((status for revision, status in parsed if revision == latest_revision), "")
        revision = latest_revision + int(latest_status in {"FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"})
    job_id = f"{manifest['episode_id']}_voice_r{revision:02d}"
    job = {
        "schema_version": "ai-interview-voice-v1",
        "job_id": job_id,
        "episode_id": manifest["episode_id"],
        "revision": revision,
        "backend": "IndexTTS-2.5",
        "candidates_per_line": 3,
        "asr_required": True,
        "lines": lines,
        "input_sha256": {relative: sha256_file(source) for relative, source in files},
    }
    limits = manifest["retry_policy"]
    snapshot = budget_snapshot(manifest)
    job["budget"] = {
        "remaining_gpu_minutes_per_episode": snapshot["gpu_minutes_available"],
        "remaining_gpu_minutes_by_shot": {shot_id: values["gpu_minutes_available"] for shot_id, values in snapshot["shots"].items()},
    }
    destination = episode_root / "voice_jobs" / job_id
    if destination.exists():
        existing = destination / "job.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")) == job:
            return destination
        raise FileExistsError(f"Preserve existing non-identical voice job: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        for relative, source in files:
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256_file(target) != job["input_sha256"][relative]:
                raise IOError(f"voice reference changed while packaging: {source}")
        write_json(job, temp / "job.json")
        temp.rename(destination)
    return destination


def package_voice_design(manifest_path: str | Path, character_id: str, candidates: int = 3) -> Path:
    """Package an original Qwen3-TTS VoiceDesign request without copying model code."""
    if not 1 <= candidates <= 6:
        raise ValueError("voice-design candidates must be between 1 and 6")
    manifest_path = Path(manifest_path).resolve()
    episode_root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    character = manifest["characters"].get(character_id)
    if not isinstance(character, dict):
        raise ValueError(f"unknown character: {character_id}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}", character_id) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}", str(character.get("voice_id", ""))):
        raise ValueError("character_id and voice_id must be safe identifiers")
    design = character.get("voice_design")
    if not isinstance(design, dict):
        raise ValueError(f"characters.{character_id}.voice_design must define description, sample_text, and language")
    description = design.get("description")
    sample_text = design.get("sample_text")
    language = design.get("language", "Chinese")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("voice_design.description is required")
    if not isinstance(sample_text, str) or len(sample_text.strip()) < 50:
        raise ValueError("voice_design.sample_text must contain at least 50 characters for a 10-30 second reference")
    if language not in {"Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"}:
        raise ValueError("voice_design.language is unsupported by Qwen3-TTS")
    revision = int(character.get("voice_design_revision", 1))
    if "voice_design_revision" not in character:
        history = manifest.get("voice_design_jobs", {})
        parsed = []
        if isinstance(history, dict):
            for previous_id, previous in history.items():
                match = re.search(r"_design_r(\d+)$", str(previous_id))
                if match and isinstance(previous, dict) and previous.get("character_id") == character_id:
                    parsed.append((int(match.group(1)), str(previous.get("status", "")).upper()))
        latest_revision = max((item[0] for item in parsed), default=revision)
        latest_status = next((status for rev, status in parsed if rev == latest_revision), "")
        revision = latest_revision + int(latest_status in {"FAILED", "ERROR", "NEEDS_MANUAL_REVIEW"})
    job_id = f"{manifest['episode_id']}_{character['voice_id']}_design_r{revision:02d}"
    budget_limit = float(manifest.get("voice_design_budget_minutes", 15.0))
    design_jobs = manifest.get("voice_design_jobs", {})
    active_states = {"RESERVED", "SUBMITTED", "QUEUED", "RUNNING", "INTERRUPTED"}
    spent = sum(float(item.get("gpu_minutes", 0.0)) for item in design_jobs.values() if isinstance(item, dict))
    reserved = sum(
        float(item.get("reserved_gpu_minutes", 0.0)) for item in design_jobs.values()
        if isinstance(item, dict) and str(item.get("remote_status", item.get("status", ""))).upper() in active_states
    )
    remaining = max(0.0, budget_limit - spent - reserved)
    if remaining <= 0:
        raise ValueError("voice-design GPU budget is exhausted; manual review or a new authorized budget revision is required")
    job = {
        "schema_version": "ai-interview-voice-design-v1", "job_id": job_id,
        "episode_id": manifest["episode_id"], "character_id": character_id,
        "voice_id": character["voice_id"], "revision": revision,
        "backend": "Qwen3-TTS-VoiceDesign", "candidates": candidates,
        "description": description.strip(), "sample_text": sample_text.strip(), "language": language,
        "budget": {"remaining_gpu_minutes_per_episode": remaining},
    }
    destination = episode_root / "voice_design_jobs" / job_id
    if destination.exists():
        existing = destination / "job.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")) == job:
            return destination
        raise FileExistsError(f"Preserve existing non-identical voice-design job: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{job_id}.", dir=destination.parent) as temporary:
        temp = Path(temporary)
        write_json(job, temp / "job.json")
        temp.rename(destination)
    return destination
