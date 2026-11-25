"""The set of hosts MyBuddy may contact, built from the config and the settings.

Every outbound request and redirect hop is checked against it by UrllibHttpClient.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from mybuddy.config import Settings, Topic
from mybuddy.deliver import TELEGRAM_API
from mybuddy.http import host_allowlist, url_host
from mybuddy.summarize import GEMINI_URL, GROQ_URL

TELEGRAM_HOSTS = frozenset({url_host(TELEGRAM_API) or "api.telegram.org"})


def topic_source_specs(topics: Iterable[Topic]) -> Iterator[tuple[Topic, dict[str, Any]]]:
    """Every source entry of every topic, rotation entries included."""
    for topic in topics:
        for spec in topic.sources:
            yield topic, spec
        for entry in (topic.rotation or {}).values():
            for spec in entry.sources:
                yield topic, spec


def source_hosts(topics: Iterable[Topic]) -> set[str]:
    """Config-declared source URL hosts plus each used source type's fixed API hosts."""
    from mybuddy.sources import source_hosts as hosts_for_spec

    hosts: set[str] = set()
    for _, spec in topic_source_specs(topics):
        hosts |= hosts_for_spec(spec)
    return hosts


def provider_hosts(settings: Settings) -> set[str]:
    """Hosts of the AI and delivery services the settings turn on."""
    urls: list[str] = []
    if settings.groq_api_key:
        urls.append(GROQ_URL)
    if settings.gemini_api_key:
        urls.append(GEMINI_URL)
    if settings.telegram_bot_token:
        urls.append(TELEGRAM_API)
    if settings.discord_webhook_url:
        urls.append(settings.discord_webhook_url)
    return {h for h in map(url_host, urls) if h}


def build_allowed_hosts(
    topics: Sequence[Topic], settings: Settings, extra: Iterable[str] = ()
) -> frozenset[str]:
    """Source hosts + provider hosts + the config's `extra_allowed_hosts`.

    Hosts that are not plain names (e.g. an IP-address webhook URL) are left out, so a
    request to them is refused.
    """
    candidates = source_hosts(topics) | provider_hosts(settings) | set(extra)
    allowed: set[str] = set()
    for host in candidates:
        try:
            allowed |= host_allowlist([host])
        except ValueError:
            continue
    return frozenset(allowed)
