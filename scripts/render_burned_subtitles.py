"""Burn a compact Chinese subtitle layer into an MP4 without touching source files.

Uses macOS system Python's Pillow and ffmpeg. The SRT remains a timing source;
the output video intentionally has no same-basename sidecar subtitle.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT = Path("/System/Library/Fonts/STHeiti Medium.ttc")
FONT_SIZE = 51
FRAME = (1080, 1920)
RIGHT_MARGIN = 62
BOTTOM_MARGIN = 195
MAX_TEXT_WIDTH = 790
LINE_HEIGHT = 69


def seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def cues(path: Path) -> list[tuple[float, float, str]]:
    result = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        timing = next((line for line in lines if " --> " in line), None)
        if timing is None:
            raise ValueError(f"SRT cue has no timing: {block!r}")
        start, end = timing.split(" --> ")
        content = "".join(lines[lines.index(timing) + 1 :]).strip()
        if not content or seconds(end) <= seconds(start):
            raise ValueError(f"SRT cue has invalid content or timing: {block!r}")
        result.append((seconds(start), seconds(end), content))
    return result


def wrap_text(text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        proposal = current + char
        if current and font.getlength(proposal) > MAX_TEXT_WIDTH:
            lines.append(current)
            current = char
        else:
            current = proposal
    if current:
        lines.append(current)
    if len(lines) > 2:
        raise ValueError(f"subtitle needs more than two lines: {text}")
    return lines


def render_card(text: str, path: Path) -> None:
    if not FONT.is_file():
        raise FileNotFoundError(FONT)
    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    lines = wrap_text(text, font)
    image = Image.new("RGBA", FRAME, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bottom = FRAME[1] - BOTTOM_MARGIN
    top = bottom - len(lines) * LINE_HEIGHT
    for index, line in enumerate(lines):
        x = FRAME[0] - RIGHT_MARGIN - font.getlength(line)
        y = top + index * LINE_HEIGHT
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 155), stroke_width=5, stroke_fill=(0, 0, 0, 155))
        draw.text((x, y), line, font=font, fill=(249, 247, 242, 255), stroke_width=2, stroke_fill=(20, 16, 14, 230))
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("srt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if not args.video.is_file() or not args.srt.is_file():
        raise FileNotFoundError("source video and SRT must exist")
    if args.output.exists():
        raise FileExistsError(args.output)
    entries = cues(args.srt)
    with tempfile.TemporaryDirectory(prefix="ai-interview-subtitles-") as temporary:
        card_paths = []
        for index, (_, _, content) in enumerate(entries, 1):
            card = Path(temporary) / f"cue_{index:02d}.png"
            render_card(content, card)
            card_paths.append(card)
        filters = []
        prior = "[0:v]"
        for index, (start, end, _) in enumerate(entries, 1):
            output = f"[v{index}]"
            filters.append(
                f"{prior}[{index}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})':format=auto{output}"
            )
            prior = output
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(args.video)]
        for card in card_paths:
            command.extend(["-loop", "1", "-i", str(card)])
        command.extend([
            "-filter_complex", ";".join(filters), "-map", prior, "-map", "0:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(args.output),
        ])
        subprocess.run(command, check=True)
    print(args.output)


if __name__ == "__main__":
    main()
