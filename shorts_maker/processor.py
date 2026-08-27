from __future__ import annotations

import math
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import (
    FFmpeg,
    FFmpegError,
    find_ffmpeg,
    output_fps,
    output_geometry,
    overlay_banner_geometry,
)
from .moment_analysis import choose_interesting_moment
from .settings import Settings
from .subtitles import write_ass
from .transcription import TranscriptionError, transcribe
from .utils import safe_filename, unique_output_path


class ProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Part:
    number: int
    start: float
    duration: float


def plan_parts(duration: float, part_length: float, min_last_part: float) -> list[Part]:
    duration = max(0.0, duration)
    length = max(5.0, part_length)
    if duration <= 0.01:
        return []
    parts: list[Part] = []
    start = 0.0
    number = 1
    while start < duration - 0.01:
        remaining = duration - start
        if parts and remaining < max(0.0, min_last_part):
            previous = parts[-1]
            parts[-1] = Part(previous.number, previous.start, previous.duration + remaining)
            break
        part_duration = min(length, remaining)
        parts.append(Part(number, start, part_duration))
        start += part_duration
        number += 1
    return parts


def insertion_time(settings: Settings, duration: float) -> float:
    if settings.insert_mode == "fixed":
        point = settings.insert_value
    else:
        point = duration * settings.insert_value / 100.0
    return min(max(point, 0.05), max(0.05, duration - 0.05))


class BatchProcessor:
    def __init__(
        self,
        settings: Settings,
        app_dir: Path,
        log: Callable[[str], None],
        progress: Callable[[int, int, str], None],
        cancel_event: threading.Event,
    ) -> None:
        self.settings = settings
        self.app_dir = app_dir
        self.log = log
        self.progress = progress
        self.cancel_event = cancel_event

    def run(self, sources: list[Path], banner: Path, output_dir: Path) -> list[Path]:
        if not sources:
            raise ProcessingError("Добавьте хотя бы один исходный видеофайл.")
        if not banner.is_file():
            raise ProcessingError("Выберите существующий рекламный MP4-файл.")
        resolved = find_ffmpeg(self.settings.ffmpeg_path, self.app_dir)
        if not resolved:
            raise ProcessingError(
                "FFmpeg не найден. Нажмите «Установить FFmpeg» или укажите папку с ffmpeg.exe и ffprobe.exe."
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        ff = FFmpeg(*resolved, log=self.log, cancel_check=self.cancel_event.is_set)

        valid_sources: list[tuple[Path, object, list[Part]]] = []
        total = 0
        for source in sources:
            if not source.is_file():
                self.log(f"Пропуск: файл не найден — {source}")
                continue
            info = ff.probe(source)
            parts = plan_parts(info.duration, self.settings.part_length, self.settings.min_last_part)
            if not parts:
                self.log(f"Пропуск: пустое или повреждённое видео — {source.name}")
                continue
            valid_sources.append((source, info, parts))
            total += len(parts)
        if not valid_sources:
            raise ProcessingError("Не найдено ни одного пригодного исходного видео.")

        banner_info = ff.probe(banner)
        if banner_info.duration <= 0:
            raise ProcessingError("Не удалось определить длительность рекламного баннера.")
        self.log(f"FFmpeg: {resolved[0]}")
        self.log(f"Баннер: {banner.name}, {banner_info.duration:.2f} сек.")

        created: list[Path] = []
        done = 0
        temp_root = Path(tempfile.mkdtemp(prefix="VideoShortsMaker_"))
        self.log(f"Временная папка: {temp_root}")
        try:
            banner_cache: dict[tuple[int, int, int, str], Path] = {}
            for source, source_info, parts in valid_sources:
                title = source.stem
                width, height = output_geometry(source_info, self.settings.resolution, self.settings.aspect_mode)
                fps = output_fps(source_info, self.settings.fps)
                if self.settings.banner_mode == "overlay":
                    banner_width, banner_height = overlay_banner_geometry(
                        width, height, banner_info, self.settings.banner_width_percent
                    )
                    banner_layout = "vertical_pad"
                else:
                    banner_width, banner_height = width, height
                    banner_layout = self.settings.aspect_mode
                cache_key = (banner_width, banner_height, round(fps * 1000), banner_layout)
                normalized_banner = banner_cache.get(cache_key)
                if normalized_banner is None:
                    normalized_banner = temp_root / f"banner_{banner_width}x{banner_height}_{cache_key[2]}.mp4"
                    ff.normalize_segment(
                        banner, normalized_banner, banner_info, banner_width, banner_height, fps,
                        banner_layout, self.settings.use_nvenc,
                        self.settings.quality, self.settings.bitrate_mbps,
                        duration=banner_info.duration, volume=self.settings.banner_volume,
                        label="Подготовка баннера",
                    )
                    banner_cache[cache_key] = normalized_banner
                normalized_banner_info = ff.probe(normalized_banner)

                for part in parts:
                    if self.cancel_event.is_set():
                        raise ProcessingError("Обработка отменена пользователем.")
                    message = f"{source.name}: часть {part.number} из {len(parts)}"
                    self.progress(done, total, message)
                    self.log(f"\n[{done + 1}/{total}] {message}")
                    token = f"job_{done + 1:04d}"
                    main_temp = temp_root / f"{token}_main.mp4"
                    audio_temp = temp_root / f"{token}_audio.wav"
                    ass_temp = temp_root / f"{token}.ass"

                    ff.normalize_segment(
                        source, main_temp, source_info, width, height, fps,
                        self.settings.aspect_mode, self.settings.use_nvenc,
                        self.settings.quality, self.settings.bitrate_mbps,
                        start=part.start, duration=part.duration,
                        label="Подготовка фрагмента",
                    )
                    main_info = ff.probe(main_temp)
                    words = []
                    if self.settings.recognition != "none" and source_info.has_audio:
                        ff.extract_audio(source, part.start, part.duration, audio_temp)
                        words = transcribe(audio_temp, self.settings, self.log)
                        self.log(f"Распознано слов: {len(words)}")
                    elif not source_info.has_audio:
                        self.log("В исходнике нет звуковой дорожки — субтитры пропущены.")

                    point = insertion_time(self.settings, main_info.duration)
                    smart_point = choose_interesting_moment(
                        words, self.settings, main_info.duration,
                        normalized_banner_info.duration, self.log,
                    )
                    if smart_point is not None:
                        point = smart_point
                    write_ass(
                        ass_temp, title, words, self.settings, width, height,
                        main_info.duration, point, normalized_banner_info.duration,
                    )
                    filename = f"{safe_filename(title)} — часть {part.number}.mp4"
                    target = unique_output_path(output_dir, filename)
                    ff.compose_with_banner(
                        main_temp, normalized_banner, ass_temp, target,
                        main_info.duration, point, normalized_banner_info.duration,
                        self.settings.banner_mode, width, height,
                        self.settings.banner_position_percent,
                        self.settings.main_volume_during_banner,
                        self.settings.use_nvenc,
                        self.settings.quality, self.settings.bitrate_mbps,
                    )
                    created.append(target)
                    done += 1
                    self.progress(done, total, message)
                    self.log(f"Готово: {target}")
                    for temporary in (main_temp, audio_temp, ass_temp):
                        temporary.unlink(missing_ok=True)
            return created
        except (FFmpegError, TranscriptionError, OSError) as exc:
            raise ProcessingError(str(exc)) from exc
        finally:
            if self.settings.keep_temp:
                self.log(f"Временные файлы сохранены: {temp_root}")
            else:
                shutil.rmtree(temp_root, ignore_errors=True)
