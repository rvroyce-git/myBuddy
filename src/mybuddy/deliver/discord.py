"""Discord webhook delivery."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from mybuddy.deliver.base import (
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    PART_DELAY_SECONDS,
    DeliveryError,
    split_message,
)
from mybuddy.deliver.format import format_report
from mybuddy.http import HttpError
from mybuddy.models import HttpClient, Report


class DiscordWebhook:
    def __init__(
        self,
        url: str,
        http: HttpClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        part_delay: float = PART_DELAY_SECONDS,
    ) -> None:
        if not url.startswith("https://"):
            raise DeliveryError("DISCORD_WEBHOOK_URL must start with https://")
        self.url = url
        self.http = http
        self.sleep = sleep
        self.part_delay = part_delay

    def _post(self, content: str) -> None:
        payload = {"content": content, "allowed_mentions": {"parse": []}}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self.http.post_json(self.url, payload)
                return
            except HttpError as exc:
                if exc.status == 429 and attempt < MAX_ATTEMPTS:
                    self.sleep(min(_retry_after(exc), MAX_RETRY_AFTER_SECONDS))
                    continue
                raise DeliveryError(f"Discord webhook failed: HTTP {exc.status}") from None

    def deliver(self, report: Report, emoji: str) -> None:
        parts = split_message(format_report(report, emoji))
        for i, part in enumerate(parts):
            if i:
                self.sleep(self.part_delay)
            self._post(part)


def _retry_after(exc: HttpError) -> float:
    try:
        data: Any = json.loads(exc.body) if exc.body else {}
        value = float(data["retry_after"])
        return max(0.0, value)
    except (ValueError, TypeError, KeyError):
        pass
    return exc.retry_after if exc.retry_after is not None else 1.0
