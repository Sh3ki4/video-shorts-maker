from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


APP_NAME = "VideoShortsMaker"


@dataclass
class Settings:
    settings_version: int = 3
    ffmpeg_path: str = ""
    output_dir: str = ""
    part_length: float = 60.0
    min_last_part: float = 5.0
    insert_mode: str = "percent"  # percent | fixed
    insert_value: float = 50.0
    smart_insert: bool = False
    smart_range_start: float = 15.0
    smart_range_end: float = 45.0
    title_position: str = "top"  # top | center
    title_font_size: int = 58
    title_margin: int = 110
    title_color: str = "#FFFFFF"
    subtitle_position: str = "bottom"  # bottom | center
    subtitle_font_size: int = 54
    subtitle_margin: int = 280
    subtitle_color: str = "#FFFFFF"
    subtitle_highlight: str = "#FFD400"
    subtitle_max_words: int = 5
    subtitle_max_chars: int = 34
    banner_volume: float = 1.0
    banner_mode: str = "overlay"  # overlay | pause
    banner_width_percent: float = 88.0
    banner_position_percent: float = 58.0
    main_volume_during_banner: float = 0.35
    aspect_mode: str = "vertical_blur"  # vertical_blur | vertical_pad | vertical_crop | original
    resolution: str = "1080x1920"
    fps: str = "30"
    quality: str = "high"  # high | balanced | compact
    bitrate_mbps: float = 0.0
    use_nvenc: bool = True
    recognition: str = "local"  # local | groq | none
    whisper_model: str = "small"
    whisper_device: str = "auto"
    groq_api_key: str = ""
    groq_model: str = "whisper-large-v3-turbo"
    groq_analysis_model: str = "openai/gpt-oss-20b"
    language: str = "ru"
    keep_temp: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        data = dict(data)
        # Version 1 used destructive center-cropping as the default. Existing
        # users should receive the new full-frame layout automatically once.
        version = int(data.get("settings_version", 1))
        if version < 2:
            if data.get("aspect_mode", "vertical_crop") == "vertical_crop":
                data["aspect_mode"] = "vertical_blur"
        if version < 3:
            # Version 3 changes the preferred advert behavior from pausing the
            # clip to a picture-in-picture overlay while the clip keeps moving.
            data["banner_mode"] = "overlay"
            data["settings_version"] = 3
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Portable/restricted Windows sessions may block AppData writes.
        base = Path(os.environ.get("TEMP", ".")) / APP_NAME
        base.mkdir(parents=True, exist_ok=True)
    return base / "settings.json"


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    config_path().write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
    )
