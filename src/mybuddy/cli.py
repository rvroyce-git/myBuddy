"""Command line: mybuddy list | run <topic|all> [--dry-run] | serve | check | export |
telegram-setup [--save] | bot | subscribers."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mybuddy import __version__
from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    SETTING_ENV_VARS,
    ConfigError,
    Settings,
    Topic,
    load_env_file,
    load_topics,
    parse_max_subscribers,
)
from mybuddy.deliver import TELEGRAM_API, TOKEN_RE, make_deliverer
from mybuddy.engine import Deps, RunResult, run_topic
from mybuddy.export import export, schedule_label
from mybuddy.http import HttpError, UrllibHttpClient
from mybuddy.models import Clock
from mybuddy.schedule import Scheduler, SystemClock, next_run
from mybuddy.store import Store
from mybuddy.subscriptions import full_mask, mask_chat_id, topic_bits, website_topics
from mybuddy.summarize import Summarizer

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
EX_CONFIG = 78  # "not configured": the systemd unit does not restart on this exit code


def _bot_http() -> UrllibHttpClient:
    from mybuddy.bot import HTTP_TIMEOUT_SECONDS

    return UrllibHttpClient(timeout=HTTP_TIMEOUT_SECONDS)


# Factories are module attributes so tests can swap in fakes.
make_http: Any = UrllibHttpClient
make_bot_http: Any = _bot_http  # long polling needs a longer timeout than the default 20 s
make_clock: Any = SystemClock


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mybuddy", description="Your personal AI news buddy.")
    p.add_argument("--version", action="version", version=f"mybuddy {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"topics file (default: $MYBUDDY_CONFIG or {DEFAULT_CONFIG_PATH})",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=f"KEY=VALUE env file (default: {DEFAULT_ENV_FILE}); real env vars win",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show topics and when they run next")
    run = sub.add_parser("run", help="run one topic now (or 'all')")
    run.add_argument("topic", help="topic id or 'all'")
    run.add_argument(
        "--dry-run", action="store_true", help="collect and print; no AI, no delivery, no state"
    )
    sub.add_parser("serve", help="run forever, each topic at its scheduled time (IST)")
    sub.add_parser("check", help="validate config, env and connectivity")
    sub.add_parser("export", help="rewrite the website reports.json from saved reports")
    tg = sub.add_parser(
        "telegram-setup", help="find your Telegram chat id (send your bot a message first)"
    )
    tg.add_argument("--save", action="store_true", help="write TELEGRAM_CHAT_ID to the env file")
    sub.add_parser("bot", help="run the Telegram bot that lets visitors subscribe to topics")
    sub.add_parser("subscribers", help="count public Telegram subscribers per topic")
    return p


def _config_path(args: argparse.Namespace) -> Path:
    if args.config:
        return Path(args.config)
    env = os.environ.get("MYBUDDY_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _build_deps(settings: Settings, topics: Sequence[Topic], clock: Clock) -> Deps:
    http = make_http()
    store = Store(settings.data_dir)
    return Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(settings, http, clock),
        deliverer=make_deliverer(settings, http, clock, store=store, topics=topics),
        topics=list(topics),
        export_path=settings.site_data,
    )


def cmd_list(topics: Sequence[Topic], clock: Clock) -> int:
    now = clock.now()
    for t in topics:
        nxt = next_run(t.schedule, now)
        n_sources = len(t.sources) or len(next(iter((t.rotation or {}).values())).sources)
        kind = "ai" if t.ai else "plain"
        print(
            f"{t.emoji} {t.id:<16} {t.title:<34} {schedule_label(t.schedule):<22} "
            f"{kind:<5} {n_sources} src  next {nxt:%a %d %b %H:%M}"
        )
    return 0


def cmd_run(
    target: str, topics: Sequence[Topic], settings: Settings, clock: Clock, dry_run: bool
) -> int:
    if target == "all":
        selected = list(topics)
    else:
        selected = [t for t in topics if t.id == target]
        if not selected:
            _err(f"unknown topic {target!r}; choose one of: {', '.join(t.id for t in topics)}")
            return 2
    deps = _build_deps(settings, topics, clock)
    results: list[RunResult] = []
    try:
        for topic in selected:
            try:
                results.append(run_topic(topic, deps, dry_run=dry_run))
            except Exception as exc:
                _err(f"[mybuddy] {topic.id}: crashed: {type(exc).__name__}: {exc}")
                results.append(RunResult(topic.id, "failed", errors=[str(exc)]))
    finally:
        deps.store.close()
    for r in results:
        print(f"{r.topic_id}: {r.status} (collected {r.collected}, new {r.new})", file=sys.stderr)
    return 0 if all(r.ok for r in results) else 1


def cmd_serve(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    deps = _build_deps(settings, topics, clock)
    scheduler = Scheduler(topics, lambda t: run_topic(t, deps), clock)
    scheduler.install_signal_handlers()
    print(f"[mybuddy] serving {len(topics)} topics (fake={settings.fake})", file=sys.stderr)
    try:
        scheduler.loop()
    finally:
        deps.store.close()
    return 0


def cmd_export(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    with Store(settings.data_dir) as store:
        export(topics, store, settings.site_data, clock.now())
    print(f"wrote {settings.site_data}")
    return 0


def cmd_check(config_path: Path, settings: Settings, clock: Clock) -> int:
    problems: list[str] = []
    print(f"config: {config_path}")
    topics: list[Topic] = []
    try:
        topics = load_topics(config_path)
        print(f"  ok: {len(topics)} topics ({', '.join(t.id for t in topics)})")
    except ConfigError as exc:
        problems.append(f"config: {exc}")
        print(f"  ERROR: {exc}")

    if topics:
        try:
            from mybuddy.sources import build_source

            http = make_http()
            for t in topics:
                specs = list(t.sources)
                for entry in (t.rotation or {}).values():
                    specs += entry.sources
                for spec in specs:
                    try:
                        build_source({**spec, "_state": {}}, http, clock)
                    except Exception as exc:
                        problems.append(f"{t.id}: source {spec.get('name')!r}: {exc}")
                        print(f"  ERROR: {t.id}: source {spec.get('name')!r}: {exc}")
        except ImportError as exc:
            problems.append(f"sources package unavailable: {exc}")
            print(f"  ERROR: sources package unavailable: {exc}")

    print("environment:")
    for name, secret in SETTING_ENV_VARS:
        value = os.environ.get(name, "")
        if not value:
            shown = "not set"
        elif secret:
            shown = "set"
        else:
            shown = f"set ({value})"
        print(f"  {name:<20} {shown}")
    print(f"  data dir: {settings.data_dir}")
    print(f"  site data: {settings.site_data}")
    if settings.fake:
        print("  fake mode: AI and Discord are simulated")
    else:
        if not settings.groq_api_key and not settings.gemini_api_key:
            print("  warning: no AI key set; reports will use the plain fallback")
        has_telegram = bool(settings.telegram_bot_token and settings.telegram_chat_id)
        if not settings.discord_webhook_url and not has_telegram:
            print("  warning: no Discord or Telegram set; reports go to the outbox folder")
        if settings.telegram_bot_token and not settings.telegram_chat_id:
            print("  warning: TELEGRAM_CHAT_ID not set; run 'mybuddy telegram-setup --save'")
    if settings.discord_webhook_url and not settings.discord_webhook_url.startswith("https://"):
        problems.append("DISCORD_WEBHOOK_URL must start with https://")

    try:
        with Store(settings.data_dir) as store:
            active = store.active_subscriber_count()
        print("  ok: data dir is writable")
        print(f"  subscribers: {active} active (limit {settings.max_subscribers})")
    except Exception as exc:
        problems.append(f"data dir {settings.data_dir}: {exc}")
    try:
        parse_max_subscribers(os.environ.get("MYBUDDY_MAX_SUBSCRIBERS"))
    except ConfigError as exc:
        print(f"  warning: {exc}; using {settings.max_subscribers}")

    if settings.groq_api_key and not settings.fake:
        print("connectivity:")
        try:
            data = make_http().get_json(
                GROQ_MODELS_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"}
            )
            models = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
            print(f"  ok: Groq reachable ({len(models)} models)")
            if models and settings.groq_model not in models:
                problems.append(f"GROQ_MODEL {settings.groq_model!r} not offered by Groq")
        except (HttpError, AttributeError, TypeError) as exc:
            problems.append(f"Groq check failed: {exc}")
    if settings.telegram_bot_token and not settings.fake:
        bot_name = _telegram_bot_name(settings.telegram_bot_token)
        if bot_name:
            print(f"  ok: Telegram bot @{bot_name}")
            if bot_name.lower() != settings.bot_username.lower():
                print(
                    f"  warning: website links point to @{settings.bot_username}; "
                    "set MYBUDDY_BOT_USERNAME to match"
                )
        else:
            problems.append("Telegram check failed: token rejected or Telegram unreachable")

    if problems:
        print("problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("all good")
    return 0


def _telegram_get(token: str, method: str) -> Any:
    """Call a Telegram Bot API method. Errors are reported without the URL (it holds the token)."""
    try:
        return make_http().get_json(f"{TELEGRAM_API}/bot{token}/{method}")
    except HttpError as exc:
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.status}") from None


def _telegram_bot_name(token: str) -> str | None:
    try:
        data = _telegram_get(token, "getMe")
    except RuntimeError:
        return None
    result = data.get("result") if isinstance(data, dict) else None
    return str(result.get("username")) if isinstance(result, dict) else None


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
        if isinstance(chat, dict) and chat.get("type") == "private" and "id" in chat:
            found[str(chat["id"])] = None
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
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.startswith("TELEGRAM_CHAT_ID=")]
    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(f"saved TELEGRAM_CHAT_ID to {path}")
    return 0


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
            make_bot_http(),
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


def cmd_subscribers(topics: Sequence[Topic], settings: Settings) -> int:
    """Counts only. Chat ids are personal data and are never printed."""
    with Store(settings.data_dir) as store:
        counts = store.subscriber_counts()
        active = store.active_subscriber_count()
    print(f"active subscribers: {active} (limit {settings.max_subscribers})")
    for t in website_topics(topics):
        extra = [f.id for f in topics if not f.ai and f.audience == t.id]
        note = f"  (+ {', '.join(extra)})" if extra else ""
        print(f"  {t.emoji} {t.id:<16} {counts.get(t.id, 0):>5}{note}")
    mask = full_mask(topic_bits(topics))
    if mask:
        print(f"start link (all topics): https://t.me/{settings.bot_username}?start=t{mask:x}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        load_env_file(args.env_file or DEFAULT_ENV_FILE)
    except ConfigError as exc:
        _err(f"env file: {exc}")
        return 2
    settings = Settings.from_env()
    clock = make_clock()
    config_path = _config_path(args)

    if args.command == "check":
        return cmd_check(config_path, settings, clock)
    if args.command == "telegram-setup":
        return cmd_telegram_setup(settings, args.env_file or DEFAULT_ENV_FILE, args.save)
    try:
        topics = load_topics(config_path)
    except ConfigError as exc:
        _err(f"config error: {exc}")
        return 2
    if args.command == "list":
        return cmd_list(topics, clock)
    if args.command == "run":
        return cmd_run(args.topic, topics, settings, clock, args.dry_run)
    if args.command == "serve":
        return cmd_serve(topics, settings, clock)
    if args.command == "export":
        return cmd_export(topics, settings, clock)
    if args.command == "bot":
        return cmd_bot(topics, settings, clock)
    if args.command == "subscribers":
        return cmd_subscribers(topics, settings)
    return 2  # pragma: no cover - argparse enforces choices


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
