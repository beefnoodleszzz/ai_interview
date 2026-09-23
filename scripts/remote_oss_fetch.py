#!/root/miniconda3/bin/python
"""Download one verified OSS object directly onto the AutoDL instance."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import oss2


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: remote_oss_fetch.py OBJECT_NAME DESTINATION")
    object_name = sys.argv[1]
    destination = Path(sys.argv[2]).resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    endpoint = required("ALIYUN_OSS_ENDPOINT")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    auth = oss2.Auth(
        required("ALIYUN_OSS_ACCESS_KEY_ID"),
        required("ALIYUN_OSS_ACCESS_KEY_SECRET"),
    )
    bucket = oss2.Bucket(auth, endpoint, os.environ.get("AI_INTERVIEW_OSS_BUCKET", "ai-interview-models"))
    expected_size = bucket.head_object(object_name).content_length
    checkpoints = Path("/dev/shm/ai-interview-oss-checkpoints")
    checkpoints.mkdir(mode=0o700, parents=True, exist_ok=True)
    oss2.resumable_download(
        bucket,
        object_name,
        str(destination),
        store=oss2.ResumableStore(str(checkpoints)),
        num_threads=1,
    )
    actual_size = destination.stat().st_size if destination.is_file() else -1
    if actual_size != expected_size:
        raise SystemExit(f"OSS size mismatch for {object_name}: {actual_size} != {expected_size}")
    print(f"OSS downloaded and verified: {object_name} ({actual_size} bytes)")


if __name__ == "__main__":
    main()
