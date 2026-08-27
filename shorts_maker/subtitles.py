from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings
from .transcription import Word


@dataclass
class Caption:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_color(html: str, alpha: str = "00") -> str:
    value = html.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "FFFFFF"
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{b}{g}{r}&".upper()


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _shift(seconds: float, insert_at: float, banner_duration: float) -> float:
    return seconds + banner_duration if seconds >= insert_at else seconds


def shifted_words(words: list[Word], insert_at: float, banner_duration: float) -> list[Word]:
    shifted: list[Word] = []
    for word in words:
        # A word that straddles the cut stays before the banner; the next word resumes after it.
        start = _shift(word.start, insert_at, banner_duration)
        end = min(word.end, insert_at) if word.start < insert_at < word.end else _shift(word.end, insert_at, banner_duration)
        shifted.append(Word(start, max(start + 0.03, end), word.text))
    return shifted


def group_words(words: list[Word], max_words: int, max_chars: int) -> list[Caption]:
    groups: list[Caption] = []
    current: list[Word] = []
    chars = 0
    for word in words:
        gap = word.start - current[-1].end if current else 0.0
        next_chars = chars + (1 if current else 0) + len(word.text)
        boundary = bool(current) and (
            len(current) >= max(1, max_words)
            or next_chars > max(8, max_chars)
            or gap > 0.65
            or current[-1].text.endswith((".", "!", "?", ":", ";"))
        )
        if boundary:
            groups.append(Caption(current))
            current, chars = [], 0
        current.append(word)
        chars += (1 if len(current) > 1 else 0) + len(word.text)
    if current:
        groups.append(Caption(current))
    return groups


def _active_line(words: list[Word], active: int, normal: str, highlight: str) -> str:
    pieces: list[str] = []
    for index, word in enumerate(words):
        color = highlight if index == active else normal
        pieces.append(r"{\c" + color + "}" + _escape(word.text))
    return " ".join(pieces)


def _wrap_title(title: str, width: int, font_size: int) -> str:
    # Approximate average glyph width; ASS then performs final centering.
    chars = max(12, int(width / max(font_size * 0.58, 1)))
    lines = textwrap.wrap(title.replace("_", " ").strip(), width=chars, max_lines=3, placeholder="…")
    return r"\N".join(_escape(line) for line in lines) or "VIDEO"


def write_ass(
    path: Path,
    title: str,
    words: list[Word],
    settings: Settings,
    width: int,
    height: int,
    main_duration: float,
    insert_at: float,
    banner_duration: float,
) -> None:
    title_align = 8 if settings.title_position == "top" else 5
    sub_align = 2 if settings.subtitle_position == "bottom" else 5
    title_margin = max(0, settings.title_margin)
    sub_margin = max(0, settings.subtitle_margin)
    title_color = _ass_color(settings.title_color)
    sub_color = _ass_color(settings.subtitle_color)
    highlight = _ass_color(settings.subtitle_highlight)
    if settings.banner_mode == "overlay":
        # The main clip keeps running under the advert, so speech timings stay
        # unchanged and captions remain visible below the banner.
        total_duration = main_duration
        prepared_words = words
    else:
        total_duration = main_duration + banner_duration
        prepared_words = shifted_words(words, insert_at, banner_duration)
    captions = group_words(prepared_words, settings.subtitle_max_words, settings.subtitle_max_chars)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,{settings.title_font_size},{title_color},{title_color},&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,4,2,{title_align},60,60,{title_margin},1
Style: Subs,Arial,{settings.subtitle_font_size},{sub_color},{highlight},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,{sub_align},60,60,{sub_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 1,{_ass_time(0)},{_ass_time(total_duration)},Title,,0,0,0,,{{\\q2}}{_wrap_title(title, width, settings.title_font_size)}"
    ]
    for caption in captions:
        for index, word in enumerate(caption.words):
            start = word.start
            end = caption.words[index + 1].start if index + 1 < len(caption.words) else max(word.end, caption.end + 0.10)
            if end <= start:
                end = start + 0.08
            line = _active_line(caption.words, index, sub_color, highlight)
            events.append(
                f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},Subs,,0,0,0,,{{\\q2}}{line}"
            )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
