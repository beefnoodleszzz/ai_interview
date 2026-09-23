#!/usr/bin/env bash
# Run one command with short-lived OSS credentials stored in a RAM-only file.
# The file path and command are arguments; credential values never appear in
# process arguments or logs.
set -euo pipefail

if (( $# < 2 )); then
  echo "usage: remote_run_with_oss_env.sh ENV_FILE COMMAND [ARG ...]" >&2
  exit 2
fi

readonly ENV_FILE=$1
shift
[[ -f "$ENV_FILE" ]] || { echo "missing OSS environment file" >&2; exit 2; }

export ALIYUN_OSS_ACCESS_KEY_ID
export ALIYUN_OSS_ACCESS_KEY_SECRET
export ALIYUN_OSS_ENDPOINT
ALIYUN_OSS_ACCESS_KEY_ID=$(sed -n '1p' "$ENV_FILE")
ALIYUN_OSS_ACCESS_KEY_SECRET=$(sed -n '2p' "$ENV_FILE")
ALIYUN_OSS_ENDPOINT=$(sed -n '3p' "$ENV_FILE")
rm -f "$ENV_FILE"

exec "$@"
