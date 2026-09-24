#!/usr/bin/env bash
# Deploy mandatory non-H3 weights directly to this AutoDL instance's runtime
# model tree.  A source -> OSS -> same-instance relay adds a full extra transfer.
set -euo pipefail

export PATH=/root/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
source /root/autodl-tmp/ai_interview/env.sh
readonly ROOT="$AI_STUDIO_ROOT"
readonly MODELS="$ROOT/models"
readonly APPS="$ROOT/apps"
readonly MANIFEST_DIR=/dev/shm/ai-interview-hf-manifests
install -d "$MANIFEST_DIR" "$MODELS/voice" "$MODELS/lipsync" "$MODELS/upscale"
trap 'rm -rf "$MANIFEST_DIR"' EXIT

free_gib() {
  df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}'
}

require_free_gib() {
  local required=$1 available
  available=$(free_gib)
  if (( available < required )); then
    echo "disk guard: ${available}GiB free; ${required}GiB required before model deployment" >&2
    exit 28
  fi
}

enable_model_network() {
  if [[ -r /etc/network_turbo ]]; then source /etc/network_turbo >/dev/null; fi
}

disable_model_network() {
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY REQUESTS_CA_BUNDLE SSL_CERT_FILE
}

manifest_for() {
  local repo=$1 output="$MANIFEST_DIR/${repo//\//_}.tsv"
  curl --fail --location --silent --show-error --retry 5 --retry-all-errors \
    "https://huggingface.co/api/models/$repo/tree/main?recursive=true&expand=false" |
    /root/miniconda3/bin/python -c '
import json, sys
for item in json.load(sys.stdin):
    if item.get("type") == "file":
        size = int(item.get("size", 0))
        if size <= 0:
            # Some repositories expose a zero-sized metadata/config entry in
            # the tree API even though the selected runtime weights are valid.
            # Omit those entries; selected runtime files are still required
            # to have a positive manifest size in deploy_files().
            continue
        print(str(size) + "\t" + item["path"])
' > "$output"
  [[ -s "$output" ]] || { echo "empty Hugging Face manifest: $repo" >&2; exit 1; }
  printf '%s\n' "$output"
}

download_hf_file() {
  local repo=$1 relative_path=$2 destination=$3 expected_size=$4 current_size=0
  install -d "$(dirname "$destination")"
  [[ -f "$destination" ]] && current_size=$(stat -c '%s' "$destination")
  if (( current_size == expected_size )); then return; fi
  if (( current_size > expected_size )); then
    echo "invalid existing file size: $destination" >&2
    exit 1
  fi
  local url="https://huggingface.co/$repo/resolve/main/$relative_path?download=true"
  if (( current_size > 0 )); then
    curl --fail --location --continue-at - --silent --show-error --retry 10 --retry-all-errors \
      --connect-timeout 30 --speed-limit 1024 --speed-time 300 --output "$destination" "$url"
  else
    curl --fail --location --silent --show-error --retry 10 --retry-all-errors \
      --connect-timeout 30 --speed-limit 1024 --speed-time 300 --output "$destination" "$url"
  fi
  [[ $(stat -c '%s' "$destination") == "$expected_size" ]] || {
    echo "incomplete model download: $relative_path" >&2; exit 1;
  }
}

link_index_tts_runtime() {
  local target=$1 checkpoint_dir="$APPS/index-tts/checkpoints" source name destination
  install -d "$checkpoint_dir"
  while IFS= read -r -d '' source; do
    name=$(basename "$source")
    destination="$checkpoint_dir/$name"
    if [[ -L "$destination" ]]; then
      [[ $(readlink "$destination") == "$source" ]] || {
        echo "checkpoint link collision: $destination" >&2; exit 1;
      }
      continue
    fi
    [[ ! -e "$destination" ]] || {
      echo "checkpoint file collision: $destination" >&2; exit 1;
    }
    ln -s "$source" "$destination"
  done < <(find "$target" -mindepth 1 -maxdepth 1 -print0)
}

deploy_snapshot() {
  local repo=$1 destination=$2 manifest
  require_free_gib 15
  manifest=$(manifest_for "$repo")
  while IFS=$'\t' read -r expected_size relative_path; do
    [[ -n "$relative_path" ]] || continue
    download_hf_file "$repo" "$relative_path" "$destination/$relative_path" "$expected_size"
  done < "$manifest"
  echo "Runtime model deployed: $destination"
}

deploy_files() {
  local repo=$1 destination=$2 manifest expected_size relative_path record
  shift 2
  require_free_gib 15
  manifest=$(manifest_for "$repo")
  for relative_path in "$@"; do
    record=$(awk -F '\t' -v path="$relative_path" '$2 == path {print; exit}' "$manifest")
    [[ -n "$record" ]] || { echo "required model file missing from manifest: $repo/$relative_path" >&2; exit 1; }
    expected_size=${record%%$'\t'*}
    download_hf_file "$repo" "$relative_path" "$destination/$relative_path" "$expected_size"
  done
  echo "Runtime model deployed: $destination"
}

verify_sha256() {
  local path=$1 expected=$2 actual
  actual=$(sha256sum "$path" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || {
    echo "model SHA-256 mismatch: $path" >&2
    exit 1
  }
}

enable_model_network
deploy_snapshot IndexTeam/IndexTTS-2.5 "$MODELS/voice/IndexTTS-2.5"
link_index_tts_runtime "$MODELS/voice/IndexTTS-2.5"
deploy_snapshot Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
deploy_snapshot Qwen/Qwen3-TTS-12Hz-1.7B-Base "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-Base"
deploy_files ByteDance/LatentSync-1.6 "$MODELS/lipsync/LatentSync-1.6" whisper/tiny.pt latentsync_unet.pt
latentsync_source="$MODELS/lipsync/LatentSync-1.6"
latentsync_checkpoints="$APPS/LatentSync/checkpoints"
if [[ -L "$latentsync_checkpoints" ]]; then
  [[ $(readlink -f "$latentsync_checkpoints") == $(readlink -f "$latentsync_source") ]] || {
    echo "LatentSync checkpoint link collision: $latentsync_checkpoints" >&2; exit 1;
  }
elif [[ -e "$latentsync_checkpoints" ]]; then
  echo "LatentSync checkpoint path already exists; refusing to replace it: $latentsync_checkpoints" >&2
  exit 1
else
  ln -s "$latentsync_source" "$latentsync_checkpoints"
fi
# The previous no-card transfer left only these two exact runtime files in a
# staging directory.  Remove that duplicate only after the direct runtime
# deployment has verified both source sizes.
if [[ -f "$ROOT/staging/model-source/LatentSync-1.6/latentsync_unet.pt" && \
      -f "$ROOT/staging/model-source/LatentSync-1.6/whisper/tiny.pt" ]]; then
  [[ $(stat -c '%s' "$ROOT/staging/model-source/LatentSync-1.6/latentsync_unet.pt") == \
     $(stat -c '%s' "$MODELS/lipsync/LatentSync-1.6/latentsync_unet.pt") ]] || {
    echo "staging/runtime size mismatch: LatentSync UNet" >&2; exit 1;
  }
  [[ $(stat -c '%s' "$ROOT/staging/model-source/LatentSync-1.6/whisper/tiny.pt") == \
     $(stat -c '%s' "$MODELS/lipsync/LatentSync-1.6/whisper/tiny.pt") ]] || {
    echo "staging/runtime size mismatch: LatentSync Whisper" >&2; exit 1;
  }
  rm -rf "$ROOT/staging/model-source/LatentSync-1.6"
  rmdir "$ROOT/staging/model-source" 2>/dev/null || true
fi
deploy_files numz/SeedVR2_comfyUI "$MODELS/upscale/SEEDVR2" seedvr2_ema_3b_fp8_e4m3fn.safetensors ema_vae_fp16.safetensors
verify_sha256 "$MODELS/upscale/SEEDVR2/seedvr2_ema_3b_fp8_e4m3fn.safetensors" \
  3bf1e43ebedd570e7e7a0b1b60d6a02e105978f505c8128a241cde99a8240cff
verify_sha256 "$MODELS/upscale/SEEDVR2/ema_vae_fp16.safetensors" \
  20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1
seedvr2_source="$MODELS/upscale/SEEDVR2"
seedvr2_comfy="$APPS/ComfyUI/models/SEEDVR2"
install -d "$(dirname "$seedvr2_comfy")"
if [[ -L "$seedvr2_comfy" ]]; then
  [[ $(readlink -f "$seedvr2_comfy") == $(readlink -f "$seedvr2_source") ]] || {
    echo "SeedVR2 model link collision: $seedvr2_comfy" >&2; exit 1;
  }
elif [[ -e "$seedvr2_comfy" ]]; then
  echo "SeedVR2 ComfyUI model path already exists; refusing to replace it: $seedvr2_comfy" >&2
  exit 1
else
  ln -s "$seedvr2_source" "$seedvr2_comfy"
fi
disable_model_network

"$ROOT/scripts/remote_inventory.py" >/dev/null
echo "Mandatory non-H3 runtime models deployed directly to AutoDL"
