"""Shared delivery types, limits and message splitting."""

from __future__ import annotations

from typing import Protocol

from mybuddy.models import Report

DISCORD_LIMIT = 2000
TELEGRAM_LIMIT = 4096
PART_DELAY_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0
MAX_ATTEMPTS = 3


class DeliveryError(Exception):
    pass


class Deliverer(Protocol):
    def deliver(self, report: Report, emoji: str) -> None: ...


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split on line boundaries into chunks of at most `limit` characters."""
    chunks: list[str] = []
    current = ""
    for line in text.rstrip("\n").split("\n"):
        while len(line) > limit:  # pathological single line: hard cut
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()]


class MultiDeliverer:
    """Deliver to every configured channel; fail only after trying all of them."""

    def __init__(self, deliverers: list[Deliverer]) -> None:
        self.deliverers = deliverers

    def deliver(self, report: Report, emoji: str) -> None:
        errors: list[str] = []
        for d in self.deliverers:
            try:
                d.deliver(report, emoji)
            except DeliveryError as exc:
                errors.append(str(exc))
        if errors:
            raise DeliveryError("; ".join(errors))
