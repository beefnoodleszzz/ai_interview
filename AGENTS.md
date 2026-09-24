# AI Interview Production Agent Standard

## Mission

Build and operate a repeatable AI-native interview production system. The local
Mac is the production control plane. AutoDL is a remote GPU worker. Never blur
that boundary by installing remote GPU stacks into this repository.

Optimization order:

1. script and character chemistry
2. voice performance and timing
3. reaction/edit rhythm
4. identity and studio continuity
5. lip sync and local visual quality
6. repeatability, cost, and speed

For end-to-end episode production or resuming an episode, use the project skill
`.agents/skills/ai-interview-production/SKILL.md`. This file remains the
authority for production boundaries and gates.

## Sources of Truth

- `episodes/<episode_id>/episode.yaml` is the canonical episode state.
- A final H3 prompt artifact is immutable input. Python validates and packages
  it byte-for-byte; it must not rewrite creative wording.
- A selected 48 kHz WAV is the dialogue timing authority.
- A versioned ComfyUI workflow on AutoDL is the render template; local code may
  inject declared fields only.
- Beads tracks durable engineering tasks and blockers. It does not replace the
  episode manifest.

## Execution Boundary

### Local Mac — required control plane

- script, beats, Episode Manifest, Character/Studio Bible, shot design
- H3 prompt authoring and structural validation
- build versioned IndexTTS/Qwen3-TTS/ASR request packages for AutoDL
- audio timing, silence insertion, loudness checks and 48 kHz master handoff
- H3 job packaging, SHA-256 manifests, SSH transfer and result import
- deterministic `ffprobe`/decode/black/freeze/silence checks
- candidate review records, hard-cut rough edit, subtitles and final packaging
- Beads planning, logs, reports and backups of irreplaceable project assets

No generation model is installed or executed on the local Mac by default.

### AutoDL — GPU worker only

- ComfyUI and MiniMax H3 FL2VA/Ref2VA models
- IndexTTS-2.5 as the primary dialogue engine
- Qwen3-TTS VoiceDesign for creating a voice, then a fixed voice reference
- Whisper, FunASR or WhisperX for transcript QC
- remote H3 workflow execution and native ambience generation
- LatentSync 1.6 only for locally classified `NEEDS_LIPSYNC` shots
- SeedVR2 only after edit lock; MuseTalk only when explicitly benchmarked
- version/runtime reporting and result-side media metadata

ComfyUI must listen on remote `127.0.0.1`. Access it through the worker over
SSH; never expose port 8188 directly to the public internet.

### Not in the current mandatory path

- Existing local TTS systems, including local VoxCPM2 BF16, are outside this
  project's voice path. Do not install, invoke, or use them as a fallback.
- Resolve is optional premium finishing. FFmpeg must be able to build the full
  local rough cut without Resolve.
- H3 Context-IR and Regenerate-2K are optional paid API evaluations.

## Required Production Gates

```text
script
→ episode manifest
→ voice cast and Audio Master
→ shot plan and final H3 prompt
→ local validation/package
→ AutoDL render
→ local import and deterministic QC
→ director selection
→ optional remote LipSync repair
→ local rough cut/subtitles/mix
→ edit lock
→ optional remote SeedVR2
→ final local QC
```

No 70–100 second episode production begins before the 15–20 second golden clip
passes identity, studio, voices, lip sync, reaction timing, room tone and cuts.

## H3 Prompt Contract

- Use the official `h3-prompt-writing` structure.
- Base modes use, in order: `integrated_multimodal_description`,
  `overall_soundscape`, `non_diegetic_music`.
- Ref2VA uses, in order: `subject_definitions`, `summary`,
  `retention_analysis`, `detailed_description`, `overall_soundscape`,
  `non_diegetic_music`.
- Section prose is English. Dialogue and visible text retain their original
  language. Dialogue uses stable `(Sx)` IDs and `<d>[Language] ...</d>`.
- Ref2VA audio may not be the only reference: at least one image or video is
  mandatory. Maximums are 9 images, 3 videos, 3 audios, 12 mixed assets; video
  and audio reference totals are each at most 15 seconds.
- Generate within H3's practical 4–15 second range. Keep edit duration separate
  and trim locally.

## State and Budget Rules

Shot states:

```text
PLANNED → AUDIO_RENDERING → AUDIO_QC → READY_FOR_H3 → H3_RENDERING
→ H3_QC → NEEDS_RETRY | NEEDS_LIPSYNC | READY_FOR_EDIT
→ UPSCALED → DONE
```

- Maximum 4 attempts per shot.
- Maximum 15 GPU minutes per shot.
- Maximum 300 GPU minutes per episode.
- Budget exhaustion moves work to `NEEDS_MANUAL_REVIEW`; it never silently
  retries.
- Every remote submission uses a unique `job_id`. Re-submission first checks
  existing remote state.
- A failed job is not overwritten. Create a new revision.

## Voice Rules

- IndexTTS-2.5 is the remote production TTS. Qwen3-TTS VoiceDesign creates
  initial voices; approved voices are frozen into versioned references before
  episode production.
- Keep TTS, ASR and their environments separate from ComfyUI/H3.
- Preserve raw WAV and JSON metadata. Convert to 48 kHz for the local Audio
  Master without overwriting the remote raw result or canonical reference.
- ASR and numeric QC cannot approve acting quality. A human/director selects the
  final take.
- Only use voice references the user owns or is authorized to clone.

## Local QC Order

1. file existence, hash and manifest contract
2. ffprobe streams/duration/fps/dimensions/audio
3. full decode, black/freeze/silence checks
4. proxy/contact sheet
5. representative-frame semantic review
6. human/director review

Never run expensive semantic review before deterministic checks.

## Repository and Safety Rules

- Do not commit generated WAV/MP4, model weights, secrets, AutoDL credentials,
  cookies or `.env.local`.
- Keep irreplaceable Bible, manifest, prompt, workflow and benchmark files in
  version control and an external backup.
- Preserve existing outputs. Packaging and import operations refuse to replace
  non-identical directories.
- Remote deletion requires a verified local import and matching result SHA-256.
- Do not start a paid GPU, submit a render, publish content, or change external
  systems unless the current request authorizes it.

## Beads Workflow

- Start with `bd ready`, claim the active issue, and keep dependencies accurate.
- Close an issue only after its acceptance checks pass.
- Record external-cost or unavailable-GPU work as blocked/open; do not pretend
  remote execution succeeded.
- At handoff, report changed files, commands run, test results and the exact
  remaining external action.

## Completion Protocol

Before calling local setup complete:

- `python -m unittest discover -s tests -v` passes in the project environment;
- the template manifest validates;
- local doctor reports FFmpeg, ffprobe, SSH and configuration status explicitly;
- a fixture H3 package is byte/hash verified;
- no GPU model or generated media has entered Git;
- Beads shows only genuinely remote/cost-bearing work unfinished.
