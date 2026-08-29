from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shorts_maker.ffmpeg_tools import (
    FFmpeg,
    FFmpegError,
    MediaInfo,
    output_fps,
    output_geometry,
    overlay_banner_geometry,
)
from shorts_maker.moment_analysis import choose_interesting_moment
from shorts_maker.processor import insertion_time, plan_parts
from shorts_maker.settings import Settings
from shorts_maker.subtitles import group_words, shifted_words, write_ass
from shorts_maker.transcription import Word
from shorts_maker.utils import safe_filename


class PartPlanningTests(unittest.TestCase):
    def test_regular_parts(self):
        parts = plan_parts(130, 60, 5)
        self.assertEqual([(p.start, p.duration) for p in parts], [(0, 60), (60, 60), (120, 10)])

    def test_short_tail_is_merged(self):
        parts = plan_parts(123, 60, 5)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[-1].duration, 63)

    def test_short_video(self):
        parts = plan_parts(3.5, 60, 5)
        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0].duration, 3.5)

    def test_insertion_modes(self):
        self.assertAlmostEqual(insertion_time(Settings(insert_mode="percent", insert_value=50), 60), 30)
        self.assertAlmostEqual(insertion_time(Settings(insert_mode="fixed", insert_value=12), 60), 12)
        self.assertLess(insertion_time(Settings(insert_mode="fixed", insert_value=999), 60), 60)


class SubtitleTests(unittest.TestCase):
    def test_shift_after_banner(self):
        words = [Word(1, 2, "до"), Word(31, 32, "после")]
        shifted = shifted_words(words, 30, 4)
        self.assertEqual(shifted[0].start, 1)
        self.assertEqual(shifted[1].start, 35)

    def test_grouping(self):
        words = [Word(i, i + .4, value) for i, value in enumerate("один два три четыре пять шесть".split())]
        groups = group_words(words, 3, 100)
        self.assertEqual([len(g.words) for g in groups], [3, 3])

    def test_ass_contains_title_and_highlight(self):
        settings = Settings(recognition="none")
        words = [Word(1, 1.4, "Привет"), Word(1.5, 2, "мир")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.ass"
            write_ass(path, "Тестовое название", words, settings, 1080, 1920, 10, 5, 3)
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("Тестовое название", content)
            self.assertIn("Dialogue: 2", content)
            self.assertIn("&H0000D4FF&", content)  # #FFD400 in ASS BGR

    def test_overlay_does_not_shift_speech(self):
        settings = Settings(banner_mode="overlay")
        words = [Word(6, 6.4, "после")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "overlay.ass"
            write_ass(path, "Тест", words, settings, 1080, 1920, 10, 5, 3)
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("Dialogue: 2,0:00:06.00", content)
            self.assertIn("Dialogue: 1,0:00:00.00,0:00:10.00", content)


class UtilityTests(unittest.TestCase):
    def test_geometry(self):
        info = MediaInfo(10, 1920, 1080, 29.97, True)
        self.assertEqual(output_geometry(info, "1080x1920", "vertical_crop"), (1080, 1920))
        self.assertEqual(output_geometry(info, "original", "original"), (1920, 1080))
        self.assertAlmostEqual(output_fps(info, "source"), 29.97)

    def test_filename(self):
        self.assertEqual(safe_filename('a:b?c*'), "a_b_c_")

    def test_blurred_layout_keeps_full_frame(self):
        graph = FFmpeg.video_filter(1080, 1920, 30, "vertical_blur")
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", graph)
        self.assertIn("boxblur=", graph)

    def test_overlay_banner_geometry_keeps_aspect(self):
        banner = MediaInfo(4, 1920, 1080, 30, True)
        self.assertEqual(overlay_banner_geometry(1080, 1920, banner, 88), (950, 534))

    def test_old_crop_setting_is_migrated(self):
        loaded = Settings.from_dict({"aspect_mode": "vertical_crop"})
        self.assertEqual(loaded.aspect_mode, "vertical_blur")
        self.assertEqual(loaded.banner_mode, "overlay")
        self.assertEqual(loaded.settings_version, 3)

    def test_groq_moment_is_snapped_to_word_boundary(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="CHOICE=2 REASON=интрига"))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
        )
        fake_groq = SimpleNamespace(Groq=lambda **_kwargs: client)
        words = [
            Word(15.0, 15.5, "пришло"),
            Word(20.0, 20.4, "был"),
            Word(20.8, 21.1, "гость"),
        ]
        settings = Settings(smart_insert=True, groq_api_key="test-key")
        with patch.dict(sys.modules, {"groq": fake_groq}):
            point = choose_interesting_moment(words, settings, 60, 4, lambda _message: None)
        self.assertAlmostEqual(point, 20.4)

    def test_groq_invalid_first_answer_retries_with_bare_number(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="не могу выбрать"))]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="20.45"))]
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        fake_groq = SimpleNamespace(Groq=lambda **_kwargs: client)
        words = [
            Word(15.0, 15.5, "пришло"),
            Word(20.0, 20.4, "был"),
            Word(20.8, 21.1, "гость"),
        ]
        logs = []
        settings = Settings(smart_insert=True, groq_api_key="test-key")
        with patch.dict(sys.modules, {"groq": fake_groq}):
            point = choose_interesting_moment(words, settings, 60, 4, logs.append)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("response_format", calls[0])
        self.assertAlmostEqual(point, 20.4)
        self.assertTrue(any("только номер" in message for message in logs))

    def test_local_fallback_prefers_point_before_reveal(self):
        values = [
            (20.0, 20.3, "пришло"), (20.4, 20.7, "время"),
            (20.8, 21.2, "рассказать"), (21.3, 21.5, "кто"),
            (21.6, 21.8, "был"), (21.9, 22.2, "тайным"),
            (22.3, 22.6, "гостем"), (22.7, 22.9, "это"),
            (23.0, 23.2, "был…"), (23.6, 23.9, "Иван"),
        ]
        words = [Word(*value) for value in values]
        logs = []
        settings = Settings(smart_insert=True, groq_api_key="")
        point = choose_interesting_moment(words, settings, 60, 4, logs.append)
        self.assertAlmostEqual(point, 23.2)
        self.assertTrue(any("Локальный анализ выбрал" in message for message in logs))

    def test_cancellation_does_not_trigger_cpu_retry(self):
        logs = []
        ffmpeg = FFmpeg(
            Path("ffmpeg.exe"), Path("ffprobe.exe"),
            log=logs.append, cancel_check=lambda: True,
        )
        with patch.object(ffmpeg, "supports_nvenc", return_value=True), patch.object(
            ffmpeg, "_run", side_effect=FFmpegError("Обработка отменена пользователем.")
        ) as run:
            with self.assertRaises(FFmpegError):
                ffmpeg.run_encode(lambda nvenc: [str(nvenc)], True, "Монтаж")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(any("NVENC не запустился" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
