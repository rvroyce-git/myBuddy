"""Command line: mybuddy list | run <topic|all> [--dry-run] | serve | check | export |
telegram-setup [--save] | bot | subscribers | health | init | doctor.

One module per command group: run (list/run/serve/export), check, doctor, init, health,
telegram (telegram-setup), bot and subscribers. This package wires the parser and keeps the
swappable factories.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from mybuddy import __version__
from mybuddy.allowlist import TELEGRAM_HOSTS, build_allowed_hosts
from mybuddy.cli._common import EX_CONFIG, _config_path, _err
from mybuddy.cli.bot import _config_editor, cmd_bot
from mybuddy.cli.check import GROQ_MODELS_URL, cmd_check
from mybuddy.cli.doctor import cmd_doctor
from mybuddy.cli.health import cmd_health
from mybuddy.cli.init import LIVE_CONFIG_PATH, cmd_init
from mybuddy.cli.run import cmd_export, cmd_list, cmd_run, cmd_serve
from mybuddy.cli.subscribers import cmd_subscribers
from mybuddy.cli.telegram import cmd_telegram_setup
from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    SETTING_ENV_VARS,
    ConfigError,
    Settings,
    load_config,
    load_env_file,
)
from mybuddy.http import UrllibHttpClient
from mybuddy.models import Clock
from mybuddy.schedule import SystemClock

__all__ = [
    "_config_editor",
    "EX_CONFIG",
    "GROQ_MODELS_URL",
    "SETTING_ENV_VARS",
    "TELEGRAM_HOSTS",
    "cmd_bot",
    "cmd_check",
    "cmd_doctor",
    "cmd_init",
    "cmd_export",
    "cmd_health",
    "cmd_list",
    "cmd_run",
    "cmd_serve",
    "cmd_subscribers",
    "cmd_telegram_setup",
    "main",
    "make_bot_http",
    "make_chat_http",
    "make_clock",
    "make_http",
]


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
    init = sub.add_parser(
        "init", help="guided setup: shows the plan and writes nothing until you type yes"
    )
    init.add_argument("--answers", type=Path, help="YAML answers file (topics, delivery, schedule)")
    init.add_argument("--yes", action="store_true", help="with --answers: write without asking")
    init.add_argument("--force", action="store_true", help="with --yes: overwrite the output")
    init.add_argument(
        "--from",
        dest="base",
        type=Path,
        default=None,
        help=f"topics to start from (default: the output file if it exists, else "
        f"{DEFAULT_CONFIG_PATH})",
    )
    init.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"topics file to write (default: $MYBUDDY_CONFIG or {LIVE_CONFIG_PATH})",
    )
    sub.add_parser(
        "doctor", help="check config, secrets (names only), files, data, services: PASS/WARN/FAIL"
    )
    return p


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
    if args.command == "doctor":
        return cmd_doctor(config_path, settings, clock, env_file)
    if args.command == "init":
        return cmd_init(
            env_file,
            settings,
            base_path=args.base,
            output=args.output,
            answers_file=args.answers,
            assume_yes=args.yes,
            force=args.force,
        )
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
