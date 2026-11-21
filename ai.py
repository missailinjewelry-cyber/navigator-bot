"""Звернення до ШІ через OpenAI-сумісний API (у тебе це Gemini)."""
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
# Швидкість: minimal / low / medium / high. Порожньо = рівень за замовчуванням моделі.
REASONING = os.getenv("REASONING_EFFORT", "").strip().lower()
RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 3


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

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if REASONING:
        payload["reasoning_effort"] = REASONING

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    busy_msg = "Сервіс ШІ тимчасово перевантажений, спробуй за хвилину."
    last_status = None
    started = time.monotonic()
    attempt = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        while attempt < ATTEMPTS:
            attempt += 1
            try:
                r = await client.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload)
                last_status = r.status_code
                if r.status_code in RETRY_STATUS:
                    log.warning("ШІ повернув %s (спроба %d/%d)", r.status_code, attempt, ATTEMPTS)
                else:
                    r.raise_for_status()
                    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
                    if text:
                        log.info(
                            "ШІ відповів за %.1f с (спроба %d, зображень: %d, reasoning: %s)",
                            time.monotonic() - started,
                            attempt,
                            len(images or []),
                            payload.get("reasoning_effort", "за замовчуванням"),
                        )
                        return text
                    log.warning("ШІ повернув порожню відповідь (спроба %d/%d)", attempt, ATTEMPTS)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                log.error("ШІ помилка %s: %s", code, e.response.text[:300])
                if code == 400 and "reasoning_effort" in payload:
                    log.warning("Модель не приймає reasoning_effort, повторюю без нього")
                    payload.pop("reasoning_effort", None)
                    attempt -= 1
                    continue
                raise AIError(f"ШІ повернув помилку (код {code}). Подробиці в логах Railway.")
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                log.warning("Збій запиту до ШІ: %s (спроба %d/%d)", type(e).__name__, attempt, ATTEMPTS)
            if attempt < ATTEMPTS:
                await asyncio.sleep(2 * attempt)  # 2 с, потім 4 с

    log.error("ШІ недоступний після %d спроб (останній статус: %s)", ATTEMPTS, last_status)
    raise AIError(busy_msg)
