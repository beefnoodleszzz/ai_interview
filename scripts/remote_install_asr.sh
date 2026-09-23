#!/usr/bin/env bash
set -euo pipefail

ROOT="${AI_STUDIO_ROOT:-/root/autodl-tmp/ai_interview}"
ASR_ENV="$ROOT/envs/asr"
ASR_MODEL="$ROOT/models/asr/whisper-tiny.pt"
EXISTING_MODEL="$ROOT/models/lipsync/LatentSync-1.6/whisper/tiny.pt"

free_gib=$(df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if (( free_gib < 8 )); then
  echo "ASR install refused: ${free_gib}GiB free; at least 8GiB is required." >&2
  exit 28
fi

if [[ ! -x "$ASR_ENV/bin/python" ]]; then
  /root/miniconda3/bin/python -m venv --system-site-packages "$ASR_ENV"
fi

mkdir -p "$ROOT/models/asr"
if [[ ! -e "$ASR_MODEL" ]]; then
  if [[ ! -s "$EXISTING_MODEL" ]]; then
    echo "No local tiny Whisper checkpoint found; refusing to download an unbudgeted model." >&2
    exit 2
  fi
  ln -s "$EXISTING_MODEL" "$ASR_MODEL"
fi

"$ASR_ENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir 'openai-whisper==20250625'
"$ASR_ENV/bin/python" - <<'PY'
import sys
import torch
import whisper

assert torch.cuda.is_available(), "ASR environment cannot access the CUDA runtime"
print(f"ASR ready: Python {sys.version.split()[0]}, Whisper {whisper.__version__}, CUDA {torch.version.cuda}")
PY
"$ASR_ENV/bin/python" -c 'from pathlib import Path; import whisper; p=Path("/root/autodl-tmp/ai_interview/models/asr/whisper-tiny.pt"); assert p.is_file() and p.stat().st_size > 0; print("Whisper tiny checkpoint available")'
