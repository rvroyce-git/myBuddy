"""mybuddy bot: the Telegram bot (subscriptions, chat, owner config changes)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from mybuddy.allowlist import TELEGRAM_HOSTS, provider_hosts
from mybuddy.cli._common import EX_CONFIG, _err, new_bot_http, new_chat_http
from mybuddy.config import Settings, Topic
from mybuddy.configedit import RELOAD_MARKER, ConfigEditor
from mybuddy.deliver import TOKEN_RE
from mybuddy.http import url_host
from mybuddy.models import Clock
from mybuddy.store import Store
from mybuddy.summarize import GEMINI_URL, GROQ_URL


def _config_editor(
    config_path: Path | None, store: Store, clock: Clock, settings: Settings
) -> ConfigEditor | None:
    """Owner config changes need a config file the bot can write (MYBUDDY_CONFIG on the VM)."""
    if config_path is None:
        return None
    path = config_path.expanduser()
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        _err(f"[mybuddy-bot] {path} is not writable; config changes from chat are off")
        return None
    return ConfigEditor(path, store, clock, marker_path=settings.data_dir / RELOAD_MARKER)


def cmd_bot(
    topics: Sequence[Topic], settings: Settings, clock: Clock, config_path: Path | None = None
) -> int:
    from mybuddy.bot import SubscriptionBot

    token = settings.telegram_bot_token
    if not token or not TOKEN_RE.fullmatch(token):
        _err("TELEGRAM_BOT_TOKEN is missing or invalid; the subscription bot is not started")
        return EX_CONFIG
    if settings.fake:
        _err("MYBUDDY_FAKE is on; the subscription bot needs the real Telegram API")
        return EX_CONFIG
    with Store(settings.data_dir) as store:
        chat = None
        if settings.chat_active:
            from mybuddy.chat import ChatResponder

            # Only the AI provider hosts: chat can reach nothing else.
            hosts = frozenset(h for h in map(url_host, (GROQ_URL, GEMINI_URL)) if h)
            hosts &= frozenset(provider_hosts(settings))
            chat = ChatResponder(settings, new_chat_http(hosts), store, topics, clock)
        bot = SubscriptionBot(
            token,
            new_bot_http(TELEGRAM_HOSTS),
            store,
            topics,
            clock,
            owner_chat_id=settings.telegram_chat_id,
            max_subscribers=settings.max_subscribers,
            bot_username=settings.bot_username,
            public_signup=settings.public_signup,
            chat_allowlist=settings.chat_allowlist,
            chat=chat,
            editor=_config_editor(config_path, store, clock, settings),
            site_url=settings.site_url,
        )
        bot.install_signal_handlers()
        bot.run()
    return 0
