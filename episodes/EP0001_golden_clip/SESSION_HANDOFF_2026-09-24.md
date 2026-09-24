# EP0001 golden clip — session handoff (2026-09-24, Asia/Shanghai)

The user paused work because the Codex session quota was reached and asked to continue in a new session. The active goal remains to finish and QC the 15.8-second golden clip, then shut down the AutoDL instance in Ego. Do not ask the user to review images or videos: the agent is authorized to decide. Do not start the full episode before the golden clip passes.

## Current remote state

- AutoDL console instance: `259341893e-a8a1301c` (Beijing B, RTX 5090), cloned from the earlier test instance. User-provided SSH command/keyless login is in `.env.local`. Never print credentials.
- Ego TaskSpace `1`: `p1` AutoDL console, `p2` signed-in JupyterLab. Keep it for final shutdown at `https://www.autodl.com/console/instance/list?_random_=1790180526773`. Do not shut down until generation and import are complete.
- ComfyUI is running on remote `127.0.0.1:8188` from `scripts/start_comfyui.sh` and passed remote doctor before S001 submission.
- `EP0001_golden_clip_S001_r01` FAILED after 0.2 GPU minutes because the worker assigned the video VAE to the audio VAE node. Fixed locally in `remote/ai_interview_h3_worker/worker.py`, regression assertion added in tests, and that file alone deployed to remote with backup under `worker/backups/ep0001-audio-vae-fix/`.
- `EP0001_golden_clip_S001_r02` was SUBMITTED and RUNNING. Last successful status: `candidate_index=3`, `gpu_minutes=11.763`, stage `comfy_submit`. Candidates 01 and 02 already exist in remote `jobs/work/EP0001_golden_clip_S001_r02/`; local previews in `/tmp/EP0001_S001_candidate_01_preview.mp4` and `_02_preview.mp4`. Candidate 01 has ffprobe/full-decode PASS, visual contact sheet `/tmp/ep0001_s001_contact.png`; visually coherent Guest, costume, microphone, axis and mouth movement. Third candidate may hit the 14.609 GPU-minute reservation before completion; inspect state and preserve any completed candidates. Two later SSH status attempts returned `Connection closed by 198.18.1.133 port 33070`, likely transient proxy/gateway trouble. Do not repeatedly submit S001.
- S002–S005 local r02 packages have `candidate_count=1` to stay within the 15-minute shot budget. Their assets, prompt and `job.json` are already staged, **not published or submitted**, under remote `staging/h3_upload/EP0001_golden_clip_S00{2..5}_r02`. The corresponding r01 packages/stages are preserved as unsubmitted drafts. Before publishing each r02: inspect remote state, call `begin_h3_attempt`, update local and remote staged `job.json` budget to the reserved amount, verify all asset and prompt hashes, atomically move stage to `jobs/inbox`, then run `uv run ai-interview submit-h3 <local package>`. Do not run multiple H3 workers concurrently because waiting for ComfyUI queue counts against each job's GPU-minute budget.

## Inputs already locked

- Studio wide and Guest close-up approved and copied to `02_refs/studio_wide.png` and `02_refs/studio_guest.png`, with SHA-256 provenance sidecars. Host identity/close-up and S002 start/end were approved earlier. The batch review sheet is `02_refs/studio_candidates/studio_guest_batch_review_r01.png`.
- Voice r02 completed remotely, 12 raw candidate WAV/JSON files imported with hashes. Selected B01 c02, B02 c01, B03 c03, B04 c02 by ASR, pause and timing data. 48 kHz Master: `01_audio/master/EP0001_golden_clip_audio_master.wav` SHA-256 `d7ecd43dee7ded2fa4ade6320a37922946d3a3afa90e5f59ca36777fccc20857`. S003 H3-only silence-padded reference is `01_audio/h3_reference/S003.wav` (Master unchanged). The model could not directly hear audio; do not claim acting quality was aurally verified.
- All final H3 prompts are byte-locked in `03_h3_prompts/S001.txt` through `S005.txt` and `PROMPT_LOCK.json`. Do not rewrite creative wording during packaging.
- Canonical episode state: `episode.yaml`. S001 `H3_RENDERING` r02; S002–S005 `READY_FOR_H3` r02. User explicitly authorized paid GPU and remote production, no image/video review requests, and final AutoDL shutdown via Ego.

## Next actions

1. Reconnect to remote and inspect S001 r02 without resubmission. If COMPLETE, import candidate files with matching result hashes; run deterministic QC first, then representative frames/selection. If budget-paused with valid completed candidates, diagnose and recover within manifest budget; do not pretend it completed. Candidate 01 already looks promising.
2. Submit S002–S005 one at a time from their staged r02 packages, QC and select; generate/edit/mix/subtitle final golden clip. Use the project CLI/AGENTS.md gates.
3. Run project tests and final QC, finish Beads issue only on acceptance, then use Ego `p1` to shut down the **same verified instance** and confirm `已关机`. Finish the Ego TaskSpace after shutdown.

## Changes and checks this session

- `orchestrator/ai_interview/package.py`: permits packaging `NEEDS_RETRY` shots; test passed.
- `orchestrator/ai_interview/cli.py`: `h3-status` records only the manifest's active job, while still displaying historical jobs. This avoids a r01/r02 mismatch error. Full suite still needs to be run.
- `episode.yaml`: S002–S005 r02, one candidate each; r01 unsubmitted packages preserved. S001 r02 remains three candidates.
- Remote S002–S005 r01 input and prompt hashes verified before cloning to r02 stage. The r02 staging assets have not yet received final publication verification or budget updates.
- SSH/SCP can stall or drop via the Clash fake-IP gateway; Ego Jupyter `page.fetch` with 256 KiB chunks worked for larger files. Do not change proxy settings without the user's approval.
