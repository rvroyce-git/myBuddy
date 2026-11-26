"""mybuddy telegram-setup, plus Telegram Bot API helpers for other commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mybuddy.allowlist import TELEGRAM_HOSTS
from mybuddy.cli._common import _err, new_http, write_private
from mybuddy.config import Settings
from mybuddy.deliver import TELEGRAM_API, TOKEN_RE
from mybuddy.http import HttpError
from mybuddy.subscriptions import mask_chat_id


def _telegram_get(token: str, method: str) -> Any:
    """Call a Telegram Bot API method. Errors are reported without the URL (it holds the token)."""
    if not TOKEN_RE.fullmatch(token):
        raise RuntimeError("TELEGRAM_BOT_TOKEN does not look like a bot token")
    try:
        return new_http(TELEGRAM_HOSTS).get_json(f"{TELEGRAM_API}/bot{token}/{method}")
    except HttpError as exc:
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.status}") from None


def _telegram_bot_name(token: str) -> str | None:
    try:
        data = _telegram_get(token, "getMe")
    except RuntimeError:
        return None
    result = data.get("result") if isinstance(data, dict) else None
    name = result.get("username") if isinstance(result, dict) else None
    return name if isinstance(name, str) and name else None


def cmd_telegram_setup(settings: Settings, env_file: Path, save: bool) -> int:
    token = settings.telegram_bot_token
    if not token:
        _err("TELEGRAM_BOT_TOKEN is not set (run deploy/set-secrets.sh on your laptop)")
        return 1
    try:
        data = _telegram_get(token, "getUpdates")
    except RuntimeError as exc:
        _err(str(exc))
        return 1
    # Chat ids are personal data: only masked ids are printed, never names.
    found: dict[str, None] = {}  # ordered set
    for update in data.get("result", []) if isinstance(data, dict) else []:
        msg = update.get("message") if isinstance(update, dict) else None
        chat = msg.get("chat") if isinstance(msg, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        # Only real integer ids: the value is written into the env file below.
        if (
            isinstance(chat, dict)
            and chat.get("type") == "private"
            and isinstance(chat_id, int)
            and not isinstance(chat_id, bool)
        ):
            found[str(chat_id)] = None
    chats = list(found)
    if not chats:
        _err("No messages found. Open your bot in Telegram, tap Start, send 'hi', then retry.")
        return 1
    for chat_id in chats:
        print(f"  private chat {mask_chat_id(chat_id)}")
    if len(chats) > 1:
        _err("More than one person messaged the bot; set TELEGRAM_CHAT_ID by hand.")
        return 1
    chat_id = chats[0]
    if not save:
        print(f"Run again with --save to store chat {mask_chat_id(chat_id)} as TELEGRAM_CHAT_ID")
        return 0
    path = env_file.expanduser()
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines = [ln for ln in lines if not _CHAT_ID_LINE.match(ln)]
        lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
        write_private(path, "\n".join(lines) + "\n")
    except (OSError, UnicodeDecodeError) as exc:
        _err(f"could not update {path}: {exc}")
        return 1
    print(f"saved TELEGRAM_CHAT_ID to {path}")
    return 0


_CHAT_ID_LINE = re.compile(r"\s*(?:export\s+)?TELEGRAM_CHAT_ID\s*=")
