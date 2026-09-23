#!/root/miniconda3/bin/python
"""Synchronously upload one archive to Alibaba OSS and verify byte size.

Credentials are process environment inputs only.  This program never prints,
writes, or serializes them.
"""
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
    if len(sys.argv) != 2:
        raise SystemExit("usage: remote_oss_sync.py ARCHIVE_PATH")
    source = Path(sys.argv[1]).resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"invalid archive: {source}")

    endpoint = required("ALIYUN_OSS_ENDPOINT")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    bucket_name = os.environ.get("AI_INTERVIEW_OSS_BUCKET", "ai-interview-models")
    auth = oss2.Auth(
        required("ALIYUN_OSS_ACCESS_KEY_ID"),
        required("ALIYUN_OSS_ACCESS_KEY_SECRET"),
    )
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    checkpoint_root = Path("/dev/shm/ai-interview-oss-checkpoints")
    checkpoint_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    oss2.resumable_upload(
        bucket,
        source.name,
        str(source),
        store=oss2.ResumableStore(str(checkpoint_root)),
        num_threads=1,
    )
    remote_size = bucket.head_object(source.name).content_length
    local_size = source.stat().st_size
    if remote_size != local_size:
        raise SystemExit(f"OSS size mismatch for {source.name}: {remote_size} != {local_size}")
    print(f"OSS verified: {source.name} ({local_size} bytes)")


if __name__ == "__main__":
    main()
