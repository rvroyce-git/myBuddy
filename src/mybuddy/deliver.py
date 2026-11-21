"""Deliver reports: Discord webhook, or a local outbox file in fake mode."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from mybuddy.config import Settings
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Report
from mybuddy.store import ensure_private_dir

DISCORD_LIMIT = 2000
PART_DELAY_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0
MAX_ATTEMPTS = 3

_MD_SPECIAL = re.compile(r"([\\*_~`|\[\]<>#])")


class DeliveryError(Exception):
    pass


class Deliverer(Protocol):
    def deliver(self, report: Report, emoji: str) -> None: ...


def escape_md(text: str) -> str:
    """Escape Discord markdown so item text can't break formatting or fake links."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = _MD_SPECIAL.sub(r"\\\1", text)
    # Neutralise mass mentions even though allowed_mentions already blocks them.
    return text.replace("@everyone", "@​everyone").replace("@here", "@​here")


def safe_link_url(url: str) -> str:
    """Encode characters that would end a <url> or (url) Discord link early."""
    return quote(url.strip(), safe=":/?#[]@!$&'*+,;=%-._~")


def format_report(report: Report, emoji: str) -> str:
    lines = [f"{emoji} **{escape_md(report.title)}**"]
    if report.headline:
        lines.append(f"**{escape_md(report.headline)}**")
    if report.summary:
        lines += ["", escape_md(report.summary)]
    lines.append("")
    for n, item in enumerate(report.items, start=1):
        parts = [f"{n}. [{escape_md(item.title)}](<{safe_link_url(item.url)}>)"]
        if item.source:
            parts.append(escape_md(item.source))
        if item.blurb:
            parts.append(escape_md(item.blurb))
        lines.append(" — ".join(parts))
    return "\n".join(lines).strip() + "\n"


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


class OutboxWriter:
    """Fake delivery: write the formatted message to <data>/outbox/<topic>-<stamp>.md."""

    def __init__(self, data_dir: Path, clock: Clock) -> None:
        self.dir = data_dir / "outbox"
        self.clock = clock
        self.last_path: Path | None = None

    def deliver(self, report: Report, emoji: str) -> None:
        ensure_private_dir(self.dir)
        stamp = self.clock.now().strftime("%Y%m%d-%H%M%S")
        path = self.dir / f"{report.topic_id}-{stamp}.md"
        n = 1
        while path.exists():
            n += 1
            path = self.dir / f"{report.topic_id}-{stamp}-{n}.md"
        parts = split_message(format_report(report, emoji))
        path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        self.last_path = path


def make_deliverer(settings: Settings, http: HttpClient, clock: Clock) -> Deliverer:
    if settings.fake or not settings.discord_webhook_url:
        return OutboxWriter(settings.data_dir, clock)
    return DiscordWebhook(settings.discord_webhook_url, http)
