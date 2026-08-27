from __future__ import annotations

import json
import os
import re
from typing import Callable

from .settings import Settings
from .transcription import Word


def _api_key(settings: Settings) -> str:
    return settings.groq_api_key.strip() or os.environ.get("GROQ_API_KEY", "")


def _timed_transcript(words: list[Word], start: float, end: float) -> str:
    selected = [word for word in words if word.end >= start and word.start <= end]
    return " ".join(f"[{word.start:.2f}] {word.text}" for word in selected)


def _response_texts(completion) -> list[str]:
    """Return final and reasoning text used by different Groq model families."""
    message = completion.choices[0].message
    texts: list[str] = []
    for name in ("content", "reasoning_content", "reasoning"):
        value = getattr(message, name, None)
        if value:
            text = str(value).strip()
            if text and text not in texts:
                texts.append(text)
    return texts


def _json_result(content: str) -> tuple[float, str]:
    cleaned = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    data = json.loads(match.group(0) if match else cleaned)
    return float(data["time"]), str(data.get("reason", "выбрана точка максимальной интриги")).strip()


def _time_result(texts: list[str], allow_bare_number: bool = False) -> tuple[float, str]:
    for content in texts:
        match = re.search(r"\bTIME\s*[:=]\s*(\d+(?:[.,]\d+)?)", content, flags=re.IGNORECASE)
        if match:
            requested = float(match.group(1).replace(",", "."))
            reason_match = re.search(r"\bREASON\s*[:=]\s*(.+)", content, flags=re.IGNORECASE)
            reason = reason_match.group(1).strip() if reason_match else "точка максимальной интриги"
            return requested, reason
        try:
            return _json_result(content)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    if allow_bare_number:
        for content in texts:
            matches = re.findall(r"(?m)^\s*(\d+(?:[.,]\d+)?)\s*$", content)
            if matches:
                return float(matches[-1].replace(",", ".")), "точка выбрана числовым ответом Groq"
    raise ValueError("в ответе Groq не найдено время")


def choose_interesting_moment(
    words: list[Word],
    settings: Settings,
    duration: float,
    banner_duration: float,
    log: Callable[[str], None],
) -> float | None:
    """Ask Groq to choose a curiosity/peak point and return a safe word boundary."""
    if not settings.smart_insert:
        return None
    if not words:
        log("Умный момент пропущен: нет расшифровки с таймкодами. Используется обычная точка.")
        return None
    key = _api_key(settings)
    if not key:
        log("Умный момент пропущен: не указан Groq API key. Используется обычная точка.")
        return None

    lower = max(0.05, min(settings.smart_range_start, duration - 0.05))
    upper = min(settings.smart_range_end, duration - 0.05)
    if settings.banner_mode == "overlay":
        upper = min(upper, max(lower, duration - min(banner_duration, duration - 0.05)))
    if upper <= lower + 0.2:
        log("Умный момент пропущен: выбранный диапазон слишком короткий.")
        return None

    transcript = _timed_transcript(words, max(0.0, lower - 3.0), min(duration, upper + 3.0))
    if len(transcript) < 20:
        log("Умный момент пропущен: в диапазоне почти нет распознанной речи.")
        return None

    example_time = (lower + upper) / 2
    prompt = f"""Выбери момент начала короткого рекламного баннера в вертикальном ролике.
Допустимый диапазон: от {lower:.2f} до {upper:.2f} секунды.
Лучший вариант — пик любопытства непосредственно ПЕРЕД ответом или развязкой: например после фразы
«пришло время рассказать, кто был тайным гостем — это был…», но до имени гостя.
Если явной интриги нет, выбери наиболее эмоциональный, напряжённый или смешной переход.
Не ставь баннер после развязки и не выбирай середину слова.

Расшифровка с временами начала слов:
{transcript}

Финальный ответ дай одной строкой без Markdown и без JSON:
TIME={example_time:.2f} REASON=короткая причина
Вместо {example_time:.2f} обязательно поставь выбранное время из допустимого диапазона."""

    log(f"Groq анализирует интересный момент ({lower:.0f}–{upper:.0f} сек.)…")
    try:
        from groq import Groq

        client = Groq(api_key=key)
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты монтажёр коротких роликов. Выбирай точку рекламной перебивки, "
                    "которая усиливает интригу, и строго соблюдай заданный диапазон."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        request = {
            "model": settings.groq_analysis_model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": 600,
        }
        if "gpt-oss" in settings.groq_analysis_model.lower():
            request["reasoning_effort"] = "low"
        try:
            completion = client.chat.completions.create(**request)
            requested, reason = _time_result(_response_texts(completion))
        except Exception as first_exc:
            log(f"Groq не вернул TIME=секунды ({first_exc}). Повторяю запрос: только число…")
            retry_messages = [
                messages[0],
                {
                    "role": "user",
                    "content": (
                        f"Выбери одну точку рекламной перебивки от {lower:.2f} до {upper:.2f} секунды. "
                        "Используй расшифровку ниже и выбери пик интриги перед развязкой. "
                        f"Ответь ТОЛЬКО одним числом, например {example_time:.2f}. "
                        "Не добавляй слов, JSON или пояснений.\n\n"
                        + transcript
                    ),
                },
            ]
            retry_request = dict(request)
            retry_request["messages"] = retry_messages
            retry_request["max_completion_tokens"] = 600
            completion = client.chat.completions.create(**retry_request)
            requested, reason = _time_result(_response_texts(completion), allow_bare_number=True)

        if not lower <= requested <= upper:
            raise ValueError(f"Groq вернул время вне диапазона: {requested:.2f}")

        # Snap to the nearest completed word so the advert never starts in
        # the middle of speech. Prefer the boundary just before the answer.
        boundaries = [word.end for word in words if lower <= word.end <= upper]
        point = min(boundaries, key=lambda value: abs(value - requested)) if boundaries else requested
        log(f"Groq выбрал {point:.2f} сек.: {reason}")
        return point
    except Exception as exc:
        log(f"Оба варианта Groq-анализа не сработали ({exc}). Используется обычная точка вставки.")
        return None
