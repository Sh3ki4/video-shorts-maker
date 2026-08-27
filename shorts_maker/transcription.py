from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .settings import Settings


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def _clean_words(items) -> list[Word]:
    words: list[Word] = []
    for item in items or []:
        if isinstance(item, dict):
            start, end, text = item.get("start"), item.get("end"), item.get("word", item.get("text", ""))
        else:
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
            text = getattr(item, "word", getattr(item, "text", ""))
        try:
            cleaned = str(text).strip()
            if cleaned and start is not None and end is not None:
                words.append(Word(max(0.0, float(start)), max(float(start), float(end)), cleaned))
        except (TypeError, ValueError):
            continue
    return words


def transcribe_local(audio: Path, settings: Settings, log: Callable[[str], None]) -> list[Word]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "Локальный Whisper не установлен. Запустите «Установить_Whisper.bat» "
            "или выберите режим Groq/без субтитров."
        ) from exc

    requested = settings.whisper_device
    if requested == "auto":
        try:
            import ctranslate2
            requested = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            requested = "cpu"

    def run(device: str) -> list[Word]:
        compute_type = "float16" if device == "cuda" else "int8"
        log(f"Загрузка Whisper {settings.whisper_model} ({device})…")
        model = WhisperModel(settings.whisper_model, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(
            str(audio), language=settings.language or "ru", word_timestamps=True,
            vad_filter=True, beam_size=5,
        )
        result: list[Word] = []
        for segment in segments:
            result.extend(_clean_words(getattr(segment, "words", None)))
        return result

    try:
        return run(requested)
    except Exception as exc:
        if requested != "cuda":
            raise TranscriptionError(f"Whisper не смог распознать звук: {exc}") from exc
        log("Whisper не запустился на видеокарте — повторяю на процессоре…")
        try:
            return run("cpu")
        except Exception as cpu_exc:
            raise TranscriptionError(f"Whisper не смог распознать звук: {cpu_exc}") from cpu_exc


def transcribe_groq(audio: Path, settings: Settings, log: Callable[[str], None]) -> list[Word]:
    try:
        from groq import Groq
    except ImportError as exc:
        raise TranscriptionError(
            "Модуль Groq не установлен. Запустите «Установить_Groq.bat»."
        ) from exc
    key = settings.groq_api_key.strip() or os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise TranscriptionError("Не указан Groq API key в настройках.")
    log("Отправка аудио в Groq Whisper…")
    try:
        client = Groq(api_key=key)
        with audio.open("rb") as stream:
            result = client.audio.transcriptions.create(
                file=(audio.name, stream.read()),
                model=settings.groq_model,
                language=settings.language or "ru",
                response_format="verbose_json",
                timestamp_granularities=["word"],
                temperature=0,
            )
        words = getattr(result, "words", None)
        if words is None and isinstance(result, dict):
            words = result.get("words")
        return _clean_words(words)
    except Exception as exc:
        raise TranscriptionError(f"Groq не смог распознать звук: {exc}") from exc


def transcribe(audio: Path, settings: Settings, log: Callable[[str], None]) -> list[Word]:
    if settings.recognition == "none":
        return []
    if settings.recognition == "groq":
        return transcribe_groq(audio, settings, log)
    return transcribe_local(audio, settings, log)
