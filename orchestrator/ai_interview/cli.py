from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .config import load_pipeline
from .audio import build_audio_master, build_edit_mix, generate_srt, select_take
from .manifest import load_manifest
from .media import build_roughcut_command, deterministic_qc, final_master_qc, local_tool_status, lock_edit, select_video_candidate
from .package import package_h3, package_voice, package_voice_design
from .remote import cleanup_completed, doctor as remote_doctor, pull, status, submit
from .remote import resume as remote_resume
from .remote_voice import pull_voice, submit_voice, voice_status
from .remote_voice_design import pull_voice_design, submit_voice_design, voice_design_status
from .review import approve_final, load_checklist
from .state import begin_h3_attempt, read_budget, record_remote_job, record_voice_design_remote, record_voice_remote
from .voice_design import freeze_voice_reference


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-interview")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("remote-doctor")
    validate = commands.add_parser("validate")
    validate.add_argument("manifest")
    voice = commands.add_parser("package-voice")
    voice.add_argument("manifest")
    voice_design = commands.add_parser("package-voice-design")
    voice_design.add_argument("manifest")
    voice_design.add_argument("character_id")
    voice_design.add_argument("--candidates", type=int, default=3)
    submit_voice_parser = commands.add_parser("submit-voice")
    submit_voice_parser.add_argument("package")
    voice_status_parser = commands.add_parser("voice-status")
    voice_status_parser.add_argument("episode_id")
    voice_status_parser.add_argument("--job-id")
    pull_voice_parser = commands.add_parser("pull-voice")
    pull_voice_parser.add_argument("job_id")
    pull_voice_parser.add_argument("destination")
    submit_design_parser = commands.add_parser("submit-voice-design")
    submit_design_parser.add_argument("package")
    design_status_parser = commands.add_parser("voice-design-status")
    design_status_parser.add_argument("episode_id")
    design_status_parser.add_argument("--job-id")
    pull_design_parser = commands.add_parser("pull-voice-design")
    pull_design_parser.add_argument("job_id")
    pull_design_parser.add_argument("destination")
    freeze_voice_parser = commands.add_parser("freeze-voice-reference")
    freeze_voice_parser.add_argument("manifest")
    freeze_voice_parser.add_argument("character_id")
    freeze_voice_parser.add_argument("metadata")
    freeze_voice_parser.add_argument("--selected-by", required=True)
    h3 = commands.add_parser("package-h3")
    h3.add_argument("manifest")
    h3.add_argument("shot_id")
    submit_h3 = commands.add_parser("submit-h3")
    submit_h3.add_argument("package")
    resume_h3 = commands.add_parser("resume-h3")
    resume_h3.add_argument("job_id")
    cleanup_h3 = commands.add_parser("cleanup-h3")
    cleanup_h3.add_argument("job_id")
    cleanup_h3.add_argument("local_import")
    budget = commands.add_parser("budget")
    budget.add_argument("manifest")
    h3_status = commands.add_parser("h3-status")
    h3_status.add_argument("episode_id")
    h3_status.add_argument("--shot-id")
    pull_h3 = commands.add_parser("pull-h3")
    pull_h3.add_argument("job")
    pull_h3.add_argument("destination")
    qc = commands.add_parser("qc-media")
    qc.add_argument("path")
    qc_final = commands.add_parser("qc-final")
    qc_final.add_argument("path")
    qc_final.add_argument("--duration", type=float)
    qc_final.add_argument("--fps", type=int, choices=(24, 25), default=24)
    approve = commands.add_parser("approve-final")
    approve.add_argument("manifest")
    approve.add_argument("final_path")
    approve.add_argument("--duration", type=float, required=True)
    approve.add_argument("--fps", type=int, choices=(24, 25), default=24)
    approve.add_argument("--checklist", required=True)
    approve.add_argument("--selected-by", required=True)
    choose_voice = commands.add_parser("select-take")
    choose_voice.add_argument("manifest")
    choose_voice.add_argument("beat_id")
    choose_voice.add_argument("metadata")
    choose_voice.add_argument("--selected-by", required=True)
    audio_master = commands.add_parser("build-audio-master")
    audio_master.add_argument("manifest")
    audio_mix = commands.add_parser("build-edit-mix")
    audio_mix.add_argument("manifest")
    subtitle = commands.add_parser("subtitles")
    subtitle.add_argument("manifest")
    subtitle.add_argument("--output")
    choose_video = commands.add_parser("select-video")
    choose_video.add_argument("manifest")
    choose_video.add_argument("shot_id")
    choose_video.add_argument("metadata")
    choose_video.add_argument("--selected-by", required=True)
    choose_video.add_argument("--lip-grade", required=True, choices=("A", "B", "C", "D"))
    edit_lock = commands.add_parser("lock-edit")
    edit_lock.add_argument("manifest")
    edit_lock.add_argument("--selected-by", required=True)
    rough = commands.add_parser("roughcut")
    rough.add_argument("manifest")
    rough.add_argument("output")
    rough.add_argument("--execute", action="store_true")
    return parser


def _manifest_for_remote_job(episode_id: str, job_id: str, jobs_dir: str) -> tuple[Path, Path] | None:
    episode_root = Path.cwd() / "episodes"
    direct = episode_root / episode_id
    candidates = [direct, *sorted(path for path in episode_root.iterdir() if path.is_dir() and path != direct)] if episode_root.is_dir() else []
    for root in candidates:
        request = root / jobs_dir / job_id / "job.json"
        manifest_path = root / "episode.yaml"
        if request.is_file() and manifest_path.is_file():
            try:
                job = json.loads(request.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("episode_id") == episode_id and job.get("job_id") == job_id:
                return manifest_path, request
    return None


def main() -> int:
    args = _parser().parse_args()
    if args.command == "doctor":
        pipeline = load_pipeline()
        checks = local_tool_status()
        checks["local_generation_models"] = {
            "status": "PASS", "detail": "none required; inference is remote on AutoDL"
        }
        checks["pipeline_config"] = {
            "status": "PASS", "schema_version": pipeline["schema_version"]
        }
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if all(item["status"] == "PASS" for item in checks.values()) else 1
    if args.command == "remote-doctor":
        result = remote_doctor(Path.cwd())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 2
    if args.command == "submit-h3":
        package_path = Path(args.package).resolve()
        job = json.loads((package_path / "job.json").read_text(encoding="utf-8"))
        manifest_path = package_path.parent.parent / "episode.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError("H3 package must be inside episodes/<episode_id>/remote_jobs/<job_id>")
        result = submit(
            package_path,
            Path.cwd(),
            on_submit=lambda: begin_h3_attempt(manifest_path, job["shot_id"], job["job_id"]),
        )
        if result.get("job_id") and not result.get("existing"):
            record_remote_job(manifest_path, job["shot_id"], result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "resume-h3":
        print(json.dumps(remote_resume(Path.cwd(), args.job_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "cleanup-h3":
        print(json.dumps(cleanup_completed(Path.cwd(), args.job_id, args.local_import), ensure_ascii=False, indent=2))
        return 0
    if args.command == "submit-voice":
        package_path = Path(args.package).resolve()
        job = json.loads((package_path / "job.json").read_text(encoding="utf-8"))
        manifest_path = package_path.parent.parent / "episode.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError("voice package must be inside episodes/<episode_id>/voice_jobs/<job_id>")
        result = submit_voice(
            package_path,
            Path.cwd(),
            on_submit=lambda: record_voice_remote(manifest_path, job, {"job_id": job["job_id"], "status": "SUBMITTED"}),
        )
        if result.get("existing"):
            record_voice_remote(manifest_path, job, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "voice-status":
        result = voice_status(Path.cwd(), args.episode_id, args.job_id)
        for remote_job in result.get("jobs", []):
            found = _manifest_for_remote_job(args.episode_id, remote_job["job_id"], "voice_jobs")
            if found:
                manifest_path, request = found
                record_voice_remote(manifest_path, json.loads(request.read_text(encoding="utf-8")), remote_job)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "pull-voice":
        print(pull_voice(Path.cwd(), args.job_id, args.destination))
        return 0
    if args.command == "submit-voice-design":
        package_path = Path(args.package).resolve()
        job = json.loads((package_path / "job.json").read_text(encoding="utf-8"))
        manifest_path = package_path.parent.parent / "episode.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError("VoiceDesign package must be inside episodes/<episode_id>/voice_design_jobs/<job_id>")
        result = submit_voice_design(package_path, Path.cwd(), on_submit=lambda: record_voice_design_remote(manifest_path, job, {"job_id": job["job_id"], "status": "SUBMITTED"}))
        if result.get("existing"):
            record_voice_design_remote(manifest_path, job, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "voice-design-status":
        result = voice_design_status(Path.cwd(), args.episode_id, args.job_id)
        for remote_job in result.get("jobs", []):
            found = _manifest_for_remote_job(args.episode_id, remote_job["job_id"], "voice_design_jobs")
            if found:
                manifest_path, job_path = found
                record_voice_design_remote(manifest_path, json.loads(job_path.read_text(encoding="utf-8")), remote_job)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "pull-voice-design":
        print(pull_voice_design(Path.cwd(), args.job_id, args.destination))
        return 0
    if args.command == "freeze-voice-reference":
        print(json.dumps(freeze_voice_reference(args.manifest, args.character_id, args.metadata, args.selected_by), ensure_ascii=False, indent=2))
        return 0
    if args.command == "budget":
        print(json.dumps(read_budget(args.manifest), ensure_ascii=False, indent=2))
        return 0
    if args.command == "h3-status":
        result = status(Path.cwd(), args.episode_id, args.shot_id)
        manifest_path = Path.cwd() / "episodes" / args.episode_id / "episode.yaml"
        if manifest_path.is_file():
            for remote_job in result.get("jobs", []):
                if remote_job.get("shot_id"):
                    record_remote_job(manifest_path, remote_job["shot_id"], remote_job)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "pull-h3":
        print(pull(Path.cwd(), args.job, args.destination))
        return 0
    if args.command == "qc-media":
        result = deterministic_qc(args.path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "qc-final":
        result = final_master_qc(args.path, expected_duration=args.duration, fps=args.fps)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "approve-final":
        result = approve_final(args.manifest, args.final_path, load_checklist(args.checklist), args.selected_by, expected_duration=args.duration, fps=args.fps)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "select-take":
        print(json.dumps(select_take(args.manifest, args.beat_id, args.metadata, args.selected_by), ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-audio-master":
        print(json.dumps(build_audio_master(args.manifest), ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-edit-mix":
        print(json.dumps(build_edit_mix(args.manifest), ensure_ascii=False, indent=2))
        return 0
    if args.command == "subtitles":
        print(generate_srt(args.manifest, args.output))
        return 0
    if args.command == "select-video":
        print(json.dumps(select_video_candidate(args.manifest, args.shot_id, args.metadata, args.selected_by, args.lip_grade), ensure_ascii=False, indent=2))
        return 0
    if args.command == "lock-edit":
        print(json.dumps(lock_edit(args.manifest, args.selected_by), ensure_ascii=False, indent=2))
        return 0
    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    if args.command == "validate":
        print(json.dumps({"status": "PASS", "episode_id": manifest["episode_id"]}, ensure_ascii=False))
        return 0
    if args.command == "package-voice":
        print(package_voice(manifest_path))
        return 0
    if args.command == "package-voice-design":
        print(package_voice_design(manifest_path, args.character_id, args.candidates))
        return 0
    if args.command == "package-h3":
        print(package_h3(manifest_path, args.shot_id))
        return 0
    if args.command == "roughcut":
        output = Path(args.output).resolve()
        command = build_roughcut_command(manifest_path.parent, manifest, output)
        if not args.execute:
            print(json.dumps(command, ensure_ascii=False, indent=2))
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(command, check=False).returncode
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
