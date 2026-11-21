"""Звернення до ШІ через OpenAI-сумісний API (у тебе це Gemini).

Бот пробує моделі по черзі: головну (OPENAI_MODEL), потім запасні
(OPENAI_FALLBACK_MODEL, можна кілька через кому). Перша, що відповіла, перемагає.
"""
import asyncio
import base64
import logging
import os
import time

import httpx

log = logging.getLogger("bot")

BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _build_models():
    raw = os.getenv("OPENAI_FALLBACK_MODELS") or os.getenv("OPENAI_FALLBACK_MODEL") or ""
    models = [MODEL]
    for name in raw.split(","):
        name = name.strip()
        if name and name not in models:
            models.append(name)
    return models


MODELS = _build_models()
# Швидкість: minimal / low / medium / high. Порожньо = рівень за замовчуванням моделі.
REASONING = os.getenv("REASONING_EFFORT", "").strip().lower()
# SHOW_MODEL=1 додає під відповіддю, яка модель відповіла й за скільки секунд.
SHOW_MODEL = os.getenv("SHOW_MODEL", "").strip().lower() in ("1", "true", "yes", "on")
RETRY_STATUS = {429, 500, 502, 503, 504}
ROUNDS = 2  # скільки разів пройти весь список моделей
ROUND_PAUSES = [5, 10]  # пауза (с) після кожного неуспішного кола


class AIError(Exception):
    """Помилка ШІ з коротким повідомленням, яке можна показати користувачці."""


async def ask_model(system, user_text, images=None):
    if images:
        content = [{"type": "text", "text": user_text}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
    else:
        content = user_text

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    started = time.monotonic()
    last_status = None
    dead = set()  # моделі, яких немає або вони недоступні для цього ключа
    no_reasoning = set()  # моделі, які не приймають reasoning_effort

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for rnd in range(ROUNDS):
            for model in MODELS:
                if model in dead:
                    continue
                while True:  # повторюємо лише для відкату reasoning_effort
                    payload = {"model": model, "messages": messages}
                    if REASONING and model not in no_reasoning:
                        payload["reasoning_effort"] = REASONING
                    try:
                        r = await client.post(
                            f"{BASE_URL}/chat/completions", headers=headers, json=payload
                        )
                        last_status = r.status_code
                        if r.status_code in RETRY_STATUS:
                            log.warning("ШІ (%s) повернув %s, пробую наступну модель", model, r.status_code)
                            break
                        r.raise_for_status()
                        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
                        if text:
                            elapsed = time.monotonic() - started
                            log.info(
                                "ШІ відповів за %.1f с (модель: %s, коло %d, зображень: %d, reasoning: %s)",
                                elapsed,
                                model,
                                rnd + 1,
                                len(images or []),
                                payload.get("reasoning_effort", "за замовчуванням"),
                            )
                            if SHOW_MODEL:
                                text += f"\n\n⚙️ {model}, {elapsed:.0f} с"
                            return text
                        log.warning("ШІ (%s) повернув порожню відповідь", model)
                        break
                    except httpx.HTTPStatusError as e:
                        code = e.response.status_code
                        last_status = code
                        log.error("ШІ (%s) помилка %s: %s", model, code, e.response.text[:300])
                        if code == 400 and "reasoning_effort" in payload:
                            no_reasoning.add(model)
                            log.warning("%s не приймає reasoning_effort, повторюю без нього", model)
                            continue
                        if code in (403, 404) and len(MODELS) > 1:
                            dead.add(model)
                            log.warning("Модель %s недоступна, виключаю зі списку", model)
                            break
                        raise AIError(
                            f"ШІ повернув помилку (код {code}). Подробиці в логах Railway."
                        )
                    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                        log.warning("Збій запиту до ШІ (%s): %s", model, type(e).__name__)
                        break

            if len(dead) == len(MODELS):
                break
            if rnd < ROUNDS - 1:
                await asyncio.sleep(ROUND_PAUSES[rnd])

    if len(dead) == len(MODELS):
        log.error("Жодна модель недоступна: %s", ", ".join(MODELS))
        raise AIError(
            f"Жодна з моделей недоступна (код {last_status}). Перевір назви моделей у Railway."
        )
    log.error("ШІ недоступний (моделі: %s; останній статус: %s)", ", ".join(MODELS), last_status)
    raise AIError("Сервіс ШІ тимчасово перевантажений, спробуй за хвилину.")
