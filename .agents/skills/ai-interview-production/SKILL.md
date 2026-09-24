---
name: ai-interview-production
description: Produce or resume an episode in this AI interview repository, from script and voice through AutoDL H3 renders, local edit, subtitles, QC, and director sign-off. Use for end-to-end episode work; use narrower project commands for an isolated fix.
---

# AI Interview Production

Use this skill from the repository root. Read `AGENTS.md` first: it owns the production boundary, budgets, permissions, and required gates. `episodes/<episode_id>/episode.yaml` owns episode state; Beads owns engineering tasks. The detailed command sequence is in `docs/how-to/production-runbook.md`. Read only the relevant section when resuming a partial episode.

## Start or resume

1. Run `bd ready`, inspect the target Manifest, `uv run ai-interview budget <episode.yaml>`, and the latest handoff/review record. Determine the first incomplete gate. Do not restart completed jobs or copy an old episode's runtime state into a new one.
2. Check `uv run ai-interview doctor` and `validate <episode.yaml>`. Before any remote call, verify the current instance/SSH target from `.env.local` without printing secrets; query remote job state before a retry. Start or submit paid work only when the current request authorizes it.
3. State the next artifact and acceptance check, then execute through that gate. Preserve failed jobs, generated results, prior locks, and earlier candidate revisions.

## Production gates

| Gate | Required evidence before advancing |
| --- | --- |
| Story and references | Final script/Beat mapping, Bible, approved visual and voice references, shot plan, immutable final H3 prompts |
| Audio | Selected IndexTTS takes, raw WAV/JSON, ASR as text QC, 48 kHz Audio Master, human voice-performance review |
| H3 | Valid local package and hashes; remote doctor `READY`; unique job ID, budget, remote result import, deterministic QC, director candidate selection |
| Edit | Room-tone mix, waveform-based H3 video/audio alignment, selected media, versioned edit lock, rough cut and subtitle |
| Final | Full playback, lip/reaction/identity/studio/audio/subtitle review, `qc-final`, checklist evidence, signed final revision |

The 15–20 second golden clip must pass before producing a 70–100 second episode. `qc-final` proves file integrity and signal properties; it does not prove acting, mouth sync, or pacing. If full playback finds a defect, fix that shot and create a new edit/final revision before sign-off.

## Checks that prevented EP0001 failures

- **Align each speaking shot before locking.** Compare the selected H3 candidate's embedded audio with its selected 48 kHz WAV. Measure the waveform offset, then ensure `edit_in_sec` matches it within about 0.1 s; check the cut's first and last spoken sounds and several mouth frames. In EP0001 S003, `edit_in_sec=0.47` versus a 0.9495 s audio offset made “本来？” about 0.48 s late. A correct waveform offset with bad mouth shapes points to H3 lip generation, not a local slip. See [sync checks](references/ep0001-lessons.md#mouth-and-audio-alignment).
- **Keep one subtitle layer.** Use an explicit CJK-capable font, inspect rendered Chinese glyphs, and check cue start/end frames. The project renderer `scripts/render_burned_subtitles.py` makes a compact right-lower burned layer on macOS; keep the source SRT in `07_edit/` and avoid a same-basename SRT next to the review MP4 because players may auto-load it. See [subtitle checks](references/ep0001-lessons.md#subtitle-rendering).
- **Preserve input and edit history.** Final prompts and remote packages are immutable. A changed shot timing requires an archived prior edit lock and a new lock revision. Write new rough cuts and final candidates to new paths, never overwrite the previous review copy. Re-run deterministic QC and verify audio remains unchanged when only video timing changes.
- **Keep subjective gates honest.** Contact sheets and representative frames help select candidates; they cannot replace watching the entire rendered cut with sound. Record what the director actually reviewed. Do not mark unobserved checklist items PASS.

## Handoff

Report the current candidate path, Manifest status, selected job revisions, GPU used/reserved, deterministic QC, human review result, and exact remaining gate. Update Beads only when its acceptance criteria are evidenced. Stop or shut down a paid remote instance when authorized and no remote job remains; confirm its console state.
