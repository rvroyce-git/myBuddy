"""Telegram Bot API delivery to the owner chat, plus shared Bot API helpers."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from mybuddy.deliver.base import (
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    PART_DELAY_SECONDS,
    TELEGRAM_LIMIT,
    DeliveryError,
    split_message,
)
from mybuddy.deliver.format import format_telegram
from mybuddy.feedback import feedback_markup
from mybuddy.http import HttpError
from mybuddy.models import HttpClient, Report

TELEGRAM_API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")


class TelegramBot:
    """Sends reports through the Telegram Bot API to one chat (the owner's)."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        http: HttpClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        part_delay: float = PART_DELAY_SECONDS,
    ) -> None:
        if not TOKEN_RE.fullmatch(token):
            raise DeliveryError("TELEGRAM_BOT_TOKEN does not look like a bot token")
        if not re.fullmatch(r"-?\d+", chat_id):
            raise DeliveryError("TELEGRAM_CHAT_ID must be a number")
        self._url = telegram_url(token, "sendMessage")
        self.chat_id = chat_id
        self.http = http
        self.sleep = sleep
        self.part_delay = part_delay

    def _post(self, text: str, markup: dict[str, Any] | None = None) -> None:
        try:
            payload = _html_message(self.chat_id, text, markup)
            post_telegram(self.http, self._url, payload, self.sleep)
        except HttpError as exc:
            # Never include exc/url in the message: the URL contains the bot token.
            raise DeliveryError(
                f"Telegram sendMessage failed: HTTP {exc.status} {_telegram_reason(exc)}"
            ) from None

    def deliver(self, report: Report, emoji: str) -> None:
        parts = split_message(format_telegram(report, emoji), TELEGRAM_LIMIT)
        for i, part in enumerate(parts):
            if i:
                self.sleep(self.part_delay)
            # 👍/👎 under the last part only (see feedback.py)
            self._post(part, feedback_markup(report) if i == len(parts) - 1 else None)


def telegram_url(token: str, method: str) -> str:
    return f"{TELEGRAM_API}/bot{token}/{method}"


def _html_message(
    chat_id: str | int, text: str, markup: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if markup is not None:
        payload["reply_markup"] = markup
    return payload


def post_telegram(
    http: HttpClient,
    url: str,
    payload: Any,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """POST to the Bot API, waiting out 429s (retry_after, capped). Other errors: HttpError."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return http.post_json(url, payload)
        except HttpError as exc:
            if exc.status == 429 and attempt < MAX_ATTEMPTS:
                sleep(min(_telegram_retry_after(exc), MAX_RETRY_AFTER_SECONDS))
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover


def telegram_description(exc: HttpError) -> str:
    """Telegram's error description (safe to log: it never contains the token)."""
    desc = _telegram_body(exc).get("description")
    return str(desc)[:120] if desc else ""


def chat_is_gone(exc: HttpError) -> bool:
    """True when a private chat can never be messaged again (blocked, deleted, unknown)."""
    if exc.status == 403:  # "bot was blocked by the user", "user is deactivated", ...
        return True
    return exc.status == 400 and "chat not found" in telegram_description(exc).lower()


def _telegram_body(exc: HttpError) -> dict[str, Any]:
    try:
        data = json.loads(exc.body) if exc.body else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _telegram_retry_after(exc: HttpError) -> float:
    params = _telegram_body(exc).get("parameters")
    if isinstance(params, dict) and isinstance(params.get("retry_after"), int | float):
        return max(0.0, float(params["retry_after"]))
    return exc.retry_after if exc.retry_after is not None else 1.0


def _telegram_reason(exc: HttpError) -> str:
    desc = telegram_description(exc)
    return f"({desc})" if desc else ""
