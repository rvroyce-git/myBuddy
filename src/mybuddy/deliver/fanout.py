"""Owner first, then best-effort delivery to public Telegram subscribers.

Subscriber problems never fail a run (see subscriptions.py).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from mybuddy.deliver.base import (
    PART_DELAY_SECONDS,
    TELEGRAM_LIMIT,
    Deliverer,
    DeliveryError,
    split_message,
)
from mybuddy.deliver.format import format_telegram
from mybuddy.deliver.telegram import (
    TOKEN_RE,
    _html_message,
    chat_is_gone,
    post_telegram,
    telegram_description,
    telegram_url,
)
from mybuddy.feedback import feedback_markup
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Report
from mybuddy.store import Store, ensure_private_dir
from mybuddy.subscriptions import mask_chat_id

SUBSCRIBER_SEND_INTERVAL = 1 / 25  # stay under Telegram's ~30 messages/second bot limit
SUBSCRIBER_DEADLINE_SECONDS = 120.0  # per report: never hold up the scheduler for long
SUBSCRIBER_MAX_NETWORK_ERRORS = 5  # consecutive network/5xx errors -> give up on this report


@dataclass
class FanoutStats:
    recipients: int = 0
    sent: int = 0
    failed: int = 0
    deactivated: int = 0
    skipped: int = 0  # not attempted (or incomplete) because the time/error budget ran out


class SubscriberSender(Protocol):
    def send(self, report: Report, emoji: str) -> FanoutStats: ...


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
