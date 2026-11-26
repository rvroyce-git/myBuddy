"""Telegram subscription bot: long-polls getUpdates so website visitors can pick topics.

Runs as its own small process (`mybuddy bot`, systemd unit `mybuddy-bot`). Commands:
/start [payload], /topics, /stop, /help. Only private chats are served. The only things
stored are the chat id and the chosen topic ids. Logs never contain message text or full
chat ids. The topic picker is stateless: each button's callback data carries the current
selection as a hex mask, so nothing is stored until the user taps Save.

Access (private by default): the owner (TELEGRAM_CHAT_ID) and CHAT_ALLOWLIST chats may
subscribe and chat. Other chats may subscribe only when PUBLIC_SIGNUP is on; otherwise they
get one polite fixed reply now and then (no AI call, no store write). Existing subscribers
keep their reports and can always /stop or change topics. Chat (plain text, /ask, /reset)
is answered by mybuddy.chat for the owner and allowlisted chats only.
"""

from __future__ import annotations

import re
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from types import FrameType
from typing import Any, TextIO

from mybuddy.chat import RESET, ChatResponder
from mybuddy.config import DEFAULT_BOT_USERNAME, DEFAULT_MAX_SUBSCRIBERS, Topic
from mybuddy.configedit import ConfigEditor
from mybuddy.deliver import TOKEN_RE, chat_is_gone, telegram_description, telegram_url
from mybuddy.feedback import CALLBACK_RE, weekly_summary
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient
from mybuddy.store import Store, report_chars
from mybuddy.subscriptions import (
    decode_payload,
    followers_of,
    mask_chat_id,
    mask_for,
    parse_hex_mask,
    topic_bits,
    topics_for_mask,
    website_topics,
)

POLL_TIMEOUT_SECONDS = 25
# The HTTP client used for long polling must wait longer than Telegram holds the request.
HTTP_TIMEOUT_SECONDS = POLL_TIMEOUT_SECONDS + 15.0
ALLOWED_UPDATES = ("message", "callback_query")
BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
FLOOD_LIMIT = 10  # commands per chat per window; more are ignored
CALLBACK_FLOOD_LIMIT = 40  # picker taps per chat per window (a picker needs ~10 taps)
FLOOD_WINDOW_SECONDS = 60.0
FLOOD_TRACKED_CHATS = 1000
CALLBACK_MAX_BYTES = 64
DENY_REPLY_INTERVAL_SECONDS = 600.0  # a chat that isn't allowed hears "private" at most this often

_TOGGLE_RE = re.compile(r"tog:([0-9a-f]{1,15}):([a-z][a-z0-9_]{0,39})")
_SAVE_RE = re.compile(r"save:([0-9a-f]{1,15})")
_COMMAND_RE = re.compile(r"/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]{1,64}))?")

WELCOME = (
    "Hi! I'm MyBuddy 👋\n"
    "I send short news reports, every story with its source link.\n"
    "Tap the topics you want, then tap Save."
)
PICKER = "Tap topics to switch them on or off, then tap Save."
HELP = (
    "MyBuddy sends short news reports with source links.\n\n"
    "/topics – choose your topics\n"
    "/stop – unsubscribe\n"
    "/help – show this message"
)
UNKNOWN = (
    "I only understand a few commands:\n"
    "/topics – choose your topics\n"
    "/stop – unsubscribe\n"
    "/help – help"
)
FULL = "Sorry, MyBuddy is full right now and can't take new subscribers. Please try again later."
STOPPED = "You're unsubscribed. No more reports from me.\nSend /start any time to come back."
NOT_SUBSCRIBED = "You're not subscribed. Send /topics to pick some topics."
OWNER_NOTE = "You're the owner, so you still get every report."
EXPIRED = "That button has expired. Send /topics to try again."
PICK_ONE = "Pick at least one topic first (or send /stop)."
SAVED = "Saved ✅"
SLOW_DOWN = "Too many taps – please wait a moment."
PRIVATE = (
    "Thanks for stopping by! MyBuddy is private for now and isn't taking new subscribers. "
    "The daily reports are on the website: https://rvroyce-git.github.io/myBuddy/"
)
PRIVATE_TAP = "MyBuddy is private for now."
CHAT_HELP = "\n\nAsk me about the latest reports: just send a question, or /ask <question>."
CHAT_UNAVAILABLE = "Chat isn't available here. Send /help to see what I can do."
OWNER_ONLY = "That command is for the owner only."
CONFIG_OFF = "Config changes from chat aren't set up on this server."
NOTE_SAVED = "Note saved. It shows in /feedback and is never sent to the AI."
THANKS = "Thanks for the feedback!"
ALREADY_VOTED = "Already counted. Thanks!"
OWNER_HELP = "\n\nOwner: /config, /set (config changes), /changes, /feedback, /note <text>"
TELEGRAM_TEXT_MAX = 4000
OWNER_COMMANDS = frozenset(
    {
        "config",
        "set",
        "topic",
        "time",
        "source",
        "limit",
        "approve",
        "reject",
        "changes",
        "undo",
        "feedback",
        "note",
    }
)


def parse_command(text: str, bot_username: str = DEFAULT_BOT_USERNAME) -> tuple[str | None, str]:
    """('/start@bot t3' -> ('start', 't3')). Commands for other bots or plain text -> None."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None, ""
    match = _COMMAND_RE.fullmatch(parts[0])
    if not match:
        return None, ""
    target = match.group(2)
    if target and target.lower() != bot_username.lower():
        return None, ""
    return match.group(1).lower(), parts[1].strip() if len(parts) > 1 else ""


def _int_id(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _private_chat_id(chat: Any) -> int | None:
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    return _int_id(chat.get("id"))


def _from_bot(sender: Any) -> bool:
    return isinstance(sender, dict) and sender.get("is_bot") is True


class SubscriptionBot:
    def __init__(
        self,
        token: str,
        http: HttpClient,
        store: Store,
        topics: Sequence[Topic],
        clock: Clock,
        *,
        owner_chat_id: str | None = None,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
        bot_username: str = DEFAULT_BOT_USERNAME,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], object] | None = None,
        stop: threading.Event | None = None,
        log: TextIO | None = None,
        poll_timeout: int = POLL_TIMEOUT_SECONDS,
        public_signup: bool = False,
        chat_allowlist: Sequence[int] = (),
        chat: ChatResponder | None = None,
        editor: ConfigEditor | None = None,
    ) -> None:
        if not TOKEN_RE.fullmatch(token):
            raise ValueError("TELEGRAM_BOT_TOKEN does not look like a bot token")
        self._token = token
        self.http = http
        self.store = store
        self.clock = clock
        self.all_topics = list(topics)
        self.bits = topic_bits(topics)  # permanent payload bits (contract v2)
        self.topics = [t for t in website_topics(topics) if t.id in self.bits]
        self.order = [t.id for t in self.topics]
        owner = (owner_chat_id or "").strip()
        self.owner = int(owner) if re.fullmatch(r"-?\d+", owner) else None
        self.max_subscribers = max_subscribers
        self.bot_username = bot_username
        self.monotonic = monotonic
        self.stop = stop or threading.Event()
        self.wait: Callable[[float], object] = wait or self.stop.wait
        self.log = log
        self.poll_timeout = poll_timeout
        self.offset = store.load_bot_offset()
        self._flood: dict[int, deque[float]] = {}
        self._callback_flood: dict[int, deque[float]] = {}
        self.public_signup = public_signup
        self.allowed: frozenset[int] = frozenset(chat_allowlist) | (
            {self.owner} if self.owner is not None else set()
        )
        self.chat = chat
        self.editor = editor
        self._denied: dict[int, float] = {}  # chat -> when it last got the PRIVATE reply

    # ------------------------------------------------------------------ plumbing
    def _log(self, msg: str) -> None:
        print(f"[mybuddy-bot] {msg}", file=self.log or sys.stderr, flush=True)

    def _api(self, method: str, payload: dict[str, Any]) -> Any:
        return self.http.post_json(telegram_url(self._token, method), payload)

    def _call(self, method: str, payload: dict[str, Any], chat_id: int) -> bool:
        """Best-effort API call for a reply. Errors are logged (masked), never raised."""
        try:
            self._api(method, payload)
            return True
        except HttpError as exc:
            if method == "sendMessage" and chat_is_gone(exc):
                self.store.deactivate_subscriber(chat_id, self.clock.now())
            desc = telegram_description(exc)
            self._log(f"{method} for chat {mask_chat_id(chat_id)} failed: HTTP {exc.status} {desc}")
            return False

    def _send(self, chat_id: int, text: str, markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if markup is not None:
            payload["reply_markup"] = markup
        self._call("sendMessage", payload, chat_id)

    def _answer(self, callback_id: str, chat_id: int, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload, chat_id)

    def _allow(self, chat_id: int, *, callback: bool = False) -> bool:
        """Per-chat flood guard: messages and button taps have separate budgets per window."""
        book = self._callback_flood if callback else self._flood
        limit = CALLBACK_FLOOD_LIMIT if callback else FLOOD_LIMIT
        now = self.monotonic()
        stamps = book.setdefault(chat_id, deque())
        while stamps and now - stamps[0] >= FLOOD_WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) >= limit:
            return False
        stamps.append(now)
        if len(book) > FLOOD_TRACKED_CHATS:
            for cid in [c for c, s in book.items() if now - s[-1] >= FLOOD_WINDOW_SECONDS]:
                del book[cid]
        return True

    # ------------------------------------------------------------------ access
    def _is_subscriber(self, chat_id: int) -> bool:
        sub = self.store.get_subscriber(chat_id)
        return sub is not None and sub.active

    def _may_subscribe(self, chat_id: int) -> bool:
        """New subscriptions need PUBLIC_SIGNUP or the allowlist; existing ones always stay."""
        return self.public_signup or chat_id in self.allowed or self._is_subscriber(chat_id)

    def _deny(self, chat_id: int, callback_id: str | None = None) -> None:
        """Polite fixed reply, at most once per interval per chat. No AI call, no store write."""
        if callback_id is not None:  # always stop the button spinner
            self._answer(callback_id, chat_id, PRIVATE_TAP)
            self._log(f"chat {mask_chat_id(chat_id)}: not allowed (private mode), button")
            return
        now = self.monotonic()
        last = self._denied.get(chat_id)
        if last is not None and now - last < DENY_REPLY_INTERVAL_SECONDS:
            self._log(f"chat {mask_chat_id(chat_id)}: not allowed (private mode), no reply")
            return
        self._denied[chat_id] = now
        if len(self._denied) > FLOOD_TRACKED_CHATS:
            for cid in [
                c for c, t in self._denied.items() if now - t >= DENY_REPLY_INTERVAL_SECONDS
            ]:
                del self._denied[cid]
        self._log(f"chat {mask_chat_id(chat_id)}: not allowed (private mode), replied")
        self._send(chat_id, PRIVATE)

    def _chat(self, chat_id: int, question: str) -> None:
        if self.chat is None or chat_id not in self.allowed:
            self._send(chat_id, CHAT_UNAVAILABLE)
            return
        self._send(chat_id, self.chat.answer(chat_id, question))

    # ------------------------------------------------------------------ owner tools
    def _owner_command(self, chat_id: int, command: str, arg: str) -> None:
        """Config changes, feedback and notes: the owner only, never allowlisted chats."""
        if self.owner is None or chat_id != self.owner:
            self._log(f"chat {mask_chat_id(chat_id)}: owner-only /{command} refused")
            self._send(chat_id, OWNER_ONLY)
            return
        now = self.clock.now()
        if command == "feedback":
            self._send(chat_id, weekly_summary(self.store, self.all_topics, now))
            return
        if command == "note":
            note = " ".join(arg.split())
            if not note:
                self._send(chat_id, "Usage: /note <text> (stored for /feedback, never sent to AI)")
                return
            self.store.add_note(note, now)
            self._log(f"owner note stored ({len(note)} chars)")
            self._send(chat_id, NOTE_SAVED)
            return
        if self.editor is None:
            self._send(chat_id, CONFIG_OFF)
            return
        words = arg.split()
        if command == "config":
            reply = self.editor.show(self._access_lines())
        elif command == "approve":
            reply = self.editor.approve(arg)
        elif command == "reject":
            reply = self.editor.reject(arg)
        elif command == "changes":
            reply = self.editor.changes()
        else:  # set, topic, time, source, limit, undo
            reply = self.editor.request(command, words)
        self._send(chat_id, reply[:TELEGRAM_TEXT_MAX])

    def _access_lines(self) -> list[str]:
        lines = [
            f"Signup: {'public' if self.public_signup else 'private'}; "
            f"{len(self.allowed)} allowed chat(s)",
        ]
        if self.chat is not None:
            limits = self.chat.limits()
            lines.append(
                f"Chat: on, {limits['chat_rate_per_min']}/min, {limits['chat_daily_cap']}/day "
                f"per chat, {limits['chat_daily_token_budget']} tokens/day"
            )
        else:
            lines.append("Chat: off")
        return lines

    def _vote(self, callback_id: str, chat_id: int, vote: str, topic_id: str, ref: str) -> None:
        """👍/👎 from the owner, allowlisted chats or subscribers; one vote per chat+report."""
        if chat_id not in self.allowed and not self._is_subscriber(chat_id):
            self._answer(callback_id, chat_id, PRIVATE_TAP)
            return
        report = self.store.report_by_ref(topic_id, ref)
        if report is None:
            self._answer(callback_id, chat_id, EXPIRED)
            return
        changed = self.store.record_vote(
            topic_id,
            ref,
            self.store.voter_id(chat_id),
            1 if vote == "u" else -1,
            report_chars(report),
            self.clock.now(),
        )
        self._log(f"chat {mask_chat_id(chat_id)}: feedback on {topic_id}")
        self._answer(callback_id, chat_id, THANKS if changed else ALREADY_VOTED)

    def _help(self, chat_id: int) -> str:
        text = HELP
        if self.chat is not None and chat_id in self.allowed:
            text += CHAT_HELP
        if self.owner is not None and chat_id == self.owner:
            text += OWNER_HELP
        return text

    # ------------------------------------------------------------------ views
    def picker_markup(self, mask: int) -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = []
        for t in self.topics:
            mark = "✅" if mask & (1 << self.bits[t.id]) else "⬜"
            rows.append(
                [{"text": f"{mark} {t.emoji} {t.title}", "callback_data": f"tog:{mask:x}:{t.id}"}]
            )
        rows.append([{"text": "💾 Save", "callback_data": f"save:{mask:x}"}])
        return {"inline_keyboard": rows}

    def _confirmation(self, chat_id: int, topic_ids: Sequence[str]) -> str:
        lines = ["You're subscribed to:"]
        for t in self.topics:
            if t.id in topic_ids:
                lines.append(f"• {t.emoji} {t.title}")
                lines += [f"  + {f.emoji} {f.title}" for f in followers_of(t.id, self.all_topics)]
        if chat_id == self.owner:
            lines += ["", OWNER_NOTE]
        lines += ["", "/topics to change, /stop to unsubscribe."]
        return "\n".join(lines)

    def _current_mask(self, chat_id: int) -> int:
        sub = self.store.get_subscriber(chat_id)
        if sub is None or not sub.active:
            return 0
        return mask_for([t for t in sub.topics if t in self.bits], self.bits)

    # ------------------------------------------------------------------ actions
    def _subscribe(self, chat_id: int, topic_ids: Sequence[str]) -> bool:
        current = self.store.get_subscriber(chat_id)
        already = current is not None and current.active
        if not already and not self._may_subscribe(chat_id):
            self._deny(chat_id)
            return False
        if (
            not already
            and chat_id != self.owner
            and self.store.active_subscriber_count(exclude=self.owner) >= self.max_subscribers
        ):
            self._log(f"chat {mask_chat_id(chat_id)} turned away: subscriber limit reached")
            self._send(chat_id, FULL)
            return False
        self.store.subscribe(chat_id, topic_ids, self.clock.now())
        self._log(f"chat {mask_chat_id(chat_id)} subscribed to {len(topic_ids)} topic(s)")
        self._send(chat_id, self._confirmation(chat_id, topic_ids))
        return True

    def _show_picker(self, chat_id: int, intro: str) -> None:
        self._send(chat_id, intro, self.picker_markup(self._current_mask(chat_id)))

    def _stop(self, chat_id: int) -> None:
        # Always available to everyone (never trap a subscriber); no write for non-subscribers.
        if self._is_subscriber(chat_id) and self.store.deactivate_subscriber(
            chat_id, self.clock.now()
        ):
            self._log(f"chat {mask_chat_id(chat_id)} unsubscribed")
            text = STOPPED
        else:
            text = NOT_SUBSCRIBED
        if chat_id == self.owner:
            text += "\n" + OWNER_NOTE
        self._send(chat_id, text)

    # ------------------------------------------------------------------ updates
    def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("callback_query"), dict):
            self._on_callback(update["callback_query"])
        elif isinstance(update.get("message"), dict):
            self._on_message(update["message"])

    def _on_message(self, msg: dict[str, Any]) -> None:
        chat_id = _private_chat_id(msg.get("chat"))
        if chat_id is None or _from_bot(msg.get("from")):
            return
        if not self._allow(chat_id):
            self._log(f"chat {mask_chat_id(chat_id)}: flood guard, message ignored")
            return
        raw = msg.get("text")
        text = raw.strip() if isinstance(raw, str) else ""
        command, arg = parse_command(text, self.bot_username)
        if command == "stop":
            self._stop(chat_id)
            return
        if not self._may_subscribe(chat_id):
            self._deny(chat_id)
            return
        can_chat = self.chat is not None and chat_id in self.allowed
        if command in OWNER_COMMANDS:
            self._owner_command(chat_id, command, arg)
        elif command is None and text and not text.startswith("/") and can_chat:
            self._chat(chat_id, text)
        elif command == "ask":
            self._chat(chat_id, arg)
        elif command == "reset":
            self._send(chat_id, RESET if can_chat else UNKNOWN)
        elif command == "start":
            topic_ids = decode_payload(arg, self.bits) if arg else None
            if topic_ids:
                self._subscribe(chat_id, topic_ids)
            else:
                self._show_picker(chat_id, WELCOME)
        elif command == "topics":
            self._show_picker(chat_id, PICKER)
        elif command == "help":
            self._send(chat_id, self._help(chat_id))
        else:
            self._send(chat_id, UNKNOWN)

    def _on_callback(self, cq: dict[str, Any]) -> None:
        callback_id = cq.get("id")
        msg = cq.get("message")
        if not isinstance(callback_id, str) or not isinstance(msg, dict):
            return
        chat_id = _private_chat_id(msg.get("chat"))
        if chat_id is None or _from_bot(cq.get("from")):
            self._answer(callback_id, 0)  # always stop the button spinner; nothing else
            return
        if not self._allow(chat_id, callback=True):
            self._log(f"chat {mask_chat_id(chat_id)}: flood guard, button ignored")
            self._answer(callback_id, chat_id, SLOW_DOWN)
            return
        data = cq.get("data")
        vote = CALLBACK_RE.fullmatch(data) if isinstance(data, str) else None
        if vote:
            self._vote(callback_id, chat_id, vote.group(1), vote.group(2), vote.group(3))
            return
        if not self._may_subscribe(chat_id):
            self._deny(chat_id, callback_id)
            return
        message_id = _int_id(msg.get("message_id"))
        if not isinstance(data, str) or len(data.encode("utf-8")) > CALLBACK_MAX_BYTES:
            self._answer(callback_id, chat_id, EXPIRED)
            return
        toggle = _TOGGLE_RE.fullmatch(data)
        save = _SAVE_RE.fullmatch(data)
        if toggle and message_id is not None:
            mask = parse_hex_mask(toggle.group(1), self.bits)
            topic_id = toggle.group(2)
            if mask is None or topic_id not in self.bits:
                self._answer(callback_id, chat_id, EXPIRED)
                return
            mask ^= 1 << self.bits[topic_id]
            self._call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": self.picker_markup(mask),
                },
                chat_id,
            )
            self._answer(callback_id, chat_id)
        elif save:
            mask = parse_hex_mask(save.group(1), self.bits)
            topic_ids = topics_for_mask(mask, self.bits) if mask is not None else None
            if topic_ids is None:
                self._answer(callback_id, chat_id, EXPIRED)
                return
            if not topic_ids:
                self._answer(callback_id, chat_id, PICK_ONE)
                return
            if message_id is not None:  # drop the picker so old buttons can't be reused
                self._call(
                    "editMessageReplyMarkup",
                    {"chat_id": chat_id, "message_id": message_id},
                    chat_id,
                )
            ok = self._subscribe(chat_id, topic_ids)
            self._answer(callback_id, chat_id, SAVED if ok else "")
        else:
            self._answer(callback_id, chat_id, EXPIRED)

    # ------------------------------------------------------------------ loop
    def poll_once(self) -> int:
        """One long poll; handle the batch; persist the offset. Returns updates handled."""
        data = self._api(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": self.poll_timeout,
                "allowed_updates": list(ALLOWED_UPDATES),
            },
        )
        result = data.get("result") if isinstance(data, dict) and data.get("ok") else None
        if not isinstance(result, list):
            raise ValueError("unexpected getUpdates response")
        updates = [
            u for u in result if isinstance(u, dict) and _int_id(u.get("update_id")) is not None
        ]
        for update in updates:
            try:
                self.handle_update(update)
            except Exception as exc:  # one bad update must never stop the bot
                self._log(f"update {update['update_id']} failed: {type(exc).__name__}")
        if updates:
            self.offset = max(int(u["update_id"]) for u in updates) + 1
            self.store.save_bot_offset(self.offset)
        return len(updates)

    def run(self, max_polls: int | None = None) -> int:
        """Poll until stopped. Network/API errors back off 1 s -> 30 s. Returns polls made."""
        self._log(
            f"started: {len(self.topics)} topics, limit {self.max_subscribers} subscribers, "
            f"{self.store.active_subscriber_count()} active; "
            f"{'public' if self.public_signup else 'private'} signup, "
            f"{len(self.allowed)} allowed chat(s), chat {'on' if self.chat else 'off'}"
        )
        delay = BACKOFF_START_SECONDS
        polls = 0
        while not self.stop.is_set():
            try:
                self.poll_once()
                delay = BACKOFF_START_SECONDS
            except Exception as exc:
                self._log(f"getUpdates failed ({_describe(exc)}); retrying in {delay:.0f}s")
                self.wait(delay)
                delay = min(delay * 2, BACKOFF_MAX_SECONDS)
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
        self._log("stopped")
        return polls

    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: FrameType | None) -> None:
            self._log(f"received signal {signum}; stopping after the current poll")
            self.stop.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)


def _describe(exc: Exception) -> str:
    """Error summary without the URL (it contains the bot token)."""
    if isinstance(exc, HttpError):
        if exc.status is None:
            return "network error"
        hint = " - is another bot instance or a webhook active?" if exc.status == 409 else ""
        return f"HTTP {exc.status} {telegram_description(exc)}".rstrip() + hint
    return type(exc).__name__
