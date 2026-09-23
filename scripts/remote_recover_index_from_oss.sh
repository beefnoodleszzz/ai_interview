#!/usr/bin/env bash
# Restore the already verified IndexTTS archive into the AutoDL runtime tree.
set -uo pipefail

if (( $# != 1 )); then
  echo "usage: remote_recover_index_from_oss.sh ENV_FILE" >&2
  exit 2
fi

readonly ROOT=/root/autodl-tmp/ai_interview
readonly REPORT="$ROOT/reports/index-oss-import.exit"
readonly LOG="$ROOT/logs/index-oss-import.log"
started=$(date +%s)
"$ROOT/scripts/remote_run_with_oss_env.sh" "$1" \
  bash "$ROOT/scripts/remote_oss_import.sh" IndexTTS-2.5.tar >> "$LOG" 2>&1
code=$?
ended=$(date +%s)
printf '%s\n' "$code" > "$REPORT"
printf 'index_oss_import_seconds=%s\n' "$((ended - started))" >> "$LOG"
exit "$code"
