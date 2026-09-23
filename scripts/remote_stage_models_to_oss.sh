#!/usr/bin/env bash
# Cloud-side model staging: Hugging Face -> temporary AutoDL disk -> OSS.
# Neither leg traverses the local Mac or its TUN proxy.
set -euo pipefail

export PATH=/root/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
source /root/autodl-tmp/ai_interview/env.sh
readonly ROOT="$AI_STUDIO_ROOT"
readonly STAGING="$ROOT/staging/model-source"
# AutoPanel selects the configured OSS Bucket as its current filesystem.  A
# bucket is itself the storage root, so no Bucket-internal prefix is needed.
# An optional prefix remains available for a future hierarchy.
readonly OSS_PREFIX="${AI_INTERVIEW_OSS_PREFIX:-/}"
readonly OSS_SYNC="$ROOT/scripts/remote_oss_sync.py"
: "${ALIYUN_OSS_ACCESS_KEY_ID:?Set ALIYUN_OSS_ACCESS_KEY_ID in the invoking environment.}"
: "${ALIYUN_OSS_ACCESS_KEY_SECRET:?Set ALIYUN_OSS_ACCESS_KEY_SECRET in the invoking environment.}"
: "${ALIYUN_OSS_ENDPOINT:?Set ALIYUN_OSS_ENDPOINT in the invoking environment.}"
trap 'unset ALIYUN_OSS_ACCESS_KEY_ID ALIYUN_OSS_ACCESS_KEY_SECRET ALIYUN_OSS_ENDPOINT' EXIT
install -d "$STAGING"

free_gib() {
  df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}'
}

require_staging_space() {
  local available
  available=$(free_gib)
  if (( available < 30 )); then
    echo "disk guard: ${available}GiB free; 30GiB required before staging another model" >&2
    exit 28
  fi
}

enable_model_network() {
  if [[ -r /etc/network_turbo ]]; then source /etc/network_turbo >/dev/null; fi
}

disable_model_network() {
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY REQUESTS_CA_BUNDLE SSL_CERT_FILE
}

upload_archive() {
  local source_dir=$1 archive=$2
  local archive_path="$STAGING/$archive"
  tar --create --file "$archive_path" --directory "$source_dir" --exclude='.cache' .
  [[ -s "$archive_path" ]] || { echo "empty archive: $archive" >&2; exit 1; }
  if (( $(stat -c '%s' "$archive_path") < 1048576 )); then
    echo "refusing implausibly small model archive: $archive" >&2
    exit 1
  fi
  # OSS is accessed directly from AutoDL, not through the Hugging Face turbo
  # proxy and never through the local Mac's TUN proxy.
  disable_model_network
  "$OSS_SYNC" "$archive_path"
  # source_dir is the live AutoDL runtime model directory. Retain it for
  # inference; only the reproducible transfer archive is disposable.
  rm -f "$archive_path"
}

stage_hf_snapshot() {
  local repo=$1 archive=$2 destination=$3
  require_staging_space
  # Preserve an interrupted snapshot so Hugging Face can resume it.
  mkdir -p "$destination"
  enable_model_network
  local file_count=0
  while IFS=$'\t' read -r expected_size relative_path; do
    [[ -n "$relative_path" ]] || continue
    download_hf_file "$repo" "$relative_path" "$destination/$relative_path" "$expected_size"
    ((file_count += 1))
  done < <(
    curl --fail --location --silent --show-error --retry 5 --retry-all-errors \
      "https://huggingface.co/api/models/$repo/tree/main?recursive=true&expand=false" |
      /root/miniconda3/bin/python -c '
import json, sys
for item in json.load(sys.stdin):
    if item.get("type") == "file":
        print("{}\t{}".format(item.get("size", 0), item["path"]))
'
  )
  (( file_count > 0 )) || { echo "repository file list is empty: $repo" >&2; exit 1; }
  upload_archive "$destination" "$archive"
}

stage_hf_files() {
  local repo=$1 archive=$2 destination=$3
  shift 3
  require_staging_space
  mkdir -p "$destination"
  enable_model_network
  local relative_path
  for relative_path in "$@"; do
    download_hf_file "$repo" "$relative_path" "$destination/$relative_path" 0
  done
  upload_archive "$destination" "$archive"
}

download_hf_file() {
  local repo=$1 relative_path=$2 destination=$3 expected_size=$4
  local current_size=0
  install -d "$(dirname "$destination")"
  if [[ -f "$destination" ]]; then
    current_size=$(stat -c '%s' "$destination")
  fi
  if (( expected_size > 0 && current_size == expected_size )); then
    return
  fi
  if (( expected_size > 0 && current_size > expected_size )); then
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
  if (( expected_size > 0 )) && [[ $(stat -c '%s' "$destination") != "$expected_size" ]]; then
    echo "incomplete download: $relative_path" >&2
    exit 1
  fi
}

# Sequential staging keeps the 120GB AutoDL disk within its safety budget.
# H3 is already present and verified on the instance, so it is intentionally
# excluded from OSS staging.
stage_hf_snapshot IndexTeam/IndexTTS-2.5 IndexTTS-2.5.tar "$ROOT/models/voice/IndexTTS-2.5"
stage_hf_snapshot Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign Qwen3-TTS-12Hz-1.7B-VoiceDesign.tar "$ROOT/models/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
stage_hf_snapshot Qwen/Qwen3-TTS-12Hz-1.7B-Base Qwen3-TTS-12Hz-1.7B-Base.tar "$ROOT/models/voice/Qwen3-TTS-12Hz-1.7B-Base"
stage_hf_files ByteDance/LatentSync-1.6 LatentSync-1.6.tar "$ROOT/models/lipsync/LatentSync-1.6" whisper/tiny.pt latentsync_unet.pt
stage_hf_files numz/SeedVR2_comfyUI SEEDVR2.tar "$ROOT/models/upscale/SEEDVR2" seedvr2_ema_3b_fp8_e4m3fn.safetensors ema_vae_fp16.safetensors

echo "All required non-H3 model archives were staged to OSS: $OSS_PREFIX"
