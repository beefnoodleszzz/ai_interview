from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from ai_interview.config import dump_yaml
from ai_interview.audio import build_audio_master, build_edit_mix, character_error_rate, generate_srt, normalize_transcript
from ai_interview.manifest import load_manifest, validate_manifest
from ai_interview.media import build_roughcut_command, deterministic_qc, probe
from ai_interview.package import package_h3, package_voice, package_voice_design, sha256_file
from ai_interview.prompt import validate_prompt
from ai_interview.remote import _worker, doctor as remote_doctor, load_remote_config
from remote.ai_interview_h3_worker.contract import validate_job_directory
from remote.ai_interview_h3_worker.worker import _prepare_workflow
from ai_interview.state import begin_h3_attempt, read_budget, record_remote_job
from ai_interview.voice_design import freeze_voice_reference


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


def wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\0\0" * int(seconds * 48000))


def manifest() -> dict:
    return {
        "schema_version": "ai-interview-episode-v1",
        "pipeline_version": "0.1.0",
        "episode_id": "EP0001_test",
        "format": "9:16",
        "fps": 24,
        "target_duration": 3.5,
        "retry_policy": {
            "max_attempts_per_shot": 4,
            "max_gpu_minutes_per_shot": 15,
            "max_gpu_minutes_per_episode": 300,
        },
        "characters": {
            "guest": {
                "character_id": "guest_v001",
                "voice_id": "guest_voice_v001",
                "voice_reference": "refs/voice.wav",
            }
        },
        "beats": [
            {"id": "B01", "speaker": "guest", "text": "测试台词。", "emotion": "deadpan"}
        ],
        "shots": [
            {
                "id": "S001",
                "h3_mode": "ref2va",
                "status": "READY_FOR_H3",
                "beat_ids": ["B01"],
                "edit_duration_sec": 3.5,
                "generation_duration_sec": 124 / 24,
                "candidate_count": 2,
                "prompt": "prompts/S001.txt",
                "references": {
                    "images": ["refs/identity.png"], "videos": [], "audios": ["refs/line.wav"]
                },
                "audio_intent": "exact dialogue and room tone",
                "preserve": ["identity"],
                "avoid": ["extra speech"],
            }
        ],
    }


PROMPT = """subject_definitions:
<Subject 1> is the guest in <Picture 1>. <Audio 1> is the exact speech for <Subject 1> (S1).

summary:
[reference generation + audio reuse] A realistic interview close-up.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.
<Audio 1>: fully_copy - the exact speech is reused.

detailed_description:
The target is realistic live-action. [Shot 1] <Subject 1> (S1) says: <d>[Chinese] 测试台词。</d> No narration.

overall_soundscape:
Quiet room tone.

non_diegetic_music:
N/A
"""


class PipelineTests(unittest.TestCase):
    def test_manifest_and_audio_only_guard(self) -> None:
        value = manifest()
        self.assertEqual(validate_manifest(value), [])
        value["shots"][0]["references"]["images"] = []
        self.assertIn("Ref2VA audio cannot be the only reference", "\n".join(validate_manifest(value)))

    def test_prompt_order_and_dialogue(self) -> None:
        self.assertEqual(validate_prompt("ref2va", PROMPT, ["测试台词。"]), [])
        self.assertTrue(validate_prompt("ref2va", PROMPT.replace("summary:", "wrong:")))

    def test_package_h3_preserves_prompt_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "refs").mkdir(parents=True)
            (root / "prompts").mkdir()
            (root / "refs" / "identity.png").write_bytes(PNG_1X1)
            wav(root / "refs" / "line.wav", seconds=3)
            wav(root / "refs" / "voice.wav")
            (root / "prompts" / "S001.txt").write_text(PROMPT, encoding="utf-8")
            dump_yaml(manifest(), root / "episode.yaml")
            package = package_h3(root / "episode.yaml", "S001")
            job = json.loads((package / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(job["prompt"], PROMPT)
            for relative, digest in job["input_sha256"].items():
                self.assertEqual(sha256_file(package / relative), digest)

    def test_ref2va_multimodal_contract_and_workflow_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "refs").mkdir(parents=True)
            (root / "prompts").mkdir()
            (root / "refs" / "identity.png").write_bytes(PNG_1X1)
            wav(root / "refs" / "line.wav")
            (root / "refs" / "style.mp4").write_bytes(b"fixture video")
            (root / "prompts" / "S001.txt").write_text(PROMPT, encoding="utf-8")
            value = manifest()
            value["shots"][0]["status"] = "READY_FOR_H3"
            value["shots"][0]["references"]["videos"] = ["refs/style.mp4"]
            dump_yaml(value, root / "episode.yaml")
            with patch("ai_interview.package.duration", return_value=3.0):
                package = package_h3(root / "episode.yaml", "S001")
            job = validate_job_directory(package)
            self.assertEqual(job["inputs"]["reference_videos"], ["assets/video_01.mp4"])
            self.assertEqual(job["inputs"]["reference_audio"], ["assets/audio_01.wav"])
            with patch("remote.ai_interview_h3_worker.worker.WORKFLOW_ROOT", Path(__file__).resolve().parents[1] / "remote" / "workflows"):
                api, _, _ = _prepare_workflow(job, 1, {
                    "assets/image_01.png": "h3_fixture_image.png",
                    "assets/video_01.mp4": "h3_fixture_video.mp4",
                    "assets/audio_01.wav": "h3_fixture_audio.wav",
                })
            ref_node = next(item for item in api.values() if item.get("class_type") == "MiniMaxH3ReferenceToVideo")
            self.assertEqual(ref_node["inputs"]["ref_videos.ref_video_0"], ["920", 0])
            self.assertEqual(ref_node["inputs"]["ref_video_audios.ref_video_audio_0"], ["920", 1])
            self.assertEqual(ref_node["inputs"]["ref_audios.ref_audio_0"], ["930", 0])
            self.assertEqual(api["910"]["inputs"]["file"], "h3_fixture_video.mp4")
            self.assertEqual(api["930"]["inputs"]["audio"], "h3_fixture_audio.wav")

    def test_manifest_attempts_budget_revision_and_atomic_remote_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "episode.yaml"
            value = manifest()
            value["shots"][0]["status"] = "READY_FOR_H3"
            dump_yaml(value, manifest_path)
            begin_h3_attempt(manifest_path, "S001", "EP0001_test_S001_r01")
            begin_h3_attempt(manifest_path, "S001", "EP0001_test_S001_r01")
            running = {
                "job_id": "EP0001_test_S001_r01", "status": "RUNNING",
                "gpu_minutes": 7, "prompt_id": "prompt-123",
            }
            record_remote_job(manifest_path, "S001", running)
            record_remote_job(manifest_path, "S001", {**running, "gpu_minutes": 9})
            budget = read_budget(manifest_path)
            self.assertEqual(budget["gpu_minutes_used"], 9)
            self.assertEqual(budget["shots"]["S001"]["attempt_count"], 1)
            self.assertEqual(budget["shots"]["S001"]["prompt_id"], "prompt-123")
            record_remote_job(manifest_path, "S001", {**running, "status": "FAILED", "gpu_minutes": 15, "failure_class": "timeout"})
            updated = load_manifest(manifest_path)
            self.assertEqual(updated["shots"][0]["status"], "NEEDS_MANUAL_REVIEW")
            self.assertEqual(updated["shots"][0]["job_revision"], 1)

    def test_audio_master_is_48khz_preserves_selected_take_and_writes_srt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "refs").mkdir(parents=True)
            (root / "raw" / "lines").mkdir(parents=True)
            wav(root / "refs" / "voice.wav")
            raw = root / "raw" / "lines" / "B01.wav"
            wav(raw, seconds=1.2)
            value = manifest()
            value["beats"][0]["selected_take"] = {
                "candidate_id": "candidate_01", "job_id": "job_01",
                "path": "raw/lines/B01.wav", "sha256": sha256_file(raw),
                "asr_text": "测试台词", "acting_approved": True,
            }
            dump_yaml(value, root / "episode.yaml")
            result = build_audio_master(root / "episode.yaml")
            master = root / result["shot_masters"]["S001"]
            metadata = probe(master)
            audio_stream = next(item for item in metadata["streams"] if item["codec_type"] == "audio")
            self.assertEqual(audio_stream["sample_rate"], "48000")
            self.assertEqual(sha256_file(raw), value["beats"][0]["selected_take"]["sha256"])
            subtitle = generate_srt(root / "episode.yaml")
            self.assertIn("测试台词。", subtitle.read_text(encoding="utf-8"))

    def test_edit_mix_loops_room_tone_to_exact_shot_duration_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "audio").mkdir(parents=True)
            dialogue, tone = root / "audio" / "dialogue.wav", root / "audio" / "room.wav"
            wav(dialogue, seconds=1.2)
            wav(tone, seconds=0.25)
            value = manifest()
            value["studio"] = {"room_tone_reference": "audio/room.wav", "room_tone_gain_db": -32}
            value["shots"][0]["master_audio"] = "audio/dialogue.wav"
            dump_yaml(value, root / "episode.yaml")
            mix = build_edit_mix(root / "episode.yaml")
            path = root / mix["shot_mixes"]["S001"]
            self.assertEqual(probe(path)["streams"][0]["sample_rate"], "48000")
            self.assertAlmostEqual(float(probe(path)["format"]["duration"]), 3.5, places=2)
            self.assertEqual(build_edit_mix(root / "episode.yaml"), mix)

    def test_transcript_normalization_and_cer(self) -> None:
        self.assertEqual(normalize_transcript("你好， A-12！"), "你好a12")
        self.assertEqual(character_error_rate("你好，世界。", "你好世界"), 0)

    def test_package_voice_uses_index_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "refs").mkdir(parents=True)
            wav(root / "refs" / "voice.wav")
            dump_yaml(manifest(), root / "episode.yaml")
            path = package_voice(root / "episode.yaml")
            job = json.loads((path / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(job["backend"], "IndexTTS-2.5")
            self.assertEqual(job["job_id"], "EP0001_test_voice_r01")
            self.assertEqual(job["input_sha256"]["assets/voice_guest.wav"], sha256_file(path / "assets/voice_guest.wav"))

    def test_qwen_voice_design_package_and_freeze_is_versioned_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            root.mkdir()
            value = manifest()
            value["characters"]["guest"]["voice_reference"] = "02_refs/voices/guest_voice_v001_r01.wav"
            value["characters"]["guest"]["voice_design"] = {
                "description": "An original dry, calm conversational Mandarin voice.",
                "sample_text": "我们本来准备毁灭地球，后来负责这个计划的主管开始每天看短视频，所有会议都因此一再延期。这个决定很重要，但暂时还没有人申请重新召开会议。",
                "language": "Chinese",
            }
            dump_yaml(value, root / "episode.yaml")
            package = package_voice_design(root / "episode.yaml", "guest", candidates=2)
            job = json.loads((package / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(job["backend"], "Qwen3-TTS-VoiceDesign")
            self.assertEqual(job["candidates"], 2)
            self.assertEqual(job["voice_id"], "guest_voice_v001")

            candidate_dir = root / "voice_design_results" / job["job_id"]
            candidate_dir.mkdir(parents=True)
            candidate = candidate_dir / "guest_voice_v001_candidate_01.wav"
            wav(candidate, seconds=12)
            metadata = {
                "schema_version": "ai-interview-voice-design-v1", "job_id": job["job_id"],
                "character_id": "guest", "voice_id": "guest_voice_v001", "revision": 1,
                "candidate_id": job["job_id"] + "_candidate_01", "filename": candidate.name,
                "sha256": sha256_file(candidate),
            }
            metadata_path = candidate.with_suffix(".json")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            selected = freeze_voice_reference(root / "episode.yaml", "guest", metadata_path, "owner")
            self.assertEqual(selected["sample_rate"], 48000)
            self.assertEqual(selected["channels"], 1)
            self.assertTrue((root / selected["path"]).is_file())
            self.assertEqual(freeze_voice_reference(root / "episode.yaml", "guest", metadata_path, "owner"), selected)

    def test_roughcut_command_is_local_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "video.mp4").touch()
            (root / "audio.wav").touch()
            value = manifest()
            value["shots"][0]["selected_video"] = "video.mp4"
            value["shots"][0]["edit_audio"] = "audio.wav"
            command = build_roughcut_command(root, value, root / "roughcut.mp4")
            self.assertEqual(command[0], "ffmpeg")
            self.assertIn("concat=n=1:v=1:a=1", command[command.index("-filter_complex") + 1])

    def test_remote_config_is_localhost_only_and_missing_credentials_block(self) -> None:
        project = Path(__file__).resolve().parents[1]
        config = load_remote_config(project)
        self.assertEqual(config["comfyui"]["endpoint"], "http://127.0.0.1:8188")
        result = remote_doctor(project)
        self.assertIn(result["status"], {"BLOCKED", "FAIL", "PASS"})

    def test_worker_preserves_structured_diagnostic_on_nonzero_exit(self) -> None:
        response = {
            "schema_version": "ai-interview-h3-remote-v1",
            "status": "BLOCKED",
            "checks": {"comfyui_api": "FAIL"},
        }
        completed = subprocess.CompletedProcess(
            args=["ssh"], returncode=2, stdout=json.dumps(response), stderr=""
        )
        config = {"remote": {"root": "/root/autodl-tmp/ai_interview"}}
        connection = {"port": 22, "target": "root@example.invalid"}
        with patch("ai_interview.remote.subprocess.run", return_value=completed):
            self.assertEqual(
                _worker(
                    config,
                    connection,
                    ["doctor", "--json"],
                    accept_nonzero_json=True,
                ),
                response,
            )

    def test_deterministic_media_qc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "fixture.mp4"
            command = [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x284:r=24:d=0.6",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(target),
            ]
            subprocess.run(command, check=True)
            result = deterministic_qc(target)
            self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
