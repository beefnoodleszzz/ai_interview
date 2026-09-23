#!/usr/bin/env bash
# Import verified model archives from OSS into the AutoDL runtime model tree.
# Archives are removed after extraction; deployed model files remain local for
# inference. Credentials are process-local only and are never logged.
set -euo pipefail

export PATH=/root/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
source /root/autodl-tmp/ai_interview/env.sh
readonly ROOT="$AI_STUDIO_ROOT"
readonly MODELS="$ROOT/models"
readonly APPS="$ROOT/apps"
readonly STAGING="$ROOT/staging/oss"
readonly OSS_FETCH="$ROOT/scripts/remote_oss_fetch.py"

: "${ALIYUN_OSS_ACCESS_KEY_ID:?Set ALIYUN_OSS_ACCESS_KEY_ID in the invoking environment.}"
: "${ALIYUN_OSS_ACCESS_KEY_SECRET:?Set ALIYUN_OSS_ACCESS_KEY_SECRET in the invoking environment.}"
: "${ALIYUN_OSS_ENDPOINT:?Set ALIYUN_OSS_ENDPOINT in the invoking environment.}"
trap 'unset ALIYUN_OSS_ACCESS_KEY_ID ALIYUN_OSS_ACCESS_KEY_SECRET ALIYUN_OSS_ENDPOINT' EXIT
install -d "$STAGING" "$MODELS/voice" "$MODELS/lipsync" "$MODELS/upscale"

fetch_archive() {
  local archive=$1 destination=$2
  local local_archive="$STAGING/$archive"
  if [[ -d "$destination" ]] && find "$destination" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "Runtime model already present: $destination"
    return
  fi
  "$OSS_FETCH" "$archive" "$local_archive"
  mkdir -p "$destination"
  tar --extract --file "$local_archive" --directory "$destination" --no-same-owner
  rm -f "$local_archive"
  echo "Runtime model imported: $destination"
}

link_index_tts_runtime() {
  local target="$MODELS/voice/IndexTTS-2.5" checkpoint_dir="$APPS/index-tts/checkpoints" source name destination
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

# Each archive must contain the repository snapshot's files directly at its
# root, not an extra enclosing directory.  H3 is deliberately excluded: it is
# already verified under models/h3 and must not be downloaded again.
case "${1:-all}" in
  all)
    fetch_archive IndexTTS-2.5.tar "$MODELS/voice/IndexTTS-2.5"
    fetch_archive Qwen3-TTS-12Hz-1.7B-VoiceDesign.tar "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    fetch_archive Qwen3-TTS-12Hz-1.7B-Base.tar "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-Base"
    fetch_archive LatentSync-1.6.tar "$MODELS/lipsync/LatentSync-1.6"
    fetch_archive SEEDVR2.tar "$MODELS/upscale/SEEDVR2"
    ;;
  IndexTTS-2.5.tar) fetch_archive IndexTTS-2.5.tar "$MODELS/voice/IndexTTS-2.5" ;;
  Qwen3-TTS-12Hz-1.7B-VoiceDesign.tar) fetch_archive Qwen3-TTS-12Hz-1.7B-VoiceDesign.tar "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign" ;;
  Qwen3-TTS-12Hz-1.7B-Base.tar) fetch_archive Qwen3-TTS-12Hz-1.7B-Base.tar "$MODELS/voice/Qwen3-TTS-12Hz-1.7B-Base" ;;
  LatentSync-1.6.tar) fetch_archive LatentSync-1.6.tar "$MODELS/lipsync/LatentSync-1.6" ;;
  SEEDVR2.tar) fetch_archive SEEDVR2.tar "$MODELS/upscale/SEEDVR2" ;;
  *) echo "unknown archive selector: $1" >&2; exit 2 ;;
esac

link_index_tts_runtime
echo "OSS model archives imported into $MODELS"
