"""mybuddy check: validate config, env and connectivity."""

from __future__ import annotations

import os
import stat
from collections.abc import Collection, Sequence
from pathlib import Path

from mybuddy.allowlist import build_allowed_hosts, topic_source_specs
from mybuddy.cli._common import new_http
from mybuddy.cli.telegram import _telegram_bot_name
from mybuddy.config import (
    SETTING_ENV_VARS,
    ConfigError,
    Settings,
    Topic,
    load_config,
    parse_max_subscribers,
)
from mybuddy.deliver import TOKEN_RE
from mybuddy.http import HttpError, url_host
from mybuddy.models import Clock
from mybuddy.store import Store
from mybuddy.subscriptions import mask_chat_id

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"


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

            http = new_http(allowed)
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

    if settings.groq_api_key and not settings.fake:
        print("connectivity:")
        ok, issue = groq_check(settings, allowed)
        if ok:
            print(f"  ok: {ok}")
        if issue:
            problems.append(issue)
    if settings.telegram_bot_token:
        ok, warning, issue = telegram_check(settings)
        if ok:
            print(f"  ok: {ok}")
        if warning:
            print(f"  warning: {warning}")
        if issue:
            problems.append(issue)

    if problems:
        print("problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("all good")
    return 0


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


def groq_check(settings: Settings, allowed: Collection[str]) -> tuple[str | None, str | None]:
    """Groq reachable and GROQ_MODEL offered? Returns (ok message, problem). Needs the key.

    The key travels only in the Authorization header; messages never contain it.
    """
    try:
        data = new_http(allowed).get_json(
            GROQ_MODELS_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"}
        )
        models = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
    except (HttpError, AttributeError, TypeError) as exc:
        return None, f"Groq check failed: {exc}"
    ok = f"Groq reachable ({len(models)} models)"
    if models and settings.groq_model not in models:
        return ok, f"GROQ_MODEL {settings.groq_model!r} not offered by Groq"
    return ok, None


def telegram_check(settings: Settings) -> tuple[str | None, str | None, str | None]:
    """Bot token valid (getMe)? Returns (ok, warning, problem); the token is never printed.

    Skipped (all None) when no token is set or in fake mode.
    """
    token = settings.telegram_bot_token
    if not token:
        return None, None, None
    if not TOKEN_RE.fullmatch(token):
        return None, None, "TELEGRAM_BOT_TOKEN does not look like a bot token"
    if settings.fake:
        return None, None, None
    bot_name = _telegram_bot_name(token)
    if not bot_name:
        return None, None, "Telegram check failed: token rejected or Telegram unreachable"
    warning = None
    if bot_name.lower() != settings.bot_username.lower():
        warning = (
            f"website links point to @{settings.bot_username}; set MYBUDDY_BOT_USERNAME to match"
        )
    return f"Telegram bot @{bot_name}", warning, None
