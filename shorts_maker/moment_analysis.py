from __future__ import annotations

import json
import os
import re
from typing import Callable

from .settings import Settings
from .transcription import Word


def _api_key(settings: Settings) -> str:
    return settings.groq_api_key.strip() or os.environ.get("GROQ_API_KEY", "")


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


def _json_data(content: str) -> dict:
    cleaned = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    data = json.loads(match.group(0) if match else cleaned)
    if not isinstance(data, dict):
        raise ValueError("ответ JSON не является объектом")
    return data


def _candidate_points(words: list[Word], lower: float, upper: float, limit: int = 28) -> list[float]:
    """Build a compact set of legal word boundaries for the language model."""
    boundaries = [word.end for word in words if lower <= word.end <= upper]
    if not boundaries:
        return []
    if len(boundaries) <= limit:
        return boundaries

    important: set[float] = set()
    for index, word in enumerate(words):
        if not lower <= word.end <= upper:
            continue
        next_start = words[index + 1].start if index + 1 < len(words) else word.end
        pause = max(0.0, next_start - word.end)
        if pause >= 0.32 or word.text.rstrip().endswith(("?", "!", "…", ":")):
            important.add(word.end)

    slots = max(2, limit - min(len(important), limit // 2))
    for slot in range(slots):
        target = lower + (upper - lower) * slot / max(1, slots - 1)
        important.add(min(boundaries, key=lambda value: abs(value - target)))

    selected = sorted(important)
    if len(selected) > limit:
        selected = [
            selected[round(index * (len(selected) - 1) / (limit - 1))]
            for index in range(limit)
        ]
    return list(dict.fromkeys(selected))


def _candidate_context(words: list[Word], point: float) -> str:
    before = [word.text for word in words if word.end <= point][-9:]
    after = [word.text for word in words if word.start > point][:7]
    return f"{' '.join(before)} / {' '.join(after)}".strip(" /" )


def _candidate_prompt(words: list[Word], candidates: list[float]) -> str:
    return "\n".join(
        f"{index}. [{point:.2f} сек.] {_candidate_context(words, point)}"
        for index, point in enumerate(candidates, 1)
    )


def _selection_result(
    texts: list[str], candidates: list[float], lower: float, upper: float, allow_bare_number: bool = False
) -> tuple[float, str]:
    """Accept CHOICE, legacy TIME/JSON, and a deliberately bare retry answer."""
    for content in texts:
        match = re.search(r"\b(?:CHOICE|ВАРИАНТ|НОМЕР)\s*[:=#-]?\s*(\d+)", content, flags=re.IGNORECASE)
        if match:
            choice = int(match.group(1))
            if 1 <= choice <= len(candidates):
                reason_match = re.search(r"\bREASON\s*[:=]\s*(.+)", content, flags=re.IGNORECASE)
                reason = reason_match.group(1).strip() if reason_match else "выбран пик интриги"
                return candidates[choice - 1], reason

        match = re.search(r"\bTIME\s*[:=]\s*(\d+(?:[.,]\d+)?)", content, flags=re.IGNORECASE)
        if match:
            requested = float(match.group(1).replace(",", "."))
            if lower <= requested <= upper:
                return requested, "выбрано по времени Groq"

        try:
            data = _json_data(content)
            if "choice" in data:
                choice = int(data["choice"])
                if 1 <= choice <= len(candidates):
                    return candidates[choice - 1], str(data.get("reason", "выбран пик интриги"))
            if "time" in data:
                requested = float(data["time"])
                if lower <= requested <= upper:
                    return requested, str(data.get("reason", "выбрано по времени Groq"))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass

    if allow_bare_number:
        for content in texts:
            matches = re.findall(r"(?m)^\s*(\d+(?:[.,]\d+)?)\s*$", content)
            if not matches:
                continue
            value = matches[-1]
            if "." not in value and "," not in value:
                choice = int(value)
                if 1 <= choice <= len(candidates):
                    return candidates[choice - 1], "выбран номер точки Groq"
            requested = float(value.replace(",", "."))
            if lower <= requested <= upper:
                return requested, "выбрано числовым ответом Groq"
    raise ValueError("в ответе Groq не найден корректный номер точки")


_STRONG_PHRASES: tuple[tuple[str, float], ...] = (
    ("это был", 14.0), ("это была", 14.0), ("это были", 14.0),
    ("и это", 9.0), ("тайный гость", 12.0), ("кто был", 10.0),
    ("пришло время", 11.0), ("сейчас расскажу", 11.0),
    ("сейчас узнаем", 10.0), ("вот кто", 10.0), ("вот что", 8.0),
    ("не поверите", 9.0), ("самое главное", 8.0), ("оказалось", 7.0),
    ("в последний момент", 8.0), ("решающий момент", 8.0),
    ("подожди", 5.0), ("смотрите", 4.0), ("но", 3.0),
)


def _normalise(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9 ]+", " ", value.lower().replace("ё", "е")).strip()


def _local_interesting_moment(words: list[Word], lower: float, upper: float) -> tuple[float, str] | None:
    """Pick a reveal/question/pause boundary when the cloud answer is unavailable."""
    ranked: list[tuple[float, float, str]] = []
    center = lower + (upper - lower) * 0.58
    for index, word in enumerate(words):
        if not lower <= word.end <= upper:
            continue
        context_words = [item.text for item in words[max(0, index - 10): index + 1]]
        context = _normalise(" ".join(context_words))
        score = 0.0
        reason = "выразительная пауза"
        for phrase, weight in _STRONG_PHRASES:
            position = context.rfind(phrase)
            if position >= 0:
                distance = len(context) - (position + len(phrase))
                phrase_score = weight * max(0.35, 1.0 - distance / 45.0)
                if phrase_score > score:
                    score = phrase_score
                    reason = f"фраза «{phrase}» перед продолжением"

        raw = word.text.rstrip()
        if raw.endswith("?"):
            score += 8.0
            reason = "вопрос перед ответом"
        elif raw.endswith(("…", "...", ":")):
            score += 6.0
            reason = "незавершённая фраза перед продолжением"
        elif raw.endswith("!"):
            score += 3.0

        next_start = words[index + 1].start if index + 1 < len(words) else word.end
        pause = max(0.0, next_start - word.end)
        score += min(pause, 1.5) * 5.0
        if any(token in context.split()[-6:] for token in ("кто", "что", "почему", "зачем", "как")):
            score += 3.5
            if reason == "выразительная пауза":
                reason = "вопросительная фраза перед ответом"

        score -= abs(word.end - center) * 0.01
        ranked.append((score, word.end, reason))

    if not ranked:
        return None
    score, point, reason = max(ranked, key=lambda item: (item[0], -item[1]))
    if score < 0.5:
        return None
    return point, reason


def choose_interesting_moment(
    words: list[Word],
    settings: Settings,
    duration: float,
    banner_duration: float,
    log: Callable[[str], None],
) -> float | None:
    """Ask Groq to choose a curiosity peak, with a speech-aware local fallback."""
    if not settings.smart_insert:
        return None
    if not words:
        log("Умный момент пропущен: нет расшифровки с таймкодами. Используется заданная точка.")
        return None

    lower = max(0.05, min(settings.smart_range_start, duration - 0.05))
    upper = min(settings.smart_range_end, duration - 0.05)
    if settings.banner_mode == "overlay":
        upper = min(upper, max(lower, duration - min(banner_duration, duration - 0.05)))
    if upper <= lower + 0.2:
        log("Умный момент пропущен: выбранный диапазон слишком короткий.")
        return None

    candidates = _candidate_points(words, lower, upper)
    local_result = _local_interesting_moment(words, lower, upper)
    if local_result and all(abs(point - local_result[0]) > 0.08 for point in candidates):
        # Always offer Groq the strongest locally detected reveal boundary,
        # even when uniform candidate thinning would otherwise skip it.
        candidates = sorted([*candidates, local_result[0]])
    if not candidates:
        log("Умный момент пропущен: в диапазоне почти нет распознанной речи.")
        return None

    key = _api_key(settings)
    if not key:
        if local_result:
            point, reason = local_result
            log(f"Локальный анализ выбрал {point:.2f} сек.: {reason}")
            return point
        log("Не указан Groq API key. Используется заданная точка вставки.")
        return None

    candidate_text = _candidate_prompt(words, candidates)
    prompt = f"""Выбери ОДИН номер точки для начала короткого рекламного баннера.
Допустимы только номера из списка ниже. Точка должна быть непосредственно перед ответом,
именем, развязкой, результатом шутки или на эмоциональном пике. Особенно хорошо прервать
незавершённую фразу вроде «тайным гостем был…». Не выбирай точку после раскрытия интриги.
Ставь баннер МАКСИМАЛЬНО ПОЗДНО перед ключевым словом ответа. Служебные слова
«это был», «это оказалась», «победил» должны успеть прозвучать; скрыть нужно имя,
предмет или результат после них. Например, для фразы
«пришло время рассказать, кто был гостем — это был / Иван» выбери точку после «был»,
а не после слов «время», «рассказать» или «гостем». Символ / показывает точное место точки.

Кандидаты (слева речь до точки, справа — после неё):
{candidate_text}

Ответь одной строкой без Markdown и JSON: CHOICE=<номер> REASON=<короткая причина>"""

    log(f"Groq выбирает напряжённый момент из {len(candidates)} точек ({lower:.0f}–{upper:.0f} сек.)…")
    try:
        from groq import Groq

        client = Groq(api_key=key)
        system = {
            "role": "system",
            "content": (
                "Ты монтажёр вирусных коротких видео. Выбирай максимально позднюю рекламную перебивку "
                "перед ключевым именем, предметом или результатом. Фразу-подводку «это был» оставляй до баннера. "
                "Никогда не возвращай JSON; "
                "верни только запрошенный номер кандидата."
            ),
        }
        request = {
            "model": settings.groq_analysis_model,
            "messages": [system, {"role": "user", "content": prompt}],
            "temperature": 0,
            "max_completion_tokens": 400,
        }
        if "gpt-oss" in settings.groq_analysis_model.lower():
            request["reasoning_effort"] = "low"

        try:
            completion = client.chat.completions.create(**request)
            requested, reason = _selection_result(
                _response_texts(completion), candidates, lower, upper
            )
        except Exception as first_exc:
            log(f"Первый ответ Groq не удалось прочитать ({first_exc}). Повторяю: только номер точки…")
            retry = dict(request)
            retry["messages"] = [
                system,
                {
                    "role": "user",
                    "content": (
                        f"Выбери лучший номер от 1 до {len(candidates)}: максимально поздно, но перед "
                        "первым словом ответа или развязки. "
                        "Ответь ТОЛЬКО целым числом без слов.\n\n" + candidate_text
                    ),
                },
            ]
            retry["max_completion_tokens"] = 200
            completion = client.chat.completions.create(**retry)
            requested, reason = _selection_result(
                _response_texts(completion), candidates, lower, upper, allow_bare_number=True
            )

        boundaries = [word.end for word in words if lower <= word.end <= upper]
        point = min(boundaries, key=lambda value: abs(value - requested)) if boundaries else requested
        log(f"Groq выбрал {point:.2f} сек.: {reason}")
        return point
    except Exception as exc:
        if local_result:
            point, reason = local_result
            log(f"Groq недоступен ({exc}). Локальный анализ выбрал {point:.2f} сек.: {reason}")
            return point
        log(f"Groq недоступен ({exc}). Используется заданная точка вставки.")
        return None
