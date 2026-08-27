from __future__ import annotations

import re
from pathlib import Path


WINDOWS_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(value: str, fallback: str = "video") -> str:
    value = WINDOWS_FORBIDDEN.sub("_", value).strip().rstrip(".")
    return value[:180] or fallback


def unique_output_path(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while True:
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_fraction(value: str | int | float | None, default: float = 30.0) -> float:
    if value is None:
        return default
    text = str(value)
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            denominator = float(b)
            return float(a) / denominator if denominator else default
        result = float(text)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
