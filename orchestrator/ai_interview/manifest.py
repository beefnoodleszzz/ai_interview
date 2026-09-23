from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .config import load_yaml

EPISODE_RE = re.compile(r"^EP\d{4}_[a-z0-9][a-z0-9_-]*$")
SHOT_RE = re.compile(r"^S\d{3}$")
BEAT_RE = re.compile(r"^B\d{2,3}$")
MODES = {"i2va", "fl2va", "ref2va"}
STATES = {
    "PLANNED", "AUDIO_RENDERING", "AUDIO_QC", "READY_FOR_H3",
    "H3_RENDERING", "H3_QC", "NEEDS_RETRY", "NEEDS_LIPSYNC",
    "NEEDS_MANUAL_REVIEW", "READY_FOR_EDIT", "UPSCALED", "DONE",
}


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def validate_manifest(value: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != "ai-interview-episode-v1":
        errors.append("schema_version must be ai-interview-episode-v1")
    episode_id = value.get("episode_id")
    if not isinstance(episode_id, str) or not EPISODE_RE.fullmatch(episode_id):
        errors.append("episode_id must match EP0001_slug")
    if value.get("format") != "9:16":
        errors.append("format must be 9:16")
    if value.get("fps") not in {24, 25}:
        errors.append("fps must be 24 or 25")
    if not _positive(value.get("target_duration")):
        errors.append("target_duration must be positive")

    characters = value.get("characters")
    if not isinstance(characters, Mapping) or not characters:
        errors.append("characters must be a non-empty mapping")
        characters = {}
    for key, character in characters.items():
        if not isinstance(character, Mapping):
            errors.append(f"characters.{key} must be a mapping")
            continue
        if not isinstance(character.get("character_id"), str):
            errors.append(f"characters.{key}.character_id is required")
        if not isinstance(character.get("voice_id"), str):
            errors.append(f"characters.{key}.voice_id is required")

    beats = value.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats must be a non-empty list")
        beats = []
    beat_ids: set[str] = set()
    for index, beat in enumerate(beats):
        if not isinstance(beat, Mapping):
            errors.append(f"beats[{index}] must be a mapping")
            continue
        beat_id = beat.get("id")
        if not isinstance(beat_id, str) or not BEAT_RE.fullmatch(beat_id):
            errors.append(f"beats[{index}].id must match B01")
        elif beat_id in beat_ids:
            errors.append(f"duplicate beat id: {beat_id}")
        else:
            beat_ids.add(beat_id)
        if beat.get("speaker") not in characters:
            errors.append(f"beats[{index}].speaker must name a character")
        if not isinstance(beat.get("text"), str) or not beat["text"].strip():
            errors.append(f"beats[{index}].text is required")

    shots = value.get("shots")
    if not isinstance(shots, list) or not shots:
        errors.append("shots must be a non-empty list")
        shots = []
    shot_ids: set[str] = set()
    for index, shot in enumerate(shots):
        if not isinstance(shot, Mapping):
            errors.append(f"shots[{index}] must be a mapping")
            continue
        shot_id = shot.get("id")
        if not isinstance(shot_id, str) or not SHOT_RE.fullmatch(shot_id):
            errors.append(f"shots[{index}].id must match S001")
        elif shot_id in shot_ids:
            errors.append(f"duplicate shot id: {shot_id}")
        else:
            shot_ids.add(shot_id)
        if shot.get("h3_mode") not in MODES:
            errors.append(f"{shot_id or index}.h3_mode must be i2va, fl2va or ref2va")
        if shot.get("status") not in STATES:
            errors.append(f"{shot_id or index}.status is invalid")
        if not _positive(shot.get("edit_duration_sec")):
            errors.append(f"{shot_id or index}.edit_duration_sec must be positive")
        if not _positive(shot.get("generation_duration_sec")):
            errors.append(f"{shot_id or index}.generation_duration_sec must be positive")
        elif not 124 / 24 <= float(shot["generation_duration_sec"]) <= 362 / 24:
            errors.append(f"{shot_id or index}.generation_duration_sec must fit 124-362 H3 frames")
        elif abs((round(float(shot["generation_duration_sec"]) * 24) - 5) % 17) > 0:
            errors.append(f"{shot_id or index}.generation_duration_sec must align to H3 17k+5 frames")
        if _positive(shot.get("edit_duration_sec")) and _positive(shot.get("generation_duration_sec")):
            if float(shot["edit_duration_sec"]) > float(shot["generation_duration_sec"]):
                errors.append(f"{shot_id or index}.generation_duration_sec must cover edit duration")
        references = shot.get("references")
        if not isinstance(references, Mapping):
            errors.append(f"{shot_id or index}.references must be a mapping")
            references = {}
        counts = {name: len(references.get(name, [])) if isinstance(references.get(name, []), list) else -1 for name in ("images", "videos", "audios")}
        if any(count < 0 for count in counts.values()):
            errors.append(f"{shot_id or index}.references values must be lists")
        else:
            if counts["images"] > 9 or counts["videos"] > 3 or counts["audios"] > 3:
                errors.append(f"{shot_id or index} exceeds H3 reference count limits")
            if sum(counts.values()) > 12:
                errors.append(f"{shot_id or index} exceeds 12 mixed references")
            if shot.get("h3_mode") == "ref2va" and counts["audios"] and not (counts["images"] or counts["videos"]):
                errors.append(f"{shot_id or index} Ref2VA audio cannot be the only reference")
            if shot.get("h3_mode") == "i2va" and not isinstance(references.get("first_frame"), str):
                errors.append(f"{shot_id or index} i2va requires references.first_frame")
            if shot.get("h3_mode") == "fl2va" and not (
                isinstance(references.get("first_frame"), str)
                and isinstance(references.get("last_frame"), str)
            ):
                errors.append(f"{shot_id or index} fl2va requires first_frame and last_frame")
        for beat_id in shot.get("beat_ids", []):
            if beat_id not in beat_ids:
                errors.append(f"{shot_id or index} references unknown beat {beat_id}")

    policy = value.get("retry_policy")
    if not isinstance(policy, Mapping):
        errors.append("retry_policy must be a mapping")
    else:
        expected = {
            "max_attempts_per_shot": 4,
            "max_gpu_minutes_per_shot": 15,
            "max_gpu_minutes_per_episode": 300,
        }
        for key, limit in expected.items():
            current = policy.get(key)
            if not _positive(current) or float(current) > limit:
                errors.append(f"retry_policy.{key} must be positive and <= {limit}")
    return errors


def load_manifest(path: str | Path) -> dict[str, Any]:
    value = load_yaml(path)
    errors = validate_manifest(value)
    if errors:
        raise ValueError("Invalid episode manifest:\n- " + "\n- ".join(errors))
    return value


def find_shot(manifest: Mapping[str, Any], shot_id: str) -> dict[str, Any]:
    for shot in manifest["shots"]:
        if shot.get("id") == shot_id:
            return dict(shot)
    raise ValueError(f"Unknown shot id: {shot_id}")
