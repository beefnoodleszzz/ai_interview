#!/usr/bin/env bash
set -euo pipefail

readonly ROOT=/root/autodl-tmp/ai_interview
readonly APPS="$ROOT/apps"
readonly MODELS="$ROOT/models"

if [[ -r /etc/network_turbo ]]; then
  # AutoDL's documented helper is scoped to GitHub/Hugging Face downloads.
  source /etc/network_turbo >/dev/null
fi

install -d -m 0755 \
  "$APPS" "$MODELS/h3" "$MODELS/voice" "$MODELS/lipsync" "$MODELS/upscale" \
  "$ROOT/cache/huggingface" "$ROOT/cache/torch" "$ROOT/cache/xdg" \
  "$ROOT/staging/oss" \
  "$ROOT/characters" "$ROOT/sets" "$ROOT/episodes" "$ROOT/workflows" "$ROOT/scripts" \
  "$ROOT/jobs/inbox" "$ROOT/jobs/running" "$ROOT/jobs/work" \
  "$ROOT/jobs/complete" "$ROOT/jobs/failed" "$ROOT/logs" "$ROOT/reports"

if [[ -d /root/autodl-tmp/ComfyUI-H3-models && ! -e "$MODELS/h3/.migrated" ]]; then
  rmdir "$MODELS/h3"
  mv /root/autodl-tmp/ComfyUI-H3-models "$MODELS/h3"
  touch "$MODELS/h3/.migrated"
fi

clone_at() {
  local repo=$1 destination=$2 commit=$3
  if [[ ! -d "$destination/.git" ]]; then
    git clone --filter=blob:none "$repo" "$destination"
  fi
  git -C "$destination" fetch --depth 1 origin "$commit"
  git -C "$destination" checkout --detach "$commit"
}

clone_at https://github.com/Comfy-Org/ComfyUI.git "$APPS/ComfyUI" b33e2b55cae074eca5aec96283cceac19aa249ba
clone_at https://github.com/index-tts/index-tts.git "$APPS/index-tts" ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c
clone_at https://github.com/QwenLM/Qwen3-TTS.git "$APPS/Qwen3-TTS" 022e286b98fbec7e1e916cb940cdf532cd9f488e
clone_at https://github.com/bytedance/LatentSync.git "$APPS/LatentSync" a229c3948406bc2cf6eaf4873e662e70c6a04746
clone_at https://github.com/TMElyralab/MuseTalk.git "$APPS/MuseTalk" 0a89dec45a0192b824e3cf4daf96c239440c5ed8
clone_at https://github.com/comfyorg/comfyui_seedvr2.git "$APPS/ComfyUI/custom_nodes/comfyui_seedvr2" 6d50cf04803aa6d27300ccf2724d5471d366eb12

install -m 0755 /tmp/ai_interview_inventory.py "$ROOT/scripts/remote_inventory.py"

for relative in \
  diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
  diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors \
  text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  vae/minimax_h3_video_vae_fp16.safetensors \
  vae/minimax_h3_audio_vae_fp32.safetensors; do
  source="$MODELS/h3/$relative"
  [[ -s "$source" ]] || { echo "missing H3 model: $source" >&2; exit 1; }
  destination="$APPS/ComfyUI/models/$relative"
  install -d "$(dirname "$destination")"
  ln -sfn "$source" "$destination"
done

cat > "$ROOT/env.sh" <<'ENV'
export AI_STUDIO_ROOT=/root/autodl-tmp/ai_interview
export HF_HOME="$AI_STUDIO_ROOT/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TORCH_HOME="$AI_STUDIO_ROOT/cache/torch"
export XDG_CACHE_HOME="$AI_STUDIO_ROOT/cache/xdg"
export AI_INTERVIEW_RUNTIME="$AI_STUDIO_ROOT"
export AI_INTERVIEW_COMFYUI_ROOT="$AI_STUDIO_ROOT/apps/ComfyUI"
export AI_INTERVIEW_H3_MODEL_ROOT="$AI_STUDIO_ROOT/models/h3"
export AI_INTERVIEW_WORKFLOW_ROOT="$AI_STUDIO_ROOT/workflows"
export AI_INTERVIEW_COMFY_ENDPOINT=http://127.0.0.1:8188
export AI_INTERVIEW_H3_PYTHON=/root/miniconda3/bin/python
ENV

cat > "$ROOT/scripts/start_comfyui.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source /root/autodl-tmp/ai_interview/env.sh
cd "$AI_INTERVIEW_COMFYUI_ROOT"
exec /root/miniconda3/bin/python main.py --listen 127.0.0.1 --port 8188
SCRIPT
chmod 0755 "$ROOT/scripts/start_comfyui.sh"

if ! command -v tmux >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y tmux
  rm -rf /var/lib/apt/lists/*
fi

# Exact obsolete project remnants, verified during the read-only audit.
rm -f /root/GetVideoComponents /root/MiniMaxH3ReferenceToVideo.ref_videos.ref_video_0
rm -f /root/start_comfyui.sh
for path in /root/diffusion_models /root/text_encoders /root/vae; do
  if [[ -L "$path" || -d "$path" ]]; then rm -rf "$path"; fi
done
screen -wipe >/dev/null 2>&1 || true

/root/miniconda3/bin/python - <<'PY'
import json, os, shutil, subprocess
from pathlib import Path
root=Path('/root/autodl-tmp/ai_interview')
models={}
for p in sorted((root/'models'/'h3').rglob('*.safetensors')):
    models[str(p.relative_to(root))]={'bytes':p.stat().st_size}
report={
  'schema_version':'ai-interview-environment-v1',
  'root':str(root),
  'persistent_mount':os.path.ismount('/root/autodl-tmp'),
  'models':models,
  'apps':{},
  'gpu_expected':False,
}
for p in sorted((root/'apps').iterdir()):
    if (p/'.git').is_dir():
        result=subprocess.run(['git','-C',str(p),'rev-parse','HEAD'],capture_output=True,text=True)
        report['apps'][p.name]=result.stdout.strip()
(root/'reports'/'bootstrap.json').write_text(json.dumps(report,indent=2)+'\n')
PY

echo "AI Interview remote bootstrap complete: $ROOT"
