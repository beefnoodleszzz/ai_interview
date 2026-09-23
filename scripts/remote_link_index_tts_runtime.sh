#!/usr/bin/env bash
# Merge runtime IndexTTS weights into the repository-owned checkpoints folder
# without overwriting support files such as pinyin.vocab.
set -euo pipefail

source /root/autodl-tmp/ai_interview/env.sh
readonly ROOT="$AI_STUDIO_ROOT"
readonly TARGET="$ROOT/models/voice/IndexTTS-2.5"
readonly CHECKPOINT_DIR="$ROOT/apps/index-tts/checkpoints"

[[ -d "$TARGET" ]] || { echo "missing IndexTTS runtime model: $TARGET" >&2; exit 1; }
install -d "$CHECKPOINT_DIR"
while IFS= read -r -d '' source; do
  name=$(basename "$source")
  destination="$CHECKPOINT_DIR/$name"
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
done < <(find "$TARGET" -mindepth 1 -maxdepth 1 -print0)

test -s "$CHECKPOINT_DIR/config.yaml"
test -s "$CHECKPOINT_DIR/gpt.pth"
echo "IndexTTS runtime links verified"
