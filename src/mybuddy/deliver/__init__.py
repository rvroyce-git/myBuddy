"""Deliver reports: Discord webhook and/or Telegram bot, or a local outbox file.

Reports go to the owner's channels first (Discord and/or TELEGRAM_CHAT_ID: the "primary"
delivery whose success makes the engine mark items seen), then best-effort to public Telegram
subscribers of that topic (see subscriptions.py). Subscriber problems never fail a run.

Modules: ``base`` (limits, errors, splitting, MultiDeliverer), ``format`` (Discord markdown and
Telegram HTML), ``discord``, ``telegram`` (owner chat + Bot API helpers), ``outbox`` (fake mode),
``fanout`` (subscribers) and ``factory`` (make_deliverer). Everything public is re-exported
here, so ``from mybuddy.deliver import ...`` keeps working.
"""

from __future__ import annotations

from mybuddy.deliver.base import (
    DISCORD_LIMIT,
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    PART_DELAY_SECONDS,
    TELEGRAM_LIMIT,
    Deliverer,
    DeliveryError,
    MultiDeliverer,
    split_message,
)
from mybuddy.deliver.discord import DiscordWebhook
from mybuddy.deliver.factory import make_deliverer
from mybuddy.deliver.fanout import (
    SUBSCRIBER_DEADLINE_SECONDS,
    SUBSCRIBER_MAX_NETWORK_ERRORS,
    SUBSCRIBER_SEND_INTERVAL,
    FanoutDeliverer,
    FanoutStats,
    OutboxSubscribers,
    SubscriberSender,
    TelegramSubscribers,
)
from mybuddy.deliver.format import escape_md, format_report, format_telegram, safe_link_url
from mybuddy.deliver.outbox import OutboxWriter
from mybuddy.deliver.telegram import (
    TELEGRAM_API,
    TOKEN_RE,
    TelegramBot,
    chat_is_gone,
    post_telegram,
    telegram_description,
    telegram_url,
)

__all__ = [
    "DISCORD_LIMIT",
    "MAX_ATTEMPTS",
    "MAX_RETRY_AFTER_SECONDS",
    "PART_DELAY_SECONDS",
    "SUBSCRIBER_DEADLINE_SECONDS",
    "SUBSCRIBER_MAX_NETWORK_ERRORS",
    "SUBSCRIBER_SEND_INTERVAL",
    "TELEGRAM_API",
    "TELEGRAM_LIMIT",
    "TOKEN_RE",
    "Deliverer",
    "DeliveryError",
    "DiscordWebhook",
    "FanoutDeliverer",
    "FanoutStats",
    "MultiDeliverer",
    "OutboxSubscribers",
    "OutboxWriter",
    "SubscriberSender",
    "TelegramBot",
    "TelegramSubscribers",
    "chat_is_gone",
    "escape_md",
    "format_report",
    "format_telegram",
    "make_deliverer",
    "post_telegram",
    "safe_link_url",
    "split_message",
    "telegram_description",
    "telegram_url",
]
