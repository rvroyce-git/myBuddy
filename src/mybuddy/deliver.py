"""Deliver reports: Discord webhook and/or Telegram bot, or a local outbox file.

Reports go to the owner's channels first (Discord and/or TELEGRAM_CHAT_ID: the "primary"
delivery whose success makes the engine mark items seen), then best-effort to public Telegram
subscribers of that topic (see subscriptions.py). Subscriber problems never fail a run.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO
from urllib.parse import quote

from mybuddy.config import Settings, Topic
from mybuddy.feedback import feedback_markup
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Report
from mybuddy.store import Store, ensure_private_dir
from mybuddy.subscriptions import audience_map, mask_chat_id

DISCORD_LIMIT = 2000
TELEGRAM_LIMIT = 4096
TELEGRAM_API = "https://api.telegram.org"
PART_DELAY_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0
MAX_ATTEMPTS = 3
SUBSCRIBER_SEND_INTERVAL = 1 / 25  # stay under Telegram's ~30 messages/second bot limit
SUBSCRIBER_DEADLINE_SECONDS = 120.0  # per report: never hold up the scheduler for long
SUBSCRIBER_MAX_NETWORK_ERRORS = 5  # consecutive network/5xx errors -> give up on this report
TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")

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
    if report.summary:  # AI summaries are one line; the health check's has several
        lines += ["", *(escape_md(line) for line in report.summary.split("\n"))]
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
    lines = [f"{esc(emoji)} <b>{esc(report.title)}</b>"]
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


# --------------------------------------------------------------------------- subscribers


@dataclass
class FanoutStats:
    recipients: int = 0
    sent: int = 0
    failed: int = 0
    deactivated: int = 0
    skipped: int = 0  # not attempted (or incomplete) because the time/error budget ran out


class SubscriberSender(Protocol):
    def send(self, report: Report, emoji: str) -> FanoutStats: ...


def _owner_chat(settings: Settings) -> int | None:
    chat = settings.telegram_chat_id
    return int(chat) if chat and re.fullmatch(r"-?\d+", chat) else None


class _SubscriberBase:
    def __init__(
        self, store: Store, audience: Mapping[str, str], exclude: int | None = None
    ) -> None:
        self.store = store
        self.audience = dict(audience)
        self.exclude = exclude

    def recipients(self, topic_id: str) -> list[int]:
        """Active subscribers who get this topic's reports, without the owner, deduped."""
        target = self.audience.get(topic_id)
        if target is None:
            return []
        chats = dict.fromkeys(self.store.active_subscribers(target))
        if self.exclude is not None:
            chats.pop(self.exclude, None)
        return list(chats)


class TelegramSubscribers(_SubscriberBase):
    """Send a report to each subscriber, throttled; drop chats that blocked the bot."""

    def __init__(
        self,
        token: str,
        http: HttpClient,
        store: Store,
        clock: Clock,
        audience: Mapping[str, str],
        *,
        exclude: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        interval: float = SUBSCRIBER_SEND_INTERVAL,
        part_delay: float = PART_DELAY_SECONDS,
        deadline: float = SUBSCRIBER_DEADLINE_SECONDS,
        max_network_errors: int = SUBSCRIBER_MAX_NETWORK_ERRORS,
        monotonic: Callable[[], float] = time.monotonic,
        log: TextIO | None = None,
    ) -> None:
        if not TOKEN_RE.fullmatch(token):
            raise DeliveryError("TELEGRAM_BOT_TOKEN does not look like a bot token")
        super().__init__(store, audience, exclude)
        self.deadline = deadline
        self.max_network_errors = max_network_errors
        self.monotonic = monotonic
        self._url = telegram_url(token, "sendMessage")
        self.http = http
        self.clock = clock
        self.sleep = sleep
        self.interval = interval
        self.part_delay = part_delay
        self.log = log

    def _log(self, msg: str) -> None:
        print(f"[mybuddy] {msg}", file=self.log or sys.stderr, flush=True)

    def send(self, report: Report, emoji: str) -> FanoutStats:
        chats = self.recipients(report.topic_id)
        stats = FanoutStats(recipients=len(chats))
        if not chats:
            return stats
        parts = split_message(format_telegram(report, emoji), TELEGRAM_LIMIT)
        started = self.monotonic()
        pending = list(chats)
        sent_any = False
        network_errors = 0
        stop_reason = ""
        # Part 1 to everyone, then part 2 ...: keeps per-chat order and spaces each chat's parts.
        for n, part in enumerate(parts):
            if n:
                self.sleep(self.part_delay)
            still: list[int] = []
            for chat in pending:
                if network_errors >= self.max_network_errors:
                    stop_reason = f"{network_errors} network errors in a row"
                    break
                if sent_any:
                    self.sleep(self.interval)
                if self.monotonic() - started >= self.deadline:
                    stop_reason = f"time budget of {self.deadline:.0f}s used up"
                    break
                sent_any = True
                try:
                    markup = feedback_markup(report) if n == len(parts) - 1 else None
                    payload = _html_message(chat, part, markup)
                    post_telegram(self.http, self._url, payload, self.sleep)
                    network_errors = 0
                except HttpError as exc:
                    if exc.status is None or exc.status >= 500:
                        network_errors += 1
                    if chat_is_gone(exc):
                        if self.store.deactivate_subscriber(chat, self.clock.now()):
                            stats.deactivated += 1
                        self._log(f"subscriber {mask_chat_id(chat)} is gone; deactivated")
                    else:
                        stats.failed += 1
                        self._log(
                            f"subscriber {mask_chat_id(chat)}: HTTP {exc.status} "
                            f"{telegram_description(exc)}".rstrip()
                        )
                    continue
                except Exception as exc:  # never let one chat break the fan-out
                    stats.failed += 1
                    self._log(f"subscriber {mask_chat_id(chat)}: {type(exc).__name__}")
                    continue
                still.append(chat)
            if stop_reason:
                pending = still if n == len(parts) - 1 else []  # only complete deliveries count
                break
            pending = still
        stats.sent = len(pending)
        if stop_reason:
            stats.skipped = stats.recipients - stats.sent - stats.failed - stats.deactivated
            self._log(
                f"{report.topic_id}: stopped subscriber delivery ({stop_reason}); "
                f"{stats.skipped} skipped"
            )
        return stats


class OutboxSubscribers(_SubscriberBase):
    """Fake mode: write what subscribers would get to the outbox, clearly labelled."""

    def __init__(
        self,
        data_dir: Path,
        store: Store,
        clock: Clock,
        audience: Mapping[str, str],
        *,
        exclude: int | None = None,
    ) -> None:
        super().__init__(store, audience, exclude)
        self.dir = data_dir / "outbox"
        self.clock = clock
        self.last_path: Path | None = None

    def send(self, report: Report, emoji: str) -> FanoutStats:
        chats = self.recipients(report.topic_id)
        if not chats:
            return FanoutStats()
        ensure_private_dir(self.dir)
        stamp = self.clock.now().strftime("%Y%m%d-%H%M%S")
        path = self.dir / f"{report.topic_id}-{stamp}-subscribers.md"
        n = 1
        while path.exists():
            n += 1
            path = self.dir / f"{report.topic_id}-{stamp}-subscribers-{n}.md"
        header = (
            f"<!-- FAKE SUBSCRIBER DELIVERY: {len(chats)} Telegram subscriber(s) "
            f"({', '.join(mask_chat_id(c) for c in chats)}); nothing was sent -->"
        )
        parts = split_message(format_telegram(report, emoji), TELEGRAM_LIMIT)
        path.write_text(header + "\n\n" + "\n\n".join(parts) + "\n", encoding="utf-8")
        self.last_path = path
        return FanoutStats(recipients=len(chats), sent=len(chats))


class FanoutDeliverer:
    """Owner channels first (their failure fails the run, nothing goes to subscribers so a
    retry can't double-send), then best-effort subscribers (their failures are only logged)."""

    def __init__(
        self, primary: Deliverer, subscribers: SubscriberSender, *, log: TextIO | None = None
    ) -> None:
        self.primary = primary
        self.subscribers = subscribers
        self.log = log
        self.last_stats: FanoutStats | None = None

    def deliver(self, report: Report, emoji: str) -> None:
        self.primary.deliver(report, emoji)
        try:
            stats = self.subscribers.send(report, emoji)
        except Exception as exc:
            print(
                f"[mybuddy] {report.topic_id}: subscriber delivery crashed: {type(exc).__name__}",
                file=self.log or sys.stderr,
                flush=True,
            )
            return
        self.last_stats = stats
        if stats.recipients:
            print(
                f"[mybuddy] {report.topic_id}: subscribers: {stats.sent} sent, "
                f"{stats.failed} failed, {stats.deactivated} deactivated, "
                f"{stats.skipped} skipped",
                file=self.log or sys.stderr,
                flush=True,
            )


def _primary_deliverer(settings: Settings, http: HttpClient, clock: Clock) -> Deliverer:
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


def make_deliverer(
    settings: Settings,
    http: HttpClient,
    clock: Clock,
    *,
    store: Store | None = None,
    topics: Sequence[Topic] = (),
) -> Deliverer:
    """Owner channels; plus public Telegram subscribers when a store and topics are given."""
    primary = _primary_deliverer(settings, http, clock)
    if store is None or not topics:
        return primary
    audience = audience_map(topics)
    owner = _owner_chat(settings)
    subscribers: SubscriberSender
    if settings.fake:
        subscribers = OutboxSubscribers(settings.data_dir, store, clock, audience, exclude=owner)
    elif settings.telegram_bot_token and TOKEN_RE.fullmatch(settings.telegram_bot_token):
        subscribers = TelegramSubscribers(
            settings.telegram_bot_token, http, store, clock, audience, exclude=owner
        )
    else:
        return primary
    return FanoutDeliverer(primary, subscribers)
