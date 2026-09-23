# AutoDL Worker Rules

This directory is deployed to `/root/autodl-tmp/ai_interview/worker`. It is the
remote GPU execution boundary; creative decisions remain on the local control
plane.

- Keep the worker dependency-light and callable with the AutoDL image's
  `/root/miniconda3/bin/python`.
- Bind ComfyUI to `127.0.0.1:8188`. Do not add public listeners or credentials
  to source files.
- Accept only `ai-interview-h3-remote-v1` jobs, validate all relative paths and
  hashes, and preserve non-identical existing results.
- Read model/runtime locations from `AI_INTERVIEW_*` environment variables;
  defaults must remain under `/root/autodl-tmp/ai_interview`.
- A no-card AutoDL session may report `CONFIGURED_NO_GPU`; only a live GPU plus
  reachable ComfyUI and required nodes may report `READY`.
- Do not combine H3, IndexTTS/Qwen3-TTS, LatentSync, or optional MuseTalk into
  one Python environment. SeedVR2 is the only approved ComfyUI custom node.
- Never use local VoxCPM2 or add it as a fallback. Voice inference is remote:
  IndexTTS-2.5 for production and Qwen3-TTS for voice design/base cloning.
- `scripts/remote_deploy_models.sh` is the approved first-deployment route:
  Hugging Face (with AutoDL network turbo) -> this instance's `models/...`
  runtime directory. It validates every expected file byte size and must never
  route model bytes through the local Mac.
- OSS is not an initial-download relay on the same instance: it adds a full
  upload and download. Use it only for recovery of an already archived model or
  an explicitly requested backup. Never persist, print, or copy OSS credentials
  into project files, logs, jobs, or reports.
- Do not delete completed jobs until local import and result hash verification
  have succeeded.
