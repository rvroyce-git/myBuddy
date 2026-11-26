"""Command line: mybuddy list | run <topic|all> [--dry-run] | serve | check | export |
telegram-setup [--save] | bot | subscribers | health."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from mybuddy import __version__
from mybuddy.allowlist import (
    TELEGRAM_HOSTS,
    build_allowed_hosts,
    provider_hosts,
    topic_source_specs,
)
from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    SETTING_ENV_VARS,
    Config,
    ConfigError,
    Settings,
    Topic,
    load_config,
    load_env_file,
    parse_chat_allowlist,
    parse_max_subscribers,
)
from mybuddy.configedit import RELOAD_MARKER, ConfigEditor, make_reloader
from mybuddy.deliver import TELEGRAM_API, TOKEN_RE, make_deliverer
from mybuddy.engine import Deps, RunResult, run_topic
from mybuddy.export import export, schedule_label
from mybuddy.http import HttpError, UrllibHttpClient, url_host
from mybuddy.models import Clock
from mybuddy.schedule import Scheduler, SystemClock, next_run
from mybuddy.store import Store
from mybuddy.subscriptions import full_mask, mask_chat_id, topic_bits, website_topics
from mybuddy.summarize import GEMINI_URL, GROQ_URL, Summarizer

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
EX_CONFIG = 78  # "not configured": the systemd unit does not restart on this exit code


def _http(allowed_hosts: Collection[str]) -> UrllibHttpClient:
    return UrllibHttpClient(allowed_hosts=allowed_hosts)


def _bot_http(allowed_hosts: Collection[str]) -> UrllibHttpClient:
    from mybuddy.bot import HTTP_TIMEOUT_SECONDS

    return UrllibHttpClient(allowed_hosts=allowed_hosts, timeout=HTTP_TIMEOUT_SECONDS)


def _chat_http(allowed_hosts: Collection[str]) -> UrllibHttpClient:
    from mybuddy.chat import TIMEOUT_SECONDS

    return UrllibHttpClient(allowed_hosts=allowed_hosts, timeout=TIMEOUT_SECONDS)


# Factories are module attributes so tests can swap in fakes. Each takes the host allowlist.
make_http: Any = _http
make_bot_http: Any = _bot_http  # long polling needs a longer timeout than the default 20 s
make_chat_http: Any = _chat_http  # bot chat: AI provider hosts only, short timeout
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
    sub.add_parser(
        "health", help="print the health check (last runs, failed sources, disk, memory)"
    )
    return p


def _config_path(args: argparse.Namespace) -> Path:
    if args.config:
        return Path(args.config)
    env = os.environ.get("MYBUDDY_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _build_deps(
    settings: Settings, topics: Sequence[Topic], clock: Clock, allowed_hosts: Collection[str]
) -> Deps:
    http = make_http(allowed_hosts)
    store = Store(settings.data_dir)
    return Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(settings, http, clock, store=store),
        deliverer=make_deliverer(settings, http, clock, store=store, topics=topics),
        topics=list(topics),
        export_path=settings.site_data,
        settings=settings,
    )


def cmd_list(topics: Sequence[Topic], clock: Clock) -> int:
    now = clock.now()
    for t in topics:
        nxt = next_run(t.schedule, now)
        rotation = list((t.rotation or {}).values())
        n_sources = len(t.sources) or (len(rotation[0].sources) if rotation else 0)
        kind = t.kind if t.kind != "news" else "ai" if t.ai else "plain"
        print(
            f"{t.emoji} {t.id:<16} {t.title:<34} {schedule_label(t.schedule):<22} "
            f"{kind:<6} {n_sources} src  next {nxt:%a %d %b %H:%M}"
        )
    return 0


def cmd_run(
    target: str,
    topics: Sequence[Topic],
    settings: Settings,
    clock: Clock,
    dry_run: bool,
    allowed_hosts: Collection[str],
) -> int:
    if target == "all":
        selected = list(topics)
    else:
        selected = [t for t in topics if t.id == target]
        if not selected:
            _err(f"unknown topic {target!r}; choose one of: {', '.join(t.id for t in topics)}")
            return 2
    deps = _build_deps(settings, topics, clock, allowed_hosts)
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


def cmd_serve(
    topics: Sequence[Topic],
    settings: Settings,
    clock: Clock,
    allowed_hosts: Collection[str],
    config_path: Path | None = None,
) -> int:
    state = {"deps": _build_deps(settings, topics, clock, allowed_hosts)}

    def on_reload(config: Config) -> None:
        # New sources may need new hosts: rebuild the HTTP client, deliverer and store.
        allowed = build_allowed_hosts(config.topics, settings, config.extra_allowed_hosts)
        old = state["deps"]
        state["deps"] = _build_deps(settings, config.topics, clock, allowed)
        old.store.close()

    reload = None
    if config_path is not None:
        marker = settings.data_dir / RELOAD_MARKER
        reload = make_reloader(config_path, marker, load_config, on_reload)
    scheduler = Scheduler(topics, lambda t: run_topic(t, state["deps"]), clock, reload=reload)
    scheduler.install_signal_handlers()
    print(f"[mybuddy] serving {len(topics)} topics (fake={settings.fake})", file=sys.stderr)
    try:
        scheduler.loop()
    finally:
        state["deps"].store.close()
    return 0


def cmd_export(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    with Store(settings.data_dir) as store:
        export(topics, store, settings.site_data, clock.now())
    print(f"wrote {settings.site_data}")
    return 0


def _env_file_problem(env_file: Path) -> str | None:
    """The env file holds secrets: it must not be readable by other users."""
    try:
        mode = env_file.expanduser().stat().st_mode
    except OSError:
        return None
    if stat.S_IMODE(mode) & 0o077:
        return f"{env_file} is readable by other users; run: chmod 600 {env_file}"
    return None


def cmd_check(config_path: Path, settings: Settings, clock: Clock, env_file: Path) -> int:
    problems: list[str] = []
    print(f"config: {config_path}")
    topics: list[Topic] = []
    extra_hosts: frozenset[str] = frozenset()
    try:
        config = load_config(config_path)
        topics, extra_hosts = config.topics, config.extra_allowed_hosts
        print(f"  ok: {len(topics)} topics ({', '.join(t.id for t in topics)})")
    except ConfigError as exc:
        problems.append(f"config: {exc}")
        print(f"  ERROR: {exc}")
    allowed = build_allowed_hosts(topics, settings, extra_hosts)
    print(f"  allowed hosts: {len(allowed)} ({', '.join(sorted(allowed))})")
    for problem in _disallowed_urls(topics, settings, allowed):
        problems.append(problem)
        print(f"  ERROR: {problem}")

    if topics:
        try:
            from mybuddy.sources import build_source

            http = make_http(allowed)
            for t, spec in topic_source_specs(topics):
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
        elif name == "TELEGRAM_CHAT_ID":  # personal data: masked like everywhere else
            shown = f"set ({mask_chat_id(value)})"
        elif name == "CHAT_ALLOWLIST":  # chat ids are personal data: count only
            shown = f"set ({len(parse_chat_allowlist(value)[0])} id(s))"
        else:
            shown = "set (" + "".join(c if c.isprintable() else "?" for c in value[:80]) + ")"
        print(f"  {name:<20} {shown}")
    env_problem = _env_file_problem(env_file)
    if env_problem:
        problems.append(env_problem)
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
    _print_access(settings)

    if settings.groq_api_key and not settings.fake:
        print("connectivity:")
        try:
            data = make_http(allowed).get_json(
                GROQ_MODELS_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"}
            )
            models = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
            print(f"  ok: Groq reachable ({len(models)} models)")
            if models and settings.groq_model not in models:
                problems.append(f"GROQ_MODEL {settings.groq_model!r} not offered by Groq")
        except (HttpError, AttributeError, TypeError) as exc:
            problems.append(f"Groq check failed: {exc}")
    if settings.telegram_bot_token and not TOKEN_RE.fullmatch(settings.telegram_bot_token):
        problems.append("TELEGRAM_BOT_TOKEN does not look like a bot token")
    elif settings.telegram_bot_token and not settings.fake:
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


def _print_access(settings: Settings) -> None:
    """Who may subscribe and chat, and the chat limits. Chat ids are never printed."""
    print("access:")
    mode = "public signup" if settings.public_signup else "private (owner + allowlist only)"
    print(f"  mode: {mode}")
    owner = 1 if settings.telegram_chat_id else 0
    print(
        f"  chat allowlist: owner{'' if owner else ' (not set)'} + {len(settings.chat_allowlist)}"
    )
    _, bad = parse_chat_allowlist(os.environ.get("CHAT_ALLOWLIST"))
    if bad:
        print(f"  warning: CHAT_ALLOWLIST has {bad} invalid entr{'y' if bad == 1 else 'ies'}")
    if settings.chat_active:
        print(
            f"  chat: on ({settings.chat_rate_per_min}/min and {settings.chat_daily_cap}/day "
            f"per chat, {settings.chat_daily_token_budget} tokens/day in total)"
        )
    elif not (settings.groq_api_key or settings.gemini_api_key):
        print("  chat: off (no AI key set)")
    else:
        print("  chat: off (CHAT_ENABLED=false)")
    for name in ("CHAT_RATE_PER_MIN", "CHAT_DAILY_CAP", "CHAT_DAILY_TOKEN_BUDGET"):
        raw = os.environ.get(name, "").strip()
        if raw and not re.fullmatch(r"[0-9]{1,9}", raw):
            print(f"  warning: {name} must be a whole number; using the default")


def _disallowed_urls(
    topics: Sequence[Topic], settings: Settings, allowed: Collection[str]
) -> list[str]:
    """Config and settings URLs whose host the HTTP client would refuse."""
    out: list[str] = []
    for t, spec in topic_source_specs(topics):
        url = spec.get("url")
        if isinstance(url, str) and url_host(url) not in allowed:
            out.append(f"{t.id}: source {spec.get('name')!r}: host not allowed: {url_host(url)}")
    webhook = settings.discord_webhook_url
    if webhook and url_host(webhook) not in allowed:
        out.append("DISCORD_WEBHOOK_URL: host not allowed (use a host name, not an IP)")
    return out


def _telegram_get(token: str, method: str) -> Any:
    """Call a Telegram Bot API method. Errors are reported without the URL (it holds the token)."""
    if not TOKEN_RE.fullmatch(token):
        raise RuntimeError("TELEGRAM_BOT_TOKEN does not look like a bot token")
    try:
        return make_http(TELEGRAM_HOSTS).get_json(f"{TELEGRAM_API}/bot{token}/{method}")
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
        _write_private(path, "\n".join(lines) + "\n")
    except (OSError, UnicodeDecodeError) as exc:
        _err(f"could not update {path}: {exc}")
        return 1
    print(f"saved TELEGRAM_CHAT_ID to {path}")
    return 0


_CHAT_ID_LINE = re.compile(r"\s*(?:export\s+)?TELEGRAM_CHAT_ID\s*=")


def _write_private(path: Path, text: str) -> None:
    """Atomically replace `path` with a 0600 file (it holds secrets; never briefly 0644)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
            chat = ChatResponder(settings, make_chat_http(hosts), store, topics, clock)
        bot = SubscriptionBot(
            token,
            make_bot_http(TELEGRAM_HOSTS),
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
        extra = [f.id for f in topics if not f.on_website and f.audience == t.id]
        note = f"  (+ {', '.join(extra)})" if extra else ""
        print(f"  {t.emoji} {t.id:<16} {counts.get(t.id, 0):>5}{note}")
    mask = full_mask(topic_bits(topics))
    if mask:
        print(f"start link (all topics): https://t.me/{settings.bot_username}?start=t{mask:x}")
    return 0


def cmd_health(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    """Print the health check. Exit 1 when something is stale or a source failed."""
    from mybuddy.reports import collect_health, health_report, health_text

    health_topic = next((t for t in topics if t.kind == "health"), None)
    title = health_topic.title if health_topic else "Health check"
    now = clock.now()
    with Store(settings.data_dir) as store:
        health = collect_health(topics, store, settings, now)
    print(health_text(health_report("health", title, health, now)), end="")
    return 1 if health.stale or health.failures else 0


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

    env_file = args.env_file or DEFAULT_ENV_FILE
    try:
        return _dispatch(args, settings, clock, config_path, env_file)
    except (OSError, sqlite3.Error) as exc:
        # e.g. an unwritable data dir or a locked/corrupt database: a clear message, exit 1.
        _err(f"[mybuddy] {args.command} failed: {type(exc).__name__}: {exc}")
        return 1


def _dispatch(
    args: argparse.Namespace, settings: Settings, clock: Clock, config_path: Path, env_file: Path
) -> int:
    if args.command == "check":
        return cmd_check(config_path, settings, clock, env_file)
    if args.command == "telegram-setup":
        return cmd_telegram_setup(settings, env_file, args.save)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        _err(f"config error: {exc}")
        return 2
    topics = config.topics
    allowed = build_allowed_hosts(topics, settings, config.extra_allowed_hosts)
    if args.command == "list":
        return cmd_list(topics, clock)
    if args.command == "run":
        return cmd_run(args.topic, topics, settings, clock, args.dry_run, allowed)
    if args.command == "serve":
        return cmd_serve(topics, settings, clock, allowed, config_path)
    if args.command == "export":
        return cmd_export(topics, settings, clock)
    if args.command == "bot":
        return cmd_bot(topics, settings, clock, config_path)
    if args.command == "subscribers":
        return cmd_subscribers(topics, settings)
    if args.command == "health":
        return cmd_health(topics, settings, clock)
    return 2  # pragma: no cover - argparse enforces choices


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
