"""Voice candidate selection, ASR comparison, and non-destructive 48 kHz masters."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .media import duration
from .hashing import sha256_file
from .state import _atomic_save
from .manifest import load_manifest


def normalize_transcript(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if char.isalnum())


def character_error_rate(reference: str, hypothesis: str) -> float:
    expected, actual = normalize_transcript(reference), normalize_transcript(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, start=1):
        current = [i]
        for j, right in enumerate(actual, start=1):
            current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = current
    return row[-1] / len(expected)


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"asset must stay inside the episode: {path}")
    return resolved.relative_to(root.resolve()).as_posix()


def select_take(
    manifest_path: str | Path,
    beat_id: str,
    metadata_path: str | Path,
    selected_by: str,
) -> dict[str, Any]:
    if not selected_by.strip():
        raise ValueError("selected_by is required to record a human take selection")
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    beat = next((item for item in manifest["beats"] if item["id"] == beat_id), None)
    if beat is None:
        raise ValueError(f"Unknown beat id: {beat_id}")
    metadata_path = Path(metadata_path).resolve()
    if not metadata_path.is_relative_to(root) or not metadata_path.is_file():
        raise FileNotFoundError("candidate metadata must be inside the episode")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("line_id") != beat_id:
        raise ValueError("candidate metadata line_id does not match the selected beat")
    filename = metadata.get("filename") or metadata.get("wav")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("candidate metadata must include a safe WAV filename")
    wav_path = metadata_path.parent / filename
    if not wav_path.is_file() or wav_path.is_symlink():
        raise FileNotFoundError(f"selected candidate WAV is missing: {filename}")
    actual_hash = sha256_file(wav_path)
    if metadata.get("sha256") != actual_hash:
        raise ValueError("candidate WAV SHA-256 does not match metadata")
    asr_text = metadata.get("asr_text")
    if not isinstance(asr_text, str):
        raise ValueError("candidate metadata is missing ASR text")
    selected = {
        "candidate_id": metadata.get("candidate_id"),
        "job_id": metadata.get("job_id"),
        "path": _relative(root, wav_path),
        "sha256": actual_hash,
        "asr_text": asr_text,
        "cer": round(character_error_rate(beat["text"], asr_text), 4),
        "duration_sec": float(metadata.get("duration_sec") or duration(wav_path)),
        "selected_by": selected_by.strip(),
        "selected_at": datetime.now(UTC).isoformat(),
        "acting_approved": True,
        "evidence": "owner_selected; ASR is numeric QC only",
    }
    beat["selected_take"] = selected
    for shot in manifest["shots"]:
        if beat_id in shot.get("beat_ids", []) and all(
            isinstance(next(item for item in manifest["beats"] if item["id"] == current).get("selected_take"), dict)
            for current in shot.get("beat_ids", [])
        ):
            shot["status"] = "AUDIO_QC"
    _atomic_save(manifest_path, manifest)
    return selected


def _expected_sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def _existing_output(path: Path, expected: dict[str, Any]) -> bool:
    sidecar = _expected_sidecar(path)
    if not path.exists() or not sidecar.is_file():
        return False
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    return all(value.get(key) == item for key, item in expected.items()) and value.get("output_sha256") == sha256_file(path)


def _run_ffmpeg(command: list[str], expected_output: Path) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        expected_output.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip()[-2000:] or "ffmpeg audio operation failed")


def _make_wave(source: Path, target: Path, expected: dict[str, Any]) -> Path:
    if target.exists():
        if _existing_output(target, expected):
            return target
        raise FileExistsError(f"Preserve existing non-identical audio master: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.stem}.", dir=target.parent) as temporary:
        temp = Path(temporary) / target.name
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-n", "-i", str(source),
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(temp),
        ]
        _run_ffmpeg(command, temp)
        if not temp.is_file():
            raise RuntimeError("ffmpeg did not create the normalized WAV")
        temp.replace(target)
    sidecar = {**expected, "output_sha256": sha256_file(target)}
    _expected_sidecar(target).write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _concat_waves(sources: list[Path], pauses: list[float], target: Path, expected: dict[str, Any]) -> Path:
    if target.exists():
        if _existing_output(target, expected):
            return target
        raise FileExistsError(f"Preserve existing non-identical audio master: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    inputs = [part for source in sources for part in ("-i", str(source))]
    filters = []
    concat = []
    for i, source in enumerate(sources):
        filters.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=mono,asetpts=PTS-STARTPTS[a{i}]")
        concat.append(f"[a{i}]")
        pause = pauses[i]
        if pause > 0:
            filters.append(f"aevalsrc=0:d={pause:.6f}:s=48000:c=mono[s{i}]")
            concat.append(f"[s{i}]")
    filters.append("".join(concat) + f"concat=n={len(concat)}:v=0:a=1[out]")
    with tempfile.TemporaryDirectory(prefix=f".{target.stem}.", dir=target.parent) as temporary:
        temp = Path(temporary) / target.name
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-n", *inputs,
            "-filter_complex", ";".join(filters), "-map", "[out]", "-ar", "48000", "-ac", "1",
            "-c:a", "pcm_s16le", str(temp),
        ]
        _run_ffmpeg(command, temp)
        if not temp.is_file():
            raise RuntimeError("ffmpeg did not create the concatenated audio master")
        temp.replace(target)
    sidecar = {**expected, "output_sha256": sha256_file(target)}
    _expected_sidecar(target).write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def build_audio_master(manifest_path: str | Path) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    normalized: dict[str, Path] = {}
    for beat in manifest["beats"]:
        take = beat.get("selected_take")
        if not isinstance(take, dict) or not take.get("acting_approved"):
            raise ValueError(f"beat {beat['id']} needs a human-approved selected_take")
        source = (root / take["path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file() or sha256_file(source) != take.get("sha256"):
            raise ValueError(f"selected take missing or changed for {beat['id']}")
        target = root / "01_audio" / "master" / "beats" / f"{beat['id']}.wav"
        normalized[beat["id"]] = _make_wave(source, target, {
            "source": take["path"], "source_sha256": take["sha256"], "sample_rate": 48000,
            "channels": 1, "beat_id": beat["id"],
        })
    shot_outputs: dict[str, str] = {}
    for shot in manifest["shots"]:
        beat_ids = shot.get("beat_ids", [])
        if not beat_ids:
            continue
        beats = [next(item for item in manifest["beats"] if item["id"] == beat_id) for beat_id in beat_ids]
        sources = [normalized[beat["id"]] for beat in beats]
        pauses = [float(beat.get("pause_after", 0)) for beat in beats]
        path = root / "01_audio" / "master" / "shots" / f"{shot['id']}.wav"
        expected = {
            "shot_id": shot["id"], "beat_ids": beat_ids,
            "source_sha256": [sha256_file(source) for source in sources],
            "pause_after": pauses, "sample_rate": 48000, "channels": 1,
        }
        shot_outputs[shot["id"]] = _concat_waves(sources, pauses, path, expected).relative_to(root).as_posix()
        shot["master_audio"] = shot_outputs[shot["id"]]
        if all(isinstance(beat.get("selected_take"), dict) for beat in beats):
            shot["status"] = "READY_FOR_H3"
    episode_sources = [normalized[beat["id"]] for beat in manifest["beats"]]
    episode_pauses = [float(beat.get("pause_after", 0)) for beat in manifest["beats"]]
    episode_path = root / "01_audio" / "master" / f"{manifest['episode_id']}_audio_master.wav"
    episode_expected = {
        "episode_id": manifest["episode_id"], "beat_ids": [beat["id"] for beat in manifest["beats"]],
        "source_sha256": [sha256_file(source) for source in episode_sources],
        "pause_after": episode_pauses, "sample_rate": 48000, "channels": 1,
    }
    _concat_waves(episode_sources, episode_pauses, episode_path, episode_expected)
    manifest["audio_master"] = {
        "path": episode_path.relative_to(root).as_posix(),
        "sha256": sha256_file(episode_path),
        "sample_rate": 48000,
        "channels": 1,
        "shot_masters": shot_outputs,
    }
    _atomic_save(manifest_path, manifest)
    return manifest["audio_master"]


def build_edit_mix(manifest_path: str | Path) -> dict[str, Any]:
    """Build 48 kHz shot mixes with continuous approved studio room tone."""
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    studio = manifest.get("studio", {})
    default_tone = studio.get("room_tone_reference") if isinstance(studio, dict) else None
    tone_gain = float(studio.get("room_tone_gain_db", -32.0)) if isinstance(studio, dict) else -32.0
    if not -60 <= tone_gain <= -6:
        raise ValueError("studio.room_tone_gain_db must be between -60 and -6 dB")
    outputs: dict[str, str] = {}
    for shot in manifest["shots"]:
        raw_tone = shot.get("room_tone", default_tone)
        if not isinstance(raw_tone, str):
            raise ValueError(f"{shot['id']} needs a studio room tone reference before edit mix")
        tone = (root / raw_tone).resolve()
        if not tone.is_relative_to(root) or not tone.is_file() or tone.is_symlink():
            raise FileNotFoundError(f"{shot['id']} room tone is missing or unsafe: {raw_tone}")
        raw_dialogue = shot.get("master_audio")
        dialogue: Path | None = None
        if isinstance(raw_dialogue, str):
            dialogue = (root / raw_dialogue).resolve()
            if not dialogue.is_relative_to(root) or not dialogue.is_file() or dialogue.is_symlink():
                raise FileNotFoundError(f"{shot['id']} dialogue master is missing or unsafe: {raw_dialogue}")
        seconds = float(shot["edit_duration_sec"])
        path = root / "01_audio" / "mix" / "shots" / f"{shot['id']}_edit.wav"
        expected = {
            "shot_id": shot["id"], "dialogue_sha256": sha256_file(dialogue) if dialogue else None,
            "room_tone_sha256": sha256_file(tone), "room_tone_gain_db": tone_gain,
            "duration_sec": seconds, "sample_rate": 48000, "channels": 1,
        }
        if path.exists():
            if not _existing_output(path, expected):
                raise FileExistsError(f"Preserve existing non-identical edit mix: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f".{path.stem}.", dir=path.parent) as temporary:
                staged = Path(temporary) / path.name
                if dialogue:
                    command = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-n", "-i", str(dialogue), "-stream_loop", "-1", "-i", str(tone)]
                    graph = (
                        f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,apad,atrim=duration={seconds:.6f}[dialogue];"
                        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,volume={tone_gain:.3f}dB,atrim=duration={seconds:.6f}[room];"
                        "[dialogue][room]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.97[out]"
                    )
                else:
                    command = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-n", "-stream_loop", "-1", "-i", str(tone)]
                    graph = f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,volume={tone_gain:.3f}dB,atrim=duration={seconds:.6f},alimiter=limit=0.97[out]"
                command += ["-filter_complex", graph, "-map", "[out]", "-t", f"{seconds:.6f}", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(staged)]
                _run_ffmpeg(command, staged)
                if not staged.is_file():
                    raise RuntimeError(f"ffmpeg did not create the edit mix for {shot['id']}")
                staged.replace(path)
            sidecar = {**expected, "output_sha256": sha256_file(path)}
            _expected_sidecar(path).write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shot["edit_audio"] = path.relative_to(root).as_posix()
        outputs[shot["id"]] = shot["edit_audio"]
    manifest["audio_mix"] = {"shot_mixes": outputs, "sample_rate": 48000, "channels": 1, "room_tone_gain_db": tone_gain}
    _atomic_save(manifest_path, manifest)
    return manifest["audio_mix"]


def generate_srt(manifest_path: str | Path, output: str | Path | None = None) -> Path:
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    cursor = 0.0
    rows = []
    for beat in manifest["beats"]:
        take = beat.get("selected_take")
        if not isinstance(take, dict) or not take.get("acting_approved"):
            raise ValueError(f"beat {beat['id']} needs a human-approved selected_take")
        source = (root / take["path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file() or sha256_file(source) != take.get("sha256"):
            raise ValueError(f"selected take missing or changed for {beat['id']}")
        start, end = cursor, cursor + duration(source)
        rows.append(f"{len(rows) + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n{beat['text']}\n")
        cursor = end + float(beat.get("pause_after", 0))
    target = Path(output).resolve() if output else root / "04_edit" / f"{manifest['episode_id']}.srt"
    if not target.is_relative_to(root):
        raise ValueError("subtitle output must stay inside the episode")
    data = "\n".join(rows)
    if target.exists():
        if target.read_text(encoding="utf-8") == data:
            return target
        raise FileExistsError(f"Preserve existing non-identical subtitle: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return target


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02},{millis:03}"
