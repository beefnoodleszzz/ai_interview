# EP0001 verified lessons

Use this reference while aligning shots, rendering subtitles, or recovering a remote job. EP0001 details are evidence for the checks, not values to copy into future episodes.

## Mouth and audio alignment

The local 48 kHz selected WAV is the dialogue timing authority. H3 may include the same speech in its candidate MP4 after an initial delay. Local rough cut trims video by `edit_in_sec` and starts the selected WAV at the beginning of the shot. Therefore, for a speaking shot, the video's embedded speech offset relative to the WAV should approximately equal `edit_in_sec`.

EP0001 S003 diagnosis: cross-correlation of 4 kHz mono decodes found a 0.9495 s candidate-audio offset. The first edit used 0.47 s; 0.4795 s discrepancy matched the visible late mouth movement. Revision r02 used 0.95 s. The v2/v3 AAC stream hashes matched, and v3 Final QC passed. S004's measured offset was 1.7392 s against a 1.74 s trim; that ruled out a local gross shift for that shot.

For new shots, record the input WAV/candidate paths, measured offset, chosen trim, residual, and a few reviewed frames. Cross-correlation is a timing clue: confirm speech onset, end, and mouth action visually. H3 may alter the audio waveform or add ambience, so low correlation or multiple peaks needs manual review. If the candidate's own audio and lips disagree after timing alignment, classify it for candidate replacement or `NEEDS_LIPSYNC` rather than shifting the entire shot to hide the problem.

## Subtitle rendering

EP0001's first subtitled MP4 burned ImageMagick cards with unsupported Chinese glyphs, making white boxes. A same-basename SRT in `09_final/` was automatically shown by the player as a second black-backed layer. The clean rough cut had no burned text. The accepted revision used STHeiti Medium with a modest outline, aligned low and right, with no opaque background or matching SRT sidecar.

When delivering a review MP4, inspect actual rendered frames with short and long Chinese lines and play it in the intended viewer. `ffprobe` should show only video and audio streams unless a separate subtitle stream is deliberate. Keep the editable SRT as a source artifact outside the review video's basename. A media QC PASS cannot detect missing glyphs or automatic player overlays.

## Remote and edit recovery

- A remote `FAILED` job can contain a usable, already generated candidate. Preserve its failure status; verify candidate file hash, full decode, metadata, and human review before selection. EP0001 S001 was recovered this way without another budget-burning submission.
- Polling remote status after director selection must not regress the selected shot's local state. Inspect the Manifest after status sync.
- EP0001 S003 timing correction archived `07_edit/edit_locks/edit_lock_r01.json` and made lock r02. A lock binds both asset hashes and shot timing; merely changing `edit_in_sec` under the old lock is invalid.
- The remote instance may change between sessions. Never reuse an old SSH target or print `.env.local` credentials. Keep ComfyUI on remote loopback. Confirm completed imports and job state before shutdown or cleanup.
