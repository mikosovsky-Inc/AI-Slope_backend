import textwrap
from pathlib import Path

from app.shared.tts import CharacterAlignment

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,\
 Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,\
 Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,\
-1,0,0,0,100,100,0,0,1,3,1,2,80,80,240,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def timestamp(seconds: float) -> str:
    ticks = max(0, round(seconds * 100))
    return f"{ticks // 360000}:{ticks // 6000 % 60:02}:{ticks // 100 % 60:02}.{ticks % 100:02}"


def write_captions(path: Path, scenes: list[tuple[str, float, dict | None]]) -> None:
    lines = [HEADER]
    offset = 0.0
    for narration, duration, raw_alignment in scenes:
        alignment = CharacterAlignment.model_validate(raw_alignment) if raw_alignment else None
        if alignment and (
            not alignment.characters or any(len(c) != 1 for c in alignment.characters)
        ):
            alignment = None
        text = "".join(alignment.characters) if alignment else narration
        chunks = textwrap.wrap(text, width=64, break_long_words=True, break_on_hyphens=False) or [
            ""
        ]
        cursor = 0
        for index, chunk in enumerate(chunks):
            if alignment and alignment.characters:
                position = text.find(chunk, cursor)
                if position < 0:
                    position = min(cursor, len(alignment.characters) - 1)
                last = min(position + len(chunk) - 1, len(alignment.characters) - 1)
                start = alignment.character_start_times_seconds[position]
                end = alignment.character_end_times_seconds[last]
                cursor = last + 1
            else:
                start, end = duration * index / len(chunks), duration * (index + 1) / len(chunks)
            start, end = min(start, duration), min(end, duration)
            if end <= start:
                continue
            safe = chunk.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            safe = "\\N".join(textwrap.wrap(safe, width=32))
            lines.append(
                f"Dialogue: 0,{timestamp(offset + start)},{timestamp(offset + end)},"
                f"Default,,0,0,0,,{safe}\n"
            )
        offset += duration
    path.write_text("".join(lines), encoding="utf-8")
