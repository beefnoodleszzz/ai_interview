"""Whisper subprocess adapter; run with the isolated ASR interpreter."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()
    import whisper

    model = whisper.load_model(args.model, device="cpu")
    result = model.transcribe(args.audio, language=args.language, task="transcribe", fp16=False)
    print(json.dumps({"text": str(result.get("text", "")).strip(), "language": result.get("language")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
