# Remote Contracts

## H3

- Schema: `ai-interview-h3-remote-v1`
- Worker: AutoDL `/root/autodl-tmp/ai_interview`
- Modes: `i2va`, `fl2va`, `ref2va`
- Local package: `job.json`, `prompt.txt`, `assets/*`, SHA-256 map
- Ref2VA input lists: `reference_images` (PNG/JPEG/WebP, max 9),
  `reference_videos` (MP4/MOV/MKV/WebM, max 3), and `reference_audio`
  (WAV/MP3/M4A/FLAC/OGG, max 3). Combined references are limited to 12.
- Reference audio duration and reference video duration are each limited to 15
  seconds total; every individual clip must be 2–15 seconds. At least one image
  or video is required when using Ref2VA.
- The fixed ComfyUI graph loads video frames and embedded soundtracks through
  `LoadVideo` + `GetVideoComponents`, and standalone audio through `LoadAudio`.
- Remote result: `result.json`, candidate MP4, per-candidate metadata

## Voice

- Schema: `ai-interview-voice-v1`
- Primary backend: `IndexTTS-2.5`
- Voice creation: `Qwen3-TTS VoiceDesign` outside episode rendering; accepted output becomes a versioned reference
- Local package: exact line text, voice ID, reference path/hash, emotion, pause, candidate count
- Remote result: raw WAV, generation metadata and ASR result
- Local acceptance: integrity/format QC plus human listening; ASR alone cannot approve a Take
