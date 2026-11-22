"""Deliver reports: Discord webhook and/or Telegram bot, or a local outbox file."""

from __future__ import annotations

import html
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
TELEGRAM_LIMIT = 4096
TELEGRAM_API = "https://api.telegram.org"
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


def format_telegram(report: Report, emoji: str) -> str:
    """Telegram HTML. Every line closes its own tags, so splitting on lines stays valid."""
    esc = html.escape
    lines = [f"{emoji} <b>{esc(report.title)}</b>"]
    if report.headline:
        lines.append(f"<b>{esc(report.headline)}</b>")
    if report.summary:
        lines += ["", esc(report.summary)]
    lines.append("")
    for n, item in enumerate(report.items, start=1):
        head = f'{n}. <a href="{esc(item.url, quote=True)}">{esc(item.title)}</a>'
        if item.source:
            head += f" · <i>{esc(item.source)}</i>"
        lines.append(head)
        if item.blurb:
            lines.append(esc(item.blurb))
    return "\n".join(lines).strip()


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
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
            raise DeliveryError("TELEGRAM_BOT_TOKEN does not look like a bot token")
        if not re.fullmatch(r"-?\d+", chat_id):
            raise DeliveryError("TELEGRAM_CHAT_ID must be a number")
        self._url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.http = http
        self.sleep = sleep
        self.part_delay = part_delay

    def _post(self, text: str) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self.http.post_json(self._url, payload)
                return
            except HttpError as exc:
                if exc.status == 429 and attempt < MAX_ATTEMPTS:
                    self.sleep(min(_telegram_retry_after(exc), MAX_RETRY_AFTER_SECONDS))
                    continue
                # Never include exc/url in the message: the URL contains the bot token.
                raise DeliveryError(
                    f"Telegram sendMessage failed: HTTP {exc.status} {_telegram_reason(exc)}"
                ) from None

    def deliver(self, report: Report, emoji: str) -> None:
        parts = split_message(format_telegram(report, emoji), TELEGRAM_LIMIT)
        for i, part in enumerate(parts):
            if i:
                self.sleep(self.part_delay)
            self._post(part)


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
    desc = _telegram_body(exc).get("description")
    return f"({str(desc)[:120]})" if desc else ""


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


def make_deliverer(settings: Settings, http: HttpClient, clock: Clock) -> Deliverer:
    if settings.fake:
        return OutboxWriter(settings.data_dir, clock)
    channels: list[Deliverer] = []
    if settings.discord_webhook_url:
        channels.append(DiscordWebhook(settings.discord_webhook_url, http))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append(TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id, http))
    if not channels:
        return OutboxWriter(settings.data_dir, clock)
    return channels[0] if len(channels) == 1 else MultiDeliverer(channels)
