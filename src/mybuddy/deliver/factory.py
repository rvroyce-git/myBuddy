"""Build the deliverer for the configured channels."""

from __future__ import annotations

import re
from collections.abc import Sequence

from mybuddy.config import Settings, Topic
from mybuddy.deliver.base import Deliverer, MultiDeliverer
from mybuddy.deliver.discord import DiscordWebhook
from mybuddy.deliver.fanout import (
    FanoutDeliverer,
    OutboxSubscribers,
    SubscriberSender,
    TelegramSubscribers,
)
from mybuddy.deliver.outbox import OutboxWriter
from mybuddy.deliver.telegram import TOKEN_RE, TelegramBot
from mybuddy.models import Clock, HttpClient
from mybuddy.store import Store
from mybuddy.subscriptions import audience_map


def _owner_chat(settings: Settings) -> int | None:
    chat = settings.telegram_chat_id
    return int(chat) if chat and re.fullmatch(r"-?\d+", chat) else None


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
