"""mybuddy bot: run the Telegram subscription bot."""

from __future__ import annotations

from collections.abc import Sequence

from mybuddy.allowlist import TELEGRAM_HOSTS
from mybuddy.cli._common import EX_CONFIG, _err, new_bot_http
from mybuddy.config import Settings, Topic
from mybuddy.deliver import TOKEN_RE
from mybuddy.models import Clock
from mybuddy.store import Store


def cmd_bot(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    from mybuddy.bot import SubscriptionBot

    token = settings.telegram_bot_token
    if not token or not TOKEN_RE.fullmatch(token):
        _err("TELEGRAM_BOT_TOKEN is missing or invalid; the subscription bot is not started")
        return EX_CONFIG
    if settings.fake:
        _err("MYBUDDY_FAKE is on; the subscription bot needs the real Telegram API")
        return EX_CONFIG
    with Store(settings.data_dir) as store:
        bot = SubscriptionBot(
            token,
            new_bot_http(TELEGRAM_HOSTS),
            store,
            topics,
            clock,
            owner_chat_id=settings.telegram_chat_id,
            max_subscribers=settings.max_subscribers,
            bot_username=settings.bot_username,
        )
        bot.install_signal_handlers()
        bot.run()
    return 0
