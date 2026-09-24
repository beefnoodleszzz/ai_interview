from __future__ import annotations

import json
import io
import hashlib
import subprocess
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ai_interview.config import dump_yaml
from ai_interview.audio import build_audio_master, build_edit_mix, character_error_rate, generate_srt, normalize_transcript
from ai_interview.manifest import load_manifest, validate_manifest
from ai_interview.media import build_roughcut_command, deterministic_qc, probe, select_video_candidate
from ai_interview.package import package_h3, package_voice, package_voice_design, sha256_file
from ai_interview.postprocess import package_latentsync, package_seedvr2, select_postprocess_result
from ai_interview.prompt import validate_prompt
from ai_interview.remote import _REMOTE_AUDIT_PATH_HELPER, _publish_remote_directory, _scp, _worker, audit_remote, doctor as remote_doctor, load_remote_config, require_live_gpu, require_worker_ready, submit as remote_submit
from ai_interview import remote_postprocess
from ai_interview import remote_voice, remote_voice_design
from remote.ai_interview_h3_worker.contract import validate_job_directory
from remote.ai_interview_h3_worker import worker as h3_worker_module
from remote.ai_interview_h3_worker.worker import _prepare_workflow
from remote import postprocess_worker, voice_design_worker as voice_design_worker_module, voice_worker as voice_worker_module
from ai_interview.state import BudgetExhausted, begin_h3_attempt, read_budget, record_postprocess_remote, record_remote_job, record_voice_design_remote, record_voice_remote, reserve_postprocess_attempt, reserve_voice_attempt, reserve_voice_design_attempt
from ai_interview.voice_design import freeze_voice_reference
from ai_interview.cli import _manifest_for_remote_job, main as cli_main


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


def postprocess_reservation_fixture(root: Path, value: dict, job_id: str, mode: str, revision: int = 1) -> dict:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    video = assets / "selected.mp4"
    audio = assets / "edit.wav"
    video.write_bytes(b"canonical selected video")
    wav(audio)
    shot = value["shots"][0]
    shot.update({"selected_video": "assets/selected.mp4", "edit_audio": "assets/edit.wav"})
    video_sha, audio_sha = sha256_file(video), sha256_file(audio)
    shot["candidate_selection"] = {"sha256": video_sha}
    job = {
        "schema_version": remote_postprocess.PROTOCOL,
        "job_id": job_id, "episode_id": value["episode_id"], "shot_id": shot["id"],
        "mode": mode, "revision": revision,
        "inputs": {"video": "assets/input.mp4", "audio": "assets/edit_audio.wav"},
        "input_sha256": {"assets/input.mp4": video_sha, "assets/edit_audio.wav": audio_sha},
        "budget": {"remaining_gpu_minutes_per_shot": 11.0, "remaining_gpu_minutes_per_episode": 296.0},
    }
    if mode == "seedvr2":
        value["edit_lock"] = {"assets": [
            {"shot_id": shot["id"], "field": "selected_video", "path": shot["selected_video"], "sha256": video_sha},
            {"shot_id": shot["id"], "field": "edit_audio", "path": shot["edit_audio"], "sha256": audio_sha},
        ]}
        job["edit_lock_sha256"] = hashlib.sha256(json.dumps(value["edit_lock"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return job


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
        value["shots"][0]["generation_duration_sec"] = 362 / 24
        self.assertIn("max 15 seconds", "\n".join(validate_manifest(value)))
        value["shots"][0]["generation_duration_sec"] = 345 / 24
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
            retry = manifest()
            retry["shots"][0].update(status="NEEDS_RETRY", job_revision=2)
            dump_yaml(retry, root / "episode.yaml")
            self.assertEqual(package_h3(root / "episode.yaml", "S001").name, "EP0001_test_S001_r02")

    def test_postprocess_packages_require_review_gates_and_lock_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "assets").mkdir(parents=True)
            video = root / "assets" / "selected.mp4"
            audio = root / "assets" / "edit.wav"
            video.write_bytes(b"selected-video")
            wav(audio)
            value = manifest()
            shot = value["shots"][0]
            shot.update({
                "status": "NEEDS_LIPSYNC",
                "selected_video": "assets/selected.mp4",
                "edit_audio": "assets/edit.wav",
                "candidate_selection": {"sha256": sha256_file(video)},
            })
            path = root / "episode.yaml"
            dump_yaml(value, path)
            with patch("ai_interview.postprocess.deterministic_qc", return_value={"status": "PASS"}), patch(
                "ai_interview.postprocess.probe",
                return_value={"streams": [{"codec_type": "audio", "sample_rate": "48000"}]},
            ):
                package = package_latentsync(path, "S001", selected_by="director")
                job = json.loads((package / "job.json").read_text(encoding="utf-8"))
                self.assertEqual(job["mode"], "latentsync")
                self.assertEqual((package / "assets/input.mp4").read_bytes(), b"selected-video")
                self.assertEqual(package_latentsync(path, "S001", selected_by="director"), package)

                value["shots"][0]["status"] = "READY_FOR_EDIT"
                value["edit_lock"] = {"assets": [
                    {"shot_id": "S001", "field": "selected_video", "path": "assets/selected.mp4", "sha256": sha256_file(video)},
                    {"shot_id": "S001", "field": "edit_audio", "path": "assets/edit.wav", "sha256": sha256_file(audio)},
                ]}
                dump_yaml(value, path)
                seed_package = package_seedvr2(path, "S001", selected_by="director")
                seed_job = json.loads((seed_package / "job.json").read_text(encoding="utf-8"))
                self.assertEqual(seed_job["mode"], "seedvr2")
                self.assertRegex(seed_job["edit_lock_sha256"], r"^[0-9a-f]{64}$")

    def test_postprocess_reserves_budget_and_advances_failed_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = manifest()
            value["shots"][0]["status"] = "NEEDS_LIPSYNC"
            value["shots"][0]["runtime"] = {"attempt_count": 1, "gpu_minutes": 4.0}
            manifest_path = root / "episode.yaml"
            job = postprocess_reservation_fixture(root, value, "EP0001_test_S001_latentsync_r01", "latentsync")
            dump_yaml(value, manifest_path)

            with ThreadPoolExecutor(max_workers=8) as executor:
                reservations = list(executor.map(lambda _: reserve_postprocess_attempt(manifest_path, job), range(8)))
            reservation = reservations[0]
            self.assertTrue(all(item == reservation for item in reservations))
            self.assertEqual(reservation["remaining_gpu_minutes_per_shot"], 11.0)
            saved = load_manifest(manifest_path)
            self.assertEqual(saved["shots"][0]["runtime"]["postprocess_attempt_count"], 1)
            self.assertEqual(saved["postprocess_jobs"][job["job_id"]]["reserved_gpu_minutes"], 11.0)

            value = load_manifest(manifest_path)
            value["shots"][0]["status"] = "READY_FOR_EDIT"
            upscale = postprocess_reservation_fixture(root, value, "EP0001_test_S001_seedvr2_r01", "seedvr2")
            dump_yaml(value, manifest_path)
            with self.assertRaisesRegex(RuntimeError, "reserved by another active job"):
                reserve_postprocess_attempt(manifest_path, upscale)

            record_postprocess_remote(manifest_path, job, {"job_id": job["job_id"], "status": "FAILED", "gpu_minutes": 2.0})
            saved = load_manifest(manifest_path)
            self.assertEqual(saved["shots"][0]["lipsync_revision"], 2)
            self.assertEqual(saved["postprocess_jobs"][job["job_id"]]["remote_status"], "FAILED")
            self.assertEqual(saved["postprocess_jobs"][job["job_id"]]["reserved_gpu_minutes"], 0.0)
            record_postprocess_remote(manifest_path, job, {"job_id": job["job_id"], "status": "COMPLETE", "gpu_minutes": 2.0})
            self.assertEqual(load_manifest(manifest_path)["postprocess_jobs"][job["job_id"]]["remote_status"], "FAILED")

            retry_manifest = load_manifest(manifest_path)
            retry_manifest["shots"][0]["status"] = "NEEDS_LIPSYNC"
            dump_yaml(retry_manifest, manifest_path)
            retry = {**job, "job_id": "EP0001_test_S001_latentsync_r02", "revision": 2,
                     "budget": {"remaining_gpu_minutes_per_shot": 9.0, "remaining_gpu_minutes_per_episode": 294.0}}
            next_reservation = reserve_postprocess_attempt(manifest_path, retry)
            self.assertEqual(next_reservation["remaining_gpu_minutes_per_shot"], 9.0)

            exhausted = manifest()
            exhausted["shots"][0]["runtime"] = {"attempt_count": 3, "postprocess_attempt_count": 1, "gpu_minutes": 0.0}
            exhausted_path = root / "exhausted.yaml"
            exhausted["shots"][0]["status"] = "NEEDS_LIPSYNC"
            exhausted_job = postprocess_reservation_fixture(root, exhausted, job["job_id"], "latentsync")
            dump_yaml(exhausted, exhausted_path)
            with self.assertRaises(BudgetExhausted):
                reserve_postprocess_attempt(exhausted_path, exhausted_job)
            self.assertEqual(load_manifest(exhausted_path)["shots"][0]["status"], "NEEDS_MANUAL_REVIEW")

    def test_postprocess_budget_subtracts_active_h3_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = manifest()
            value["shots"][0]["status"] = "NEEDS_LIPSYNC"
            value["shots"][0]["runtime"] = {
                "attempt_count": 1, "gpu_minutes": 4.0, "h3_reserved_gpu_minutes": 8.0,
            }
            root = Path(temporary)
            manifest_path = root / "episode.yaml"
            job = postprocess_reservation_fixture(root, value, "EP0001_test_S001_latentsync_r01", "latentsync")
            dump_yaml(value, manifest_path)
            reservation = reserve_postprocess_attempt(manifest_path, job)
            self.assertEqual(reservation["remaining_gpu_minutes_per_shot"], 3.0)
            self.assertEqual(reservation["remaining_gpu_minutes_per_episode"], 3.0)

    def test_postprocess_reservation_rechecks_selection_and_edit_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            latency = manifest()
            latency["shots"][0]["status"] = "NEEDS_LIPSYNC"
            latency_job = postprocess_reservation_fixture(root, latency, "EP0001_test_S001_latentsync_r01", "latentsync")
            dump_yaml(latency, root / "latency.yaml")
            latency["shots"][0]["candidate_selection"]["sha256"] = "f" * 64
            dump_yaml(latency, root / "latency.yaml")
            with self.assertRaisesRegex(ValueError, "human-selected candidate"):
                reserve_postprocess_attempt(root / "latency.yaml", latency_job)

            seed = manifest()
            seed["shots"][0]["status"] = "READY_FOR_EDIT"
            seed_job = postprocess_reservation_fixture(root, seed, "EP0001_test_S001_seedvr2_r01", "seedvr2")
            dump_yaml(seed, root / "seed.yaml")
            seed["edit_lock"]["assets"][0]["sha256"] = "e" * 64
            dump_yaml(seed, root / "seed.yaml")
            with self.assertRaisesRegex(ValueError, "edit lock"):
                reserve_postprocess_attempt(root / "seed.yaml", seed_job)

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
            self.assertEqual(api["119"]["inputs"]["vae_name"], "minimax_h3_video_vae_fp16.safetensors")
            self.assertEqual(api["120"]["inputs"]["vae_name"], "minimax_h3_audio_vae_fp32.safetensors")

    def test_manifest_attempts_budget_revision_and_atomic_remote_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "episode.yaml"
            value = manifest()
            value["shots"][0]["status"] = "READY_FOR_H3"
            dump_yaml(value, manifest_path)
            begin_h3_attempt(manifest_path, "S001", "EP0001_test_S001_r01")
            reservation = begin_h3_attempt(manifest_path, "S001", "EP0001_test_S001_r01")
            self.assertEqual(reservation["shots"]["S001"]["h3_job_reserved_gpu_minutes"], 15)
            self.assertEqual(reservation["gpu_minutes_reserved"], 15)
            running = {
                "job_id": "EP0001_test_S001_r01", "status": "RUNNING",
                "gpu_minutes": 7, "prompt_id": "prompt-123",
            }
            record_remote_job(manifest_path, "S001", running)
            record_remote_job(manifest_path, "S001", {**running, "gpu_minutes": 9})
            budget = read_budget(manifest_path)
            self.assertEqual(budget["gpu_minutes_used"], 9)
            self.assertEqual(budget["shots"]["S001"]["h3_gpu_minutes_reserved"], 6)
            self.assertEqual(budget["shots"]["S001"]["attempt_count"], 1)
            self.assertEqual(budget["shots"]["S001"]["prompt_id"], "prompt-123")
            record_remote_job(manifest_path, "S001", {**running, "status": "FAILED", "gpu_minutes": 15, "failure_class": "timeout"})
            updated = load_manifest(manifest_path)
            self.assertEqual(updated["shots"][0]["status"], "NEEDS_MANUAL_REVIEW")
            self.assertEqual(updated["shots"][0]["job_revision"], 1)
            self.assertEqual(read_budget(manifest_path)["gpu_minutes_reserved"], 0)

            terminal_path = Path(temporary) / "terminal.yaml"
            terminal_value = manifest()
            terminal_value["shots"][0]["status"] = "READY_FOR_H3"
            dump_yaml(terminal_value, terminal_path)
            begin_h3_attempt(terminal_path, "S001", "EP0001_test_S001_r01")
            record_remote_job(terminal_path, "S001", {"job_id": "EP0001_test_S001_r01", "status": "COMPLETE", "gpu_minutes": 2.0})
            record_remote_job(terminal_path, "S001", {"job_id": "EP0001_test_S001_r01", "status": "FAILED", "gpu_minutes": 1.0})
            terminal_manifest = load_manifest(terminal_path)
            self.assertEqual(terminal_manifest["shots"][0]["status"], "H3_QC")
            self.assertEqual(terminal_manifest["shots"][0]["runtime"]["last_remote_status"], "COMPLETE")

    def test_remote_status_preserves_director_selected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for remote_status in ("COMPLETE", "FAILED"):
                manifest_path = Path(temporary) / f"{remote_status}.yaml"
                value = manifest()
                shot = value["shots"][0]
                shot["status"] = "READY_FOR_EDIT"
                shot["selected_video"] = "remote_results/selected.mp4"
                shot["candidate_selection"] = {
                    "candidate_id": "EP0001_test_S001_r01_candidate_01",
                    "lip_grade": "B",
                }
                shot["runtime"] = {
                    "job_id": "EP0001_test_S001_r01",
                    "job_revision": 1,
                    "attempt_count": 1,
                    "gpu_minutes": 1.0,
                }
                dump_yaml(value, manifest_path)
                record_remote_job(manifest_path, "S001", {
                    "job_id": "EP0001_test_S001_r01",
                    "status": remote_status,
                    "gpu_minutes": 1.0,
                })
                updated = load_manifest(manifest_path)["shots"][0]
                self.assertEqual(updated["status"], "READY_FOR_EDIT")
                self.assertEqual(updated["runtime"]["job_revision"], 1)
                self.assertEqual(updated["runtime"]["last_remote_status"], remote_status)

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

    def test_subtitles_follow_shot_timeline_across_reaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            root.mkdir()
            first, second = root / "B01.wav", root / "B02.wav"
            wav(first, seconds=1.0)
            wav(second, seconds=0.7)
            value = manifest()
            value["target_duration"] = 5.8
            value["beats"][0]["selected_take"] = {"path": first.name, "sha256": sha256_file(first), "acting_approved": True}
            value["beats"].append({"id": "B02", "speaker": "guest", "text": "第二句。", "selected_take": {"path": second.name, "sha256": sha256_file(second), "acting_approved": True}})
            reaction = {**value["shots"][0], "id": "S002", "beat_ids": [], "edit_duration_sec": 0.8}
            last = {**value["shots"][0], "id": "S003", "beat_ids": ["B02"], "edit_duration_sec": 1.5}
            value["shots"].extend([reaction, last])
            dump_yaml(value, root / "episode.yaml")
            subtitle = generate_srt(root / "episode.yaml").read_text(encoding="utf-8")
            self.assertIn("00:00:04,300 --> 00:00:05,000", subtitle)

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

    def test_failed_voice_revision_advances_and_is_packaged_without_overwriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            (root / "refs").mkdir(parents=True)
            wav(root / "refs" / "voice.wav")
            value = manifest()
            value["shots"][0]["status"] = "AUDIO_RENDERING"
            dump_yaml(value, root / "episode.yaml")
            first = package_voice(root / "episode.yaml")
            job = json.loads((first / "job.json").read_text(encoding="utf-8"))
            record_voice_remote(root / "episode.yaml", job, {"job_id": job["job_id"], "status": "SUBMITTED"})
            record_voice_remote(root / "episode.yaml", job, {"job_id": job["job_id"], "status": "FAILED", "error": "fixture failure"})
            failed = load_manifest(root / "episode.yaml")
            self.assertEqual(failed["voice_job_revision"], 2)
            self.assertEqual(failed["voice_jobs"][job["job_id"]]["revision"], 1)
            record_voice_remote(root / "episode.yaml", job, {"job_id": job["job_id"], "status": "RUNNING"})
            self.assertEqual(load_manifest(root / "episode.yaml")["voice_jobs"][job["job_id"]]["status"], "FAILED")
            second = package_voice(root / "episode.yaml")
            self.assertEqual(second.name, "EP0001_test_voice_r02")
            self.assertTrue(first.is_dir())

    def test_voice_budget_reservation_shares_h3_and_postprocess_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "episode.yaml"
            value = manifest()
            value["shots"][0]["runtime"] = {"attempt_count": 1, "gpu_minutes": 4.0, "h3_reserved_gpu_minutes": 8.0}
            dump_yaml(value, manifest_path)
            job = {
                "job_id": "EP0001_test_voice_r01", "episode_id": "EP0001_test", "revision": 1,
                "lines": [{"id": "B01", "shot_id": "S001"}],
                "budget": {"remaining_gpu_minutes_per_episode": 296.0, "remaining_gpu_minutes_by_shot": {"S001": 11.0}},
            }
            with ThreadPoolExecutor(max_workers=8) as executor:
                reservations = list(executor.map(lambda _: reserve_voice_attempt(manifest_path, job), range(8)))
            self.assertTrue(all(item == reservations[0] for item in reservations))
            self.assertEqual(reservations[0]["remaining_gpu_minutes_by_shot"], {"S001": 3.0})
            saved = load_manifest(manifest_path)
            self.assertEqual(saved["shots"][0]["runtime"]["voice_attempt_count"], 1)
            self.assertEqual(saved["voice_jobs"][job["job_id"]]["reserved_gpu_minutes"], 3.0)
            with self.assertRaises(BudgetExhausted):
                reserve_voice_attempt(manifest_path, {**job, "job_id": "EP0001_test_voice_other_r01"})

            record_voice_remote(manifest_path, job, {"job_id": job["job_id"], "status": "RUNNING", "gpu_minutes_by_shot": {"S001": 1.0}})
            budget = read_budget(manifest_path)
            self.assertEqual(budget["shots"]["S001"]["gpu_minutes"], 5.0)
            self.assertEqual(budget["shots"]["S001"]["voice_gpu_minutes_reserved"], 2.0)
            record_voice_remote(manifest_path, job, {"job_id": job["job_id"], "status": "COMPLETE", "gpu_minutes_by_shot": {"S001": 3.0}})
            budget = read_budget(manifest_path)
            self.assertEqual(budget["shots"]["S001"]["gpu_minutes"], 7.0)
            self.assertEqual(budget["shots"]["S001"]["voice_gpu_minutes_reserved"], 0.0)

    def test_voice_design_pending_jobs_share_episode_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "episode.yaml"
            value = manifest()
            value["characters"]["host"] = {"character_id": "host_v001", "voice_id": "host_voice_v001"}
            value["voice_design_budget_minutes"] = 15.0
            dump_yaml(value, manifest_path)
            guest = {"job_id": "EP0001_test_guest_voice_v001_design_r01", "episode_id": "EP0001_test", "character_id": "guest", "revision": 1, "budget": {"remaining_gpu_minutes_per_episode": 15.0}}
            host = {"job_id": "EP0001_test_host_voice_v001_design_r01", "episode_id": "EP0001_test", "character_id": "host", "revision": 1, "budget": {"remaining_gpu_minutes_per_episode": 15.0}}
            outcomes = []
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(reserve_voice_design_attempt, manifest_path, job) for job in (guest, host)]
                for job, future in zip((guest, host), futures):
                    try:
                        outcomes.append((job, future.result()))
                    except BudgetExhausted:
                        outcomes.append((job, None))
            winners = [(job, result) for job, result in outcomes if result is not None]
            losers = [job for job, result in outcomes if result is None]
            self.assertEqual(len(winners), 1)
            self.assertEqual(len(losers), 1)
            winning_job = winners[0][0]
            self.assertEqual(winners[0][1]["remaining_gpu_minutes_per_episode"], 15.0)
            record_voice_design_remote(manifest_path, winning_job, {"job_id": winning_job["job_id"], "status": "COMPLETE", "gpu_minutes": 10.0})
            second = reserve_voice_design_attempt(manifest_path, losers[0])
            self.assertEqual(second["remaining_gpu_minutes_per_episode"], 5.0)

    def test_voice_manual_review_results_settle_already_spent_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "episode.yaml"
            value = manifest()
            value["shots"][0]["status"] = "AUDIO_RENDERING"
            dump_yaml(value, manifest_path)
            voice_job = {
                "job_id": "EP0001_test_voice_r01", "episode_id": "EP0001_test", "revision": 1,
                "lines": [{"id": "B01", "shot_id": "S001"}],
                "budget": {"remaining_gpu_minutes_per_episode": 300.0, "remaining_gpu_minutes_by_shot": {"S001": 15.0}},
            }
            reserve_voice_attempt(manifest_path, voice_job)
            voice_result = voice_worker_module._manual_review_result(
                voice_job["job_id"], [], {"gpu_seconds_by_shot": {"S001": 90.0}}, 2.0,
            )
            settled = record_voice_remote(manifest_path, voice_job, voice_result)
            self.assertEqual(settled["gpu_minutes_used"], 1.5)
            self.assertEqual(settled["shots"]["S001"]["voice_gpu_minutes_reserved"], 0.0)
            self.assertEqual(load_manifest(manifest_path)["shots"][0]["status"], "NEEDS_MANUAL_REVIEW")

            value = manifest()
            value["voice_design_budget_minutes"] = 15.0
            value["characters"]["host"] = {"character_id": "host_v001", "voice_id": "host_voice_v001"}
            dump_yaml(value, manifest_path)
            design_job = {"job_id": "EP0001_test_guest_voice_v001_design_r01", "episode_id": "EP0001_test", "character_id": "guest", "revision": 1, "budget": {"remaining_gpu_minutes_per_episode": 15.0}}
            reserve_voice_design_attempt(manifest_path, design_job)
            design_result = voice_design_worker_module._manual_review_result(design_job["job_id"], [], 12.5)
            record_voice_design_remote(manifest_path, design_job, design_result)
            host_job = {"job_id": "EP0001_test_host_voice_v001_design_r01", "episode_id": "EP0001_test", "character_id": "host", "revision": 1, "budget": {"remaining_gpu_minutes_per_episode": 15.0}}
            remaining = reserve_voice_design_attempt(manifest_path, host_job)
            self.assertEqual(remaining["remaining_gpu_minutes_per_episode"], 2.5)

    def test_terminal_h3_job_id_cannot_be_reused_after_remote_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "episode.yaml"
            value = manifest()
            value["shots"][0]["status"] = "READY_FOR_H3"
            dump_yaml(value, manifest_path)
            job_id = "EP0001_test_S001_r01"
            begin_h3_attempt(manifest_path, "S001", job_id)
            record_remote_job(manifest_path, "S001", {"job_id": job_id, "status": "COMPLETE", "gpu_minutes": 5.0})
            before = load_manifest(manifest_path)
            with self.assertRaisesRegex(ValueError, "already been used"):
                begin_h3_attempt(manifest_path, "S001", job_id)
            after = load_manifest(manifest_path)
            self.assertEqual(after["shots"][0]["runtime"]["attempt_count"], before["shots"][0]["runtime"]["attempt_count"])
            self.assertEqual(after["shots"][0]["runtime"]["h3_reserved_gpu_minutes"], 0.0)

    def test_failed_voice_design_advances_revision_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EP0001_test"
            root.mkdir()
            value = manifest()
            value["characters"]["guest"]["voice_design"] = {
                "description": "An original dry, calm conversational Mandarin voice.",
                "sample_text": "我们本来准备毁灭地球，后来负责这个计划的主管开始每天看短视频，所有会议都因此一再延期。这个决定很重要，但暂时还没有人申请重新召开会议。",
                "language": "Chinese",
            }
            dump_yaml(value, root / "episode.yaml")
            first = package_voice_design(root / "episode.yaml", "guest")
            job = json.loads((first / "job.json").read_text(encoding="utf-8"))
            record_voice_design_remote(root / "episode.yaml", job, {"job_id": job["job_id"], "status": "SUBMITTED"})
            failed = {"job_id": job["job_id"], "status": "FAILED", "gpu_minutes": 0.0, "error": "fixture failure"}
            record_voice_design_remote(root / "episode.yaml", job, failed)
            record_voice_design_remote(root / "episode.yaml", job, failed)
            record_voice_design_remote(root / "episode.yaml", job, {"job_id": job["job_id"], "status": "COMPLETE", "gpu_minutes": 0.0})
            updated = load_manifest(root / "episode.yaml")
            self.assertEqual(updated["characters"]["guest"]["voice_design_revision"], 2)
            self.assertEqual(updated["voice_design_jobs"][job["job_id"]]["revision"], 1)
            self.assertEqual(updated["voice_design_jobs"][job["job_id"]]["status"], "FAILED")
            second = package_voice_design(root / "episode.yaml", "guest")
            self.assertEqual(second.name, "EP0001_test_guest_voice_v001_design_r02")
            self.assertTrue(first.is_dir())

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

    def test_legacy_h3_candidate_must_match_active_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = manifest()
            value["shots"][0]["runtime"] = {"job_id": "EP0001_test_S001_r02"}
            dump_yaml(value, root / "episode.yaml")
            video = root / "candidate_01.mp4"
            video.write_bytes(b"fixture video")
            metadata = root / "candidate_01.json"
            record = {
                "candidate_id": "EP0001_test_S001_r02_candidate_01",
                "filename": video.name,
                "sha256": sha256_file(video),
            }
            metadata.write_text(json.dumps(record), encoding="utf-8")
            with patch("ai_interview.media.deterministic_qc", return_value={"status": "PASS"}):
                selection = select_video_candidate(root / "episode.yaml", "S001", metadata, "director", "B")
                self.assertEqual(selection["candidate_id"], record["candidate_id"])
                record["candidate_id"] = "EP0001_test_S999_r02_candidate_01"
                metadata.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "active job"):
                    select_video_candidate(root / "episode.yaml", "S001", metadata, "director", "B")

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
            value["shots"][0]["edit_in_sec"] = 0.8
            with patch("ai_interview.media.duration", return_value=5.0):
                command = build_roughcut_command(root, value, root / "roughcut.mp4")
            self.assertIn("trim=start=0.8:duration=3.5", command[command.index("-filter_complex") + 1])
            with patch("ai_interview.media.duration", return_value=4.0):
                command = build_roughcut_command(root, value, root / "roughcut.mp4")
            self.assertIn("tpad=stop_mode=clone:stop_duration=0.341667", command[command.index("-filter_complex") + 1])
            with patch("ai_interview.media.duration", return_value=3.0):
                with self.assertRaisesRegex(ValueError, "held video"):
                    build_roughcut_command(root, value, root / "roughcut.mp4")

    def test_postprocess_selection_enters_edit_and_rejects_stale_inputs(self) -> None:
        for mode in ("latentsync", "seedvr2"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                value = manifest()
                job_id = f"EP0001_test_S001_{mode}_r01"
                job = postprocess_reservation_fixture(root, value, job_id, mode)
                shot = value["shots"][0]
                shot["status"] = "NEEDS_LIPSYNC" if mode == "latentsync" else "READY_FOR_EDIT"
                manifest_path = root / "episode.yaml"
                dump_yaml(value, manifest_path)
                package = root / "postprocess_jobs" / mode / job_id
                package.mkdir(parents=True)
                (package / "job.json").write_text(json.dumps(job), encoding="utf-8")
                imported = root / "postprocess_results" / mode / job_id
                (imported / "work").mkdir(parents=True)
                output = imported / "work" / "result.mp4"
                output.write_bytes(b"postprocess result")
                (imported / "result.json").write_text(json.dumps({
                    "schema_version": job["schema_version"], "job_id": job_id,
                    "episode_id": value["episode_id"], "shot_id": shot["id"],
                    "mode": mode, "status": "COMPLETE", "output": "work/result.mp4",
                    "sha256": sha256_file(output),
                }), encoding="utf-8")
                (imported / "local_qc.json").write_text('{"status":"PASS"}', encoding="utf-8")
                options = {"selected_by": "director", "lip_grade": "A" if mode == "latentsync" else None}
                with patch("ai_interview.postprocess.deterministic_qc", return_value={"status": "PASS"}):
                    selected = select_postprocess_result(manifest_path, shot["id"], imported, **options)
                    self.assertEqual(select_postprocess_result(manifest_path, shot["id"], imported, **options), selected)
                saved = load_manifest(manifest_path)
                self.assertEqual(saved["shots"][0]["status"], "READY_FOR_EDIT" if mode == "latentsync" else "UPSCALED")
                self.assertEqual(saved["shots"][0]["selected_video"], selected["path"] if mode == "latentsync" else "assets/selected.mp4")
                if mode == "seedvr2":
                    command = build_roughcut_command(root, saved, root / "rough.mp4")
                    self.assertIn(str(output.resolve()), command)
                    output.write_bytes(b"tampered")
                    with self.assertRaisesRegex(ValueError, "SHA-256"):
                        select_postprocess_result(manifest_path, shot["id"], imported, **options)
                    with self.assertRaisesRegex(ValueError, "upscaled video"):
                        build_roughcut_command(root, saved, root / "rough.mp4")
                else:
                    self.assertEqual(saved["shots"][0]["pre_lipsync_selection"]["sha256"], job["input_sha256"]["assets/input.mp4"])

    def test_remote_config_is_localhost_only_and_missing_credentials_block(self) -> None:
        project = Path(__file__).resolve().parents[1]
        config = load_remote_config(project)
        self.assertEqual(config["comfyui"]["endpoint"], "http://127.0.0.1:8188")
        with patch("ai_interview.remote.subprocess.run", side_effect=OSError("network disabled in unit test")):
            result = remote_doctor(project)
        self.assertIn(result["status"], {"BLOCKED", "FAIL", "PASS"})

    def test_remote_upload_selects_legacy_scp_without_changing_downloads(self) -> None:
        connection = {"port": 44317, "target": "root@example.invalid"}
        self.assertNotIn("-O", _scp(connection))
        self.assertIn("-O", _scp(connection, legacy_protocol=True))

    def test_voice_publish_uses_queue_lock_and_no_replace_move(self) -> None:
        connection = {"port": 22, "target": "root@example.invalid"}
        with patch("ai_interview.remote.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            _publish_remote_directory(connection, "/runtime/voice/jobs/inbox/.job.upload-a", "/runtime/voice/jobs/inbox/job", "/runtime/voice/jobs/.queue.lock", "voice")
        command = run.call_args.args[0][-1]
        self.assertIn("flock -x", command)
        self.assertIn("/runtime/voice/jobs/.queue.lock", command)
        self.assertIn("mv -T --", command)

    def test_h3_submit_writes_atomic_reservation_into_uploaded_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "EP0001_test_S001_r01"
            package.mkdir()
            job = {"job_id": package.name, "shot_id": "S001", "budget": {"remaining_gpu_minutes_per_shot": 15}}
            (package / "job.json").write_text(json.dumps(job), encoding="utf-8")
            config = {"remote": {"root": "/remote/root"}}
            connection = {"port": 22, "target": "root@host"}
            remote_responses = iter([
                {"schema_version": "ai-interview-h3-remote-v1", "jobs": []},
                {"job_id": package.name, "status": "QUEUED", "location": "inbox"},
                {"job_id": package.name, "status": "SUBMITTED"},
            ])
            ssh = subprocess.CompletedProcess([], 0, "", "")
            with (
                patch("ai_interview.remote.load_remote_config", return_value=config),
                patch("ai_interview.remote.resolve_connection", return_value=connection),
                patch("ai_interview.remote._worker", side_effect=lambda *_args: next(remote_responses)),
                patch("ai_interview.remote.require_live_gpu", return_value={"status": "READY"}),
                patch("ai_interview.remote.shutil.which", return_value=None),
                patch("ai_interview.remote.subprocess.run", return_value=ssh),
            ):
                result = remote_submit(
                    package, Path(temporary),
                    on_submit=lambda: {
                        "gpu_minutes_limit": 300,
                        "shots": {"S001": {"h3_job_reserved_gpu_minutes": 11}},
                    },
                )
            self.assertEqual(result["status"], "SUBMITTED")
            uploaded_job = json.loads((package / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(uploaded_job["budget"]["remaining_gpu_minutes_per_shot"], 11)
            self.assertEqual(uploaded_job["budget"]["remaining_gpu_minutes_per_episode"], 11)

    def test_h3_submit_resumes_existing_inbox_without_reserving_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "EP0001_test_S001_r01"
            package.mkdir()
            job_path = package / "job.json"
            job_path.write_text(json.dumps({"job_id": package.name, "shot_id": "S001", "input_sha256": {}}), encoding="utf-8")
            config = {"remote": {"root": "/remote/root"}}
            connection = {"port": 22, "target": "root@host"}
            responses = iter([
                {"schema_version": "ai-interview-h3-remote-v1", "jobs": [{
                    "job_id": package.name, "status": "QUEUED", "location": "inbox",
                    "request_sha256": sha256_file(job_path),
                }]},
                {"job_id": package.name, "status": "SUBMITTED"},
            ])
            with (
                patch("ai_interview.remote.load_remote_config", return_value=config),
                patch("ai_interview.remote.resolve_connection", return_value=connection),
                patch("ai_interview.remote._worker", side_effect=lambda *_args, **_kwargs: next(responses)) as worker,
                patch("ai_interview.remote.require_live_gpu", return_value={"status": "READY"}) as gpu_gate,
            ):
                reserve = unittest.mock.Mock(side_effect=AssertionError("existing reservation must not be charged twice"))
                result = remote_submit(package, Path(temporary), on_submit=reserve)
            self.assertEqual(result["status"], "SUBMITTED")
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(worker.call_args.args[2], ["submit", "--job-dir", f"/remote/root/jobs/inbox/{package.name}", "--json"])
            gpu_gate.assert_called_once()
            reserve.assert_not_called()

    def test_h3_submit_refuses_inbox_package_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "EP0001_test_S001_r01"
            package.mkdir()
            job_path = package / "job.json"
            job_path.write_text(json.dumps({"job_id": package.name, "shot_id": "S001", "input_sha256": {}}), encoding="utf-8")
            with (
                patch("ai_interview.remote.load_remote_config", return_value={"remote": {"root": "/remote/root"}}),
                patch("ai_interview.remote.resolve_connection", return_value={"port": 22, "target": "root@host"}),
                patch("ai_interview.remote._worker", return_value={"schema_version": "ai-interview-h3-remote-v1", "jobs": [{
                    "job_id": package.name, "status": "QUEUED", "location": "inbox", "request_sha256": "0" * 64,
                }]}) as worker,
                patch("ai_interview.remote.require_live_gpu") as gpu_gate,
            ):
                with self.assertRaisesRegex(FileExistsError, "different package"):
                    remote_submit(package, Path(temporary))
            worker.assert_called_once()
            gpu_gate.assert_not_called()

    def test_voice_submit_resumes_matching_inbox_without_reupload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "EP0001_test_voice_r01"
            package.mkdir()
            job_path = package / "job.json"
            job_path.write_text(json.dumps({"job_id": package.name, "input_sha256": {}, "budget": {
                "remaining_gpu_minutes_per_episode": 12.0, "remaining_gpu_minutes_by_shot": {"S001": 12.0},
            }}), encoding="utf-8")
            config = {"remote": {"root": "/remote/root"}}
            connection = {"port": 22, "target": "root@host"}
            responses = iter([
                {"schema_version": remote_voice.PROTOCOL, "jobs": [{
                    "job_id": package.name, "status": "QUEUED", "location": "inbox",
                    "request_sha256": sha256_file(job_path),
                }]},
                {"schema_version": remote_voice.PROTOCOL, "job_id": package.name, "status": "SUBMITTED"},
            ])
            with (
                patch("ai_interview.remote_voice.load_remote_config", return_value=config),
                patch("ai_interview.remote_voice.resolve_connection", return_value=connection),
                patch("ai_interview.remote_voice._status_worker", side_effect=lambda *_args: next(responses)) as worker,
                patch("ai_interview.remote_voice.require_worker_ready", return_value={"status": "READY"}) as doctor,
                patch("ai_interview.remote_voice.subprocess.run") as transport,
            ):
                reservation = {"remaining_gpu_minutes_per_episode": 12.0, "remaining_gpu_minutes_by_shot": {"S001": 12.0}}
                on_reserve = unittest.mock.Mock(return_value=reservation)
                result = remote_voice.submit_voice(package, Path(temporary), on_reserve=on_reserve)
            self.assertEqual(result["status"], "SUBMITTED")
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(worker.call_args.args[2:], ("submit", "--job-dir", f"/remote/root/voice/jobs/inbox/{package.name}", "--json"))
            doctor.assert_called_once()
            transport.assert_not_called()
            on_reserve.assert_called_once()

    def test_voice_design_submit_resumes_matching_inbox_without_reupload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "EP0001_test_guest_voice_v001_design_r01"
            package.mkdir()
            job_path = package / "job.json"
            job_path.write_text(json.dumps({
                "schema_version": remote_voice_design.PROTOCOL,
                "job_id": package.name,
                "episode_id": "EP0001_test",
                "input_sha256": {},
                "budget": {"remaining_gpu_minutes_per_episode": 9.0},
            }), encoding="utf-8")
            config = {"remote": {"root": "/remote/root"}}
            connection = {"port": 22, "target": "root@host"}
            responses = iter([
                {"schema_version": remote_voice_design.PROTOCOL, "jobs": [{
                    "job_id": package.name, "status": "QUEUED", "location": "inbox",
                    "request_sha256": sha256_file(job_path),
                }]},
                {"schema_version": remote_voice_design.PROTOCOL, "job_id": package.name, "status": "SUBMITTED"},
            ])
            with (
                patch("ai_interview.remote_voice_design.load_remote_config", return_value=config),
                patch("ai_interview.remote_voice_design.resolve_connection", return_value=connection),
                patch("ai_interview.remote_voice_design.voice_design_status", return_value=next(responses)) as status,
                patch("ai_interview.remote_voice_design.require_worker_ready", return_value={"status": "READY"}) as doctor,
                patch("ai_interview.remote_voice_design._worker", side_effect=lambda *_args, **_kwargs: next(responses)) as worker,
                patch("ai_interview.remote_voice_design.subprocess.run") as transport,
            ):
                on_reserve = unittest.mock.Mock(return_value={"remaining_gpu_minutes_per_episode": 9.0})
                result = remote_voice_design.submit_voice_design(package, Path(temporary), on_reserve=on_reserve)
            self.assertEqual(result["status"], "SUBMITTED")
            status.assert_called_once()
            self.assertEqual(worker.call_args.args[2], ["submit", "--job-dir", f"/remote/root/voice_design/jobs/inbox/{package.name}", "--json"])
            doctor.assert_called_once()
            transport.assert_not_called()
            on_reserve.assert_called_once()

    def test_h3_worker_publish_is_idempotent_and_preserves_conflict_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            job_id = "EP0001_test_S001_r01"
            inbox = runtime / "jobs" / "inbox"

            def make_stage(name: str, image_bytes: bytes) -> Path:
                stage = inbox / name
                assets = stage / "assets"
                assets.mkdir(parents=True)
                image = assets / "host.png"
                image.write_bytes(image_bytes)
                (stage / "job.json").write_text(json.dumps({
                    "schema_version": "ai-interview-h3-remote-v1",
                    "job_id": job_id,
                    "episode_id": "EP0001_test",
                    "shot_id": "S001",
                    "mode": "ref2va",
                    "candidate_count": 1,
                    "budget": {"remaining_gpu_minutes_per_shot": 15, "remaining_gpu_minutes_per_episode": 100},
                    "duration_sec": 124 / 24,
                    "aspect_ratio": "9:16",
                    "prompt": "A short fixed dialogue shot.",
                    "audio": {"generate_native_audio": True, "intent": "quiet interview room ambience"},
                    "preserve": [], "avoid": [],
                    "inputs": {"reference_images": ["assets/host.png"], "reference_videos": [], "reference_audio": []},
                    "input_sha256": {"assets/host.png": hashlib.sha256(image_bytes).hexdigest()},
                    "output": {"video": True, "native_audio": True},
                }), encoding="utf-8")
                return stage

            with patch.object(h3_worker_module, "RUNTIME", runtime):
                invalid = make_stage(f".{job_id}.upload-invalid", PNG_1X1)
                invalid_request = json.loads((invalid / "job.json").read_text(encoding="utf-8"))
                invalid_request["duration_sec"] = 362 / 24
                (invalid / "job.json").write_text(json.dumps(invalid_request), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "124-345 H3 frame count"):
                    validate_job_directory(invalid, allow_upload_name=True)
                first = make_stage(f".{job_id}.upload-one", PNG_1X1)
                published = h3_worker_module._publish(first, job_id)
                self.assertEqual(published["location"], "inbox")
                self.assertFalse(first.exists())
                duplicate = make_stage(f".{job_id}.upload-two", PNG_1X1)
                reused = h3_worker_module._publish(duplicate, job_id)
                self.assertTrue(reused["existing"])
                self.assertEqual(reused["location"], "inbox")
                self.assertFalse(duplicate.exists())
                conflict = make_stage(f".{job_id}.upload-conflict", b"different")
                with self.assertRaisesRegex(FileExistsError, "different package"):
                    h3_worker_module._publish(conflict, job_id)
                self.assertTrue(conflict.is_dir())
                with patch.object(h3_worker_module.subprocess, "Popen") as popen:
                    popen.return_value.pid = 9876
                    submitted = h3_worker_module._submit(inbox / job_id)
                    repeated = h3_worker_module._submit(inbox / job_id)
                    self.assertEqual(submitted["status"], "SUBMITTED")
                    self.assertEqual(repeated["status"], "QUEUED")
                    self.assertEqual(popen.call_count, 1)

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

    def test_gpu_submission_gate_requires_ready_worker(self) -> None:
        project = Path(__file__).resolve().parents[1]
        config = {"remote": {"root": "/root/autodl-tmp/ai_interview"}}
        connection = {"port": 22, "target": "root@example.invalid"}
        ready = {"schema_version": "ai-interview-h3-remote-v1", "status": "READY"}
        with (
            patch("ai_interview.remote.load_remote_config", return_value=config),
            patch("ai_interview.remote.resolve_connection", return_value=connection),
            patch("ai_interview.remote._worker", return_value=ready) as worker,
        ):
            self.assertEqual(require_live_gpu(project), ready)
            worker.assert_called_once_with(
                config,
                connection,
                ["doctor", "--json"],
                timeout=60,
                accept_nonzero_json=True,
            )

        for status in ("CONFIGURED_NO_GPU", "BLOCKED", "FAIL", None):
            with self.subTest(status=status):
                response = {"schema_version": "ai-interview-h3-remote-v1", "status": status}
                with (
                    patch("ai_interview.remote.load_remote_config", return_value=config),
                    patch("ai_interview.remote.resolve_connection", return_value=connection),
                    patch("ai_interview.remote._worker", return_value=response),
                ):
                    with self.assertRaisesRegex(RuntimeError, "GPU submission refused"):
                        require_live_gpu(project)

    def test_component_gpu_gate_uses_isolated_worker_doctor(self) -> None:
        project = Path(".")
        config = {"remote": {"root": "/remote/root"}}
        connection = {"port": 22, "target": "root@host"}
        ready = {"schema_version": "ai-interview-voice-v1", "status": "READY"}
        with (
            patch("ai_interview.remote.load_remote_config", return_value=config),
            patch("ai_interview.remote.resolve_connection", return_value=connection),
            patch("ai_interview.remote._worker", return_value=ready) as worker,
        ):
            self.assertEqual(
                require_worker_ready(project, "voice_worker.py", "ai-interview-voice-v1", "IndexTTS"),
                ready,
            )
        args = worker.call_args.args
        self.assertEqual(args[2], ["doctor", "--json"])
        self.assertEqual(worker.call_args.kwargs["entrypoint"], "voice_worker.py")
        self.assertEqual(worker.call_args.kwargs["expected_schema"], "ai-interview-voice-v1")

        with (
            patch("ai_interview.remote.load_remote_config", return_value=config),
            patch("ai_interview.remote.resolve_connection", return_value=connection),
            patch("ai_interview.remote._worker", return_value={**ready, "status": "CONFIGURED_NO_GPU"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "IndexTTS worker must report READY"):
                require_worker_ready(project, "voice_worker.py", "ai-interview-voice-v1", "IndexTTS")

    def test_component_doctor_cli_reports_no_gpu_as_configured(self) -> None:
        cases = (
            ("voice-doctor", "ai_interview.cli.voice_worker_doctor", "ai-interview-voice-v1"),
            ("voice-design-doctor", "ai_interview.cli.voice_design_worker_doctor", "ai-interview-voice-design-v1"),
        )
        for command, target, schema in cases:
            with self.subTest(command=command):
                output = io.StringIO()
                result = {"schema_version": schema, "status": "CONFIGURED_NO_GPU", "checks": {"runtime": "PASS"}}
                with patch(target, return_value=result), patch("sys.stdout", output), patch("sys.argv", ["ai-interview", command]):
                    self.assertEqual(cli_main(), 0)
                self.assertEqual(json.loads(output.getvalue()), result)

    def test_remote_audit_is_read_only_and_validates_schema(self) -> None:
        report = {
            "schema_version": "ai-interview-remote-audit-v1",
            "status": "PASS",
            "storage": {"free": 123},
            "partial_downloads": [],
            "deployment_artifacts": [],
        }
        completed = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout=json.dumps(report), stderr=""
        )
        with (
            patch("ai_interview.remote.load_remote_config", return_value={"remote": {"root": "/root/project"}}),
            patch("ai_interview.remote.resolve_connection", return_value={"port": 22, "target": "root@example.invalid"}),
            patch("ai_interview.remote.subprocess.run", return_value=completed) as run,
        ):
            self.assertEqual(audit_remote("."), report)
            command = run.call_args.args[0][-1]
            self.assertIn("remote-audit-v1", command)
            self.assertIn("ConnectTimeout=20", run.call_args.args[0])
            self.assertIn("ConnectionAttempts=1", run.call_args.args[0])
            self.assertIn("_display_audit_path(path, root)", command)
            self.assertIn(".upload-*.tar.gz", command)
            self.assertIn("deployment_artifacts", command)
            self.assertNotIn("rm -", command)

        namespace: dict[str, object] = {}
        exec(_REMOTE_AUDIT_PATH_HELPER, namespace)
        display = namespace["_display_audit_path"]
        root = Path("/root/autodl-tmp/ai_interview")
        self.assertEqual(display(root / "models" / "partial.tmp", root), "models/partial.tmp")
        self.assertEqual(display(Path("/root/.cache/pip/download.part"), root), "/root/.cache/pip/download.part")

        with (
            patch("ai_interview.remote.load_remote_config", return_value={"remote": {"root": "/root/project"}}),
            patch("ai_interview.remote.resolve_connection", return_value={"port": 22, "target": "root@example.invalid"}),
            patch("ai_interview.remote.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 90)),
        ):
            unreachable = audit_remote(".")
            self.assertEqual(unreachable["status"], "UNREACHABLE")

    def test_remote_job_sync_never_uses_template_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            template = project / "episodes" / "_template"
            request = template / "voice_design_jobs" / "job_123" / "job.json"
            request.parent.mkdir(parents=True)
            request.write_text(json.dumps({"episode_id": "EP0001_golden_clip", "job_id": "job_123"}))
            (template / "episode.yaml").write_text("episode_id: EP0001_golden_clip\n")
            with patch("ai_interview.cli.Path.cwd", return_value=project):
                self.assertIsNone(_manifest_for_remote_job("EP0001_golden_clip", "job_123", "voice_design_jobs"))

    def test_postprocess_worker_hash_contract_and_cleanup_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            app = runtime / "apps" / "LatentSync"
            (app / "scripts").mkdir(parents=True)
            (app / "scripts" / "inference.py").write_text("# fixture\n", encoding="utf-8")
            python = runtime / "envs" / "latentsync" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            python.chmod(0o755)
            model = runtime / "models/lipsync/LatentSync-1.6/latentsync_unet.pt"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            job_dir = runtime / "postprocess/jobs/latentsync/inbox/EP0001_test_S001_latentsync_r01"
            (job_dir / "assets").mkdir(parents=True)
            video, audio = job_dir / "assets/input.mp4", job_dir / "assets/edit_audio.wav"
            video.write_bytes(b"video")
            wav(audio)
            job = {
                "schema_version": postprocess_worker.PROTOCOL,
                "job_id": job_dir.name,
                "episode_id": "EP0001_test",
                "shot_id": "S001",
                "mode": "latentsync",
                "selected_by": "director",
                "timing": {"audio_sample_rate": 48000},
                "inputs": {"video": "assets/input.mp4", "audio": "assets/edit_audio.wav"},
                "input_sha256": {"assets/input.mp4": sha256_file(video), "assets/edit_audio.wav": sha256_file(audio)},
                "budget": {"remaining_gpu_minutes_per_shot": 15, "remaining_gpu_minutes_per_episode": 100},
                "parameters": {"inference_steps": 20, "guidance_scale": 1.5},
            }
            (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            with (
                patch.object(postprocess_worker, "RUNTIME", runtime),
                patch.object(postprocess_worker, "LATENTSYNC_ROOT", app),
                patch.object(postprocess_worker, "LATENTSYNC_PYTHON", python),
            ):
                self.assertEqual(postprocess_worker.validate_job(job_dir)["mode"], "latentsync")
                video.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "hash-mismatched"):
                    postprocess_worker.validate_job(job_dir)

                imported = runtime / "postprocess/jobs/seedvr2/complete/EP0001_test_S001_seedvr2_r01"
                output = imported / "work/result.mp4"
                output.parent.mkdir(parents=True)
                output.write_bytes(b"verified-output")
                digest = sha256_file(output)
                (imported / "result.json").write_text(json.dumps({
                    "schema_version": postprocess_worker.PROTOCOL,
                    "job_id": imported.name,
                    "status": "COMPLETE",
                    "output": "work/result.mp4",
                    "sha256": digest,
                }), encoding="utf-8")
                self.assertEqual(postprocess_worker._cleanup(imported.name, digest)["status"], "CLEANED")
                self.assertFalse(imported.exists())
                with self.assertRaises(FileNotFoundError):
                    postprocess_worker._cleanup(imported.name, digest)

    def test_postprocess_worker_doctor_json_entrypoint(self) -> None:
        payload = {"schema_version": postprocess_worker.PROTOCOL, "mode": "latentsync", "status": "CONFIGURED_NO_GPU", "checks": []}
        output = io.StringIO()
        with patch.object(postprocess_worker, "_doctor", return_value=payload), patch("sys.stdout", output):
            self.assertEqual(postprocess_worker.main(["doctor", "--mode", "latentsync", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), payload)

    def test_postprocess_publish_reuses_identical_inbox_and_preserves_conflict_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            app = runtime / "apps" / "LatentSync"
            (app / "scripts").mkdir(parents=True)
            (app / "scripts" / "inference.py").write_text("# fixture\\n", encoding="utf-8")
            python = runtime / "envs" / "latentsync" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            python.chmod(0o755)
            model = runtime / "models/lipsync/LatentSync-1.6/latentsync_unet.pt"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")

            job_id = "EP0001_test_S001_latentsync_r01"
            inbox = runtime / "postprocess/jobs/latentsync/inbox"

            def make_stage(name: str, video_bytes: bytes) -> Path:
                stage = inbox / name
                (stage / "assets").mkdir(parents=True)
                video = stage / "assets/input.mp4"
                audio = stage / "assets/edit_audio.wav"
                video.write_bytes(video_bytes)
                wav(audio)
                request = {
                    "schema_version": postprocess_worker.PROTOCOL,
                    "job_id": job_id,
                    "episode_id": "EP0001_test",
                    "shot_id": "S001",
                    "mode": "latentsync",
                    "selected_by": "director",
                    "timing": {"audio_sample_rate": 48000},
                    "inputs": {"video": "assets/input.mp4", "audio": "assets/edit_audio.wav"},
                    "input_sha256": {"assets/input.mp4": sha256_file(video), "assets/edit_audio.wav": sha256_file(audio)},
                    "budget": {"remaining_gpu_minutes_per_shot": 15, "remaining_gpu_minutes_per_episode": 100},
                    "parameters": {"inference_steps": 20, "guidance_scale": 1.5},
                }
                (stage / "job.json").write_text(json.dumps(request), encoding="utf-8")
                return stage

            with (
                patch.object(postprocess_worker, "RUNTIME", runtime),
                patch.object(postprocess_worker, "LATENTSYNC_ROOT", app),
                patch.object(postprocess_worker, "LATENTSYNC_PYTHON", python),
            ):
                first = make_stage(f".{job_id}.upload-first", b"same-video")
                published = postprocess_worker._publish(first, job_id, "latentsync")
                self.assertEqual(published["location"], "inbox")
                self.assertTrue((inbox / job_id / "job.json").is_file())
                self.assertFalse(first.exists())

                duplicate = make_stage(f".{job_id}.upload-second", b"same-video")
                resumed = postprocess_worker._publish(duplicate, job_id, "latentsync")
                self.assertTrue(resumed["existing"])
                self.assertEqual(resumed["location"], "inbox")
                self.assertFalse(duplicate.exists())
                self.assertEqual(len(list(inbox.glob(f".{job_id}.upload-*"))), 0)

                conflicting = make_stage(f".{job_id}.upload-conflict", b"different-video")
                with self.assertRaisesRegex(FileExistsError, "different package"):
                    postprocess_worker._publish(conflicting, job_id, "latentsync")
                self.assertTrue(conflicting.is_dir())

    def test_postprocess_submit_resumes_inbox_job_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            app = runtime / "apps" / "LatentSync"
            (app / "scripts").mkdir(parents=True)
            (app / "scripts" / "inference.py").write_text("# fixture\\n", encoding="utf-8")
            python = runtime / "envs" / "latentsync" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            python.chmod(0o755)
            model = runtime / "models/lipsync/LatentSync-1.6/latentsync_unet.pt"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            job_id = "EP0001_test_S001_latentsync_r01"
            inbox = runtime / "postprocess/jobs/latentsync/inbox" / job_id
            inbox.mkdir(parents=True)
            video = inbox / "video.mp4"
            audio = inbox / "audio.wav"
            video.write_bytes(b"video")
            wav(audio)
            (inbox / "job.json").write_text(json.dumps({
                "schema_version": postprocess_worker.PROTOCOL,
                "job_id": job_id,
                "episode_id": "EP0001_test",
                "shot_id": "S001",
                "mode": "latentsync",
                "selected_by": "director",
                "timing": {"audio_sample_rate": 48000},
                "inputs": {"video": "assets/input.mp4", "audio": "assets/edit_audio.wav"},
                "input_sha256": {"assets/input.mp4": "0" * 64, "assets/edit_audio.wav": "0" * 64},
                "budget": {"remaining_gpu_minutes_per_shot": 15, "remaining_gpu_minutes_per_episode": 100},
                "parameters": {"inference_steps": 20, "guidance_scale": 1.5},
            }), encoding="utf-8")
            (inbox / "assets").mkdir()
            (inbox / "assets/input.mp4").write_bytes(b"video")
            wav(inbox / "assets/edit_audio.wav")
            request = json.loads((inbox / "job.json").read_text())
            request["input_sha256"] = {
                "assets/input.mp4": sha256_file(inbox / "assets/input.mp4"),
                "assets/edit_audio.wav": sha256_file(inbox / "assets/edit_audio.wav"),
            }
            (inbox / "job.json").write_text(json.dumps(request), encoding="utf-8")
            with (
                patch.object(postprocess_worker, "RUNTIME", runtime),
                patch.object(postprocess_worker, "LATENTSYNC_ROOT", app),
                patch.object(postprocess_worker, "LATENTSYNC_PYTHON", python),
                patch.object(postprocess_worker.subprocess, "Popen") as popen,
            ):
                popen.return_value.pid = 1234
                resumed = postprocess_worker._submit(inbox)
                self.assertEqual(resumed["status"], "SUBMITTED")
                self.assertFalse(inbox.exists())
                self.assertTrue((runtime / "postprocess/jobs/latentsync/running" / job_id / "job.json").is_file())
                self.assertEqual(popen.call_count, 1)
                again = postprocess_worker._submit(inbox)
                self.assertTrue(again["existing"])
                self.assertEqual(popen.call_count, 1)

    def test_postprocess_transport_reuses_remote_jobs_and_requires_matching_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "EP0001_test" / "postprocess_jobs" / "latentsync" / "EP0001_test_S001_latentsync_r01"
            (package / "assets").mkdir(parents=True)
            asset = package / "assets/input.mp4"
            asset.write_bytes(b"local input")
            job = {
                "schema_version": remote_postprocess.PROTOCOL,
                "job_id": package.name,
                "mode": "latentsync",
                "input_sha256": {"assets/input.mp4": sha256_file(asset)},
            }
            (package / "job.json").write_text(json.dumps(job), encoding="utf-8")
            with (
                patch("ai_interview.remote_postprocess.load_remote_config", return_value={"remote": {"root": "/remote"}}),
                patch("ai_interview.remote_postprocess.resolve_connection", return_value={"target": "root@host", "port": 22}),
                patch("ai_interview.remote_postprocess._worker", return_value={"jobs": [{"job_id": package.name, "mode": "latentsync", "status": "RUNNING", "request_sha256": sha256_file(package / "job.json")}]}),
            ):
                existing = remote_postprocess.submit_postprocess(package, root)
                self.assertTrue(existing["existing"])

            with (
                patch("ai_interview.remote_postprocess.load_remote_config", return_value={"remote": {"root": "/remote"}}),
                patch("ai_interview.remote_postprocess.resolve_connection", return_value={"target": "root@host", "port": 22}),
                patch("ai_interview.remote_postprocess._worker", return_value={"jobs": []}),
                patch("ai_interview.remote_postprocess.postprocess_doctor", return_value={"status": "CONFIGURED_NO_GPU"}),
                patch("ai_interview.remote_postprocess.subprocess.run") as remote_write,
            ):
                with self.assertRaisesRegex(RuntimeError, "not ready for GPU submission"):
                    remote_postprocess.submit_postprocess(package, root)
                remote_write.assert_not_called()

            imported = root / "imported"
            output = imported / "work/result.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"result")
            digest = sha256_file(output)
            result = {"job_id": package.name, "status": "COMPLETE", "output": "work/result.mp4", "sha256": digest}
            (imported / "result.json").write_text(json.dumps(result), encoding="utf-8")
            with (
                patch("ai_interview.remote_postprocess.load_remote_config", return_value={"remote": {"root": "/remote"}}),
                patch("ai_interview.remote_postprocess.resolve_connection", return_value={"target": "root@host", "port": 22}),
                patch("ai_interview.remote_postprocess._worker", return_value={"job_id": package.name, "status": "CLEANED", "verified_sha256": digest}) as worker,
            ):
                self.assertEqual(remote_postprocess.cleanup_postprocess(root, package.name, imported)["status"], "CLEANED")
                worker.assert_called_once()
                result["sha256"] = "0" * 64
                (imported / "result.json").write_text(json.dumps(result), encoding="utf-8")
                worker.reset_mock()
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    remote_postprocess.cleanup_postprocess(root, package.name, imported)
                worker.assert_not_called()

    def test_postprocess_transport_republishes_inbox_retry_through_worker_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "EP0001_test" / "postprocess_jobs" / "latentsync" / "EP0001_test_S001_latentsync_r01"
            (package / "assets").mkdir(parents=True)
            asset = package / "assets/input.mp4"
            asset.write_bytes(b"local input")
            job = {
                "schema_version": remote_postprocess.PROTOCOL,
                "job_id": package.name,
                "mode": "latentsync",
                "input_sha256": {"assets/input.mp4": sha256_file(asset)},
                "budget": {"remaining_gpu_minutes_per_shot": 15, "remaining_gpu_minutes_per_episode": 100},
            }
            (package / "job.json").write_text(json.dumps(job), encoding="utf-8")
            status = {"jobs": [{"job_id": package.name, "mode": "latentsync", "status": "QUEUED", "location": "inbox", "request_sha256": sha256_file(package / "job.json")}]}
            calls: list[tuple[str, ...]] = []

            def worker(_config, _connection, *arguments):
                calls.append(tuple(arguments))
                if arguments[0] == "status":
                    return status
                if arguments[0] == "publish":
                    return {"job_id": package.name, "mode": "latentsync", "location": "inbox", "existing": True}
                if arguments[0] == "submit":
                    return {"job_id": package.name, "mode": "latentsync", "status": "SUBMITTED"}
                self.fail(f"unexpected worker command: {arguments}")

            with (
                patch("ai_interview.remote_postprocess.load_remote_config", return_value={"remote": {"root": "/remote"}}),
                patch("ai_interview.remote_postprocess.resolve_connection", return_value={"target": "root@host", "port": 22}),
                patch("ai_interview.remote_postprocess._worker", side_effect=worker),
                patch("ai_interview.remote_postprocess.postprocess_doctor", return_value={"status": "READY"}),
                patch("ai_interview.remote_postprocess.shutil.which", return_value=None),
                patch("ai_interview.remote_postprocess.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as remote_call,
            ):
                result = remote_postprocess.submit_postprocess(
                    package, root,
                    on_reserve=lambda _job: {
                        "remaining_gpu_minutes_per_shot": 14,
                        "remaining_gpu_minutes_per_episode": 90,
                    },
                )

            self.assertEqual(result["status"], "SUBMITTED")
            self.assertEqual([call[0] for call in calls], ["status", "publish", "submit"])
            publish_call = calls[1]
            self.assertIn("--staging-dir", publish_call)
            self.assertIn("--job-id", publish_call)
            self.assertEqual(remote_call.call_count, 2)

    def test_postprocess_conflicting_remote_job_does_not_reserve_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "EP0001_test" / "postprocess_jobs" / "latentsync" / "EP0001_test_S001_latentsync_r01"
            (package / "assets").mkdir(parents=True)
            asset = package / "assets/input.mp4"
            asset.write_bytes(b"local input")
            (package / "job.json").write_text(json.dumps({
                "schema_version": remote_postprocess.PROTOCOL,
                "job_id": package.name, "episode_id": "EP0001_test", "shot_id": "S001",
                "mode": "latentsync", "revision": 1,
                "input_sha256": {"assets/input.mp4": sha256_file(asset)},
            }), encoding="utf-8")
            reserve = unittest.mock.Mock()
            with (
                patch("ai_interview.remote_postprocess.load_remote_config", return_value={"remote": {"root": "/remote"}}),
                patch("ai_interview.remote_postprocess.resolve_connection", return_value={"target": "root@host", "port": 22}),
                patch("ai_interview.remote_postprocess._worker", return_value={"jobs": [{
                    "job_id": package.name, "mode": "latentsync", "status": "QUEUED",
                    "location": "inbox", "request_sha256": "0" * 64,
                }]}),
                patch("ai_interview.remote_postprocess.postprocess_doctor") as doctor,
                patch("ai_interview.remote_postprocess.subprocess.run") as remote_write,
            ):
                with self.assertRaisesRegex(FileExistsError, "different package"):
                    remote_postprocess.submit_postprocess(package, root, on_reserve=reserve)
            reserve.assert_not_called()
            doctor.assert_not_called()
            remote_write.assert_not_called()

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
