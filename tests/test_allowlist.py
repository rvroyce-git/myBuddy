"""Host allowlist: every request and redirect hop must go to an allowed host."""

from __future__ import annotations

import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FakeHttp
from test_cli import CONFIG, env  # noqa: F401  (pytest fixture)
from test_engine_http import FakeResponse, client

from mybuddy import cli
from mybuddy.allowlist import build_allowed_hosts, topic_source_specs
from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    Settings,
    load_config,
    parse_config,
)
from mybuddy.http import HttpError, UrllibHttpClient, _HttpsOnlyRedirect, host_allowlist, url_host
from mybuddy.sources import SOURCE_HOSTS, SOURCE_TYPES

ALLOWED = frozenset({"feeds.example", "api.example"})


def _redirect(handler: _HttpsOnlyRedirect, req: urllib.request.Request, newurl: str) -> Any:
    return handler.redirect_request(req, None, 302, "Found", Message(), newurl)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- client


def test_allowed_host_passes() -> None:
    c, opener = client(FakeResponse(b"ok", "https://feeds.example/rss"), allowed_hosts=ALLOWED)
    assert c.get_bytes("https://feeds.example/rss") == b"ok"
    assert len(opener.requests) == 1


def test_disallowed_host_refused_without_network() -> None:
    c, opener = client(FakeResponse(b"x", "https://evil.example/"), allowed_hosts=ALLOWED)
    with pytest.raises(HttpError, match="host not allowed: evil.example"):
        c.get_bytes("https://evil.example/steal")
    with pytest.raises(HttpError, match="host not allowed"):
        c.post_json("https://sub.feeds.example/", {"a": 1})  # no implicit subdomains
    assert opener.requests == []


def test_redirect_to_disallowed_host_refused() -> None:
    handler = _HttpsOnlyRedirect(ALLOWED)
    req = urllib.request.Request("https://feeds.example/rss")
    with pytest.raises(HttpError, match="host not allowed: evil.example"):
        _redirect(handler, req, "https://evil.example/rss")
    assert _redirect(handler, req, "https://api.example/rss") is not None
    # The final URL is checked again, whatever followed the redirect.
    c, _ = client(FakeResponse(b"x", "https://evil.example/rss"), allowed_hosts=ALLOWED)
    with pytest.raises(HttpError, match="host not allowed"):
        c.get_bytes("https://feeds.example/rss")


def test_client_opener_uses_the_allowlist() -> None:
    c = UrllibHttpClient(allowed_hosts=["Feeds.Example."])
    handlers = [h for h in c._opener.handlers if isinstance(h, _HttpsOnlyRedirect)]
    assert [h.allowed_hosts for h in handlers] == [frozenset({"feeds.example"})]


def test_cross_host_redirect_still_strips_credentials() -> None:
    handler = _HttpsOnlyRedirect(ALLOWED)
    req = urllib.request.Request(
        "https://api.example/x",
        headers={"Authorization": "Bearer k", "Cookie": "s=1", "Accept": "a"},
    )
    other = _redirect(handler, req, "https://feeds.example/y")
    assert not other.has_header("Authorization") and not other.has_header("Cookie")
    assert other.get_header("Accept") == "a"
    same = _redirect(handler, req, "https://API.example./z")  # same host, other spelling
    assert same.get_header("Authorization") == "Bearer k"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/",
        "https://10.0.0.5/",
        "https://0x7f.1/",
    ],
)
def test_ip_literals_refused(url: str) -> None:
    c, opener = client(FakeResponse(b"", url), allowed_hosts=ALLOWED)
    with pytest.raises(HttpError, match="host not allowed"):
        c.get_bytes(url)
    with pytest.raises(HttpError, match="host not allowed"):
        _redirect(_HttpsOnlyRedirect(ALLOWED), urllib.request.Request("https://api.example/"), url)
    assert opener.requests == []


@pytest.mark.parametrize(
    "entry", ["127.0.0.1", "169.254.169.254", "[::1]", "::1", "10.1.2.3", "2130706433"]
)
def test_ip_literals_never_enter_the_allowlist(entry: str) -> None:
    with pytest.raises(ValueError, match="IP addresses"):
        host_allowlist([entry])
    with pytest.raises(ValueError):
        UrllibHttpClient(allowed_hosts=[entry])


@pytest.mark.parametrize("entry", ["*.example.com", "https://a.example", "a b", "", "a/b"])
def test_allowlist_entries_must_be_plain_host_names(entry: str) -> None:
    with pytest.raises(ValueError, match="plain host name"):
        host_allowlist([entry])


def test_case_and_trailing_dot_normalised() -> None:
    assert host_allowlist(["API.Example.", "feeds.example"]) == ALLOWED
    c, opener = client(FakeResponse(b"ok", "https://FEEDS.example./x"), allowed_hosts=ALLOWED)
    assert c.get_bytes("https://Feeds.Example./x") == b"ok"
    assert url_host("https://A.Example.:443/p") == "a.example"


# ----------------------------------------------------------------------------- building it


def test_every_source_type_registers_its_hosts() -> None:
    assert set(SOURCE_HOSTS) == set(SOURCE_TYPES)
    assert SOURCE_HOSTS["google_news"] == ("news.google.com",)
    assert SOURCE_HOSTS["hackernews"] == ("hacker-news.firebaseio.com",)
    assert SOURCE_HOSTS["cisa_kev"] == ("www.cisa.gov",)
    assert SOURCE_HOSTS["federal_register"] == ("www.federalregister.gov",)
    assert SOURCE_HOSTS["openfda_recalls"] == ("api.fda.gov",)
    assert SOURCE_HOSTS["cpsc_recalls"] == ("www.saferproducts.gov",)


def test_allowlist_from_real_config_covers_every_source_and_provider() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    settings = Settings(
        groq_api_key="k",
        gemini_api_key="k",
        telegram_bot_token="1:x",
        discord_webhook_url="https://discord.com/api/webhooks/1/secret",
    )
    allowed = build_allowed_hosts(config.topics, settings, config.extra_allowed_hosts)
    for _, spec in topic_source_specs(config.topics):
        assert set(SOURCE_HOSTS[spec["type"]]) <= allowed
        if "url" in spec:
            assert url_host(spec["url"]) in allowed
    assert config.extra_allowed_hosts <= allowed
    for host in (
        "api.groq.com",
        "generativelanguage.googleapis.com",
        "api.telegram.org",
        "discord.com",
        "openai.com",
        "news.google.com",
        "api.github.com",
        "export.arxiv.org",
        "huggingface.co",
    ):
        assert host in allowed
    # Unused source types and unconfigured providers stay out.
    assert "www.reddit.com" not in allowed
    bare = build_allowed_hosts(config.topics, Settings())
    assert not {"api.groq.com", "api.telegram.org", "discord.com"} & bare


def test_allowlist_drops_ip_literal_urls() -> None:
    raw = yaml.safe_load(yaml.safe_dump(CONFIG))
    raw["topics"][0]["sources"][0]["url"] = "https://169.254.169.254/rss"
    topics = parse_config(raw).topics
    webhook = Settings(discord_webhook_url="https://10.0.0.1/api/webhooks/1/x")
    allowed = build_allowed_hosts(topics, webhook)
    assert allowed == {"www.cisa.gov"}


def test_extra_allowed_hosts_in_config() -> None:
    raw = yaml.safe_load(yaml.safe_dump(CONFIG))
    raw["extra_allowed_hosts"] = ["Redirect.Example."]
    assert parse_config(raw).extra_allowed_hosts == {"redirect.example"}
    for bad in (["127.0.0.1"], ["*.example.com"], "cdn.example", [1]):
        raw["extra_allowed_hosts"] = bad
        with pytest.raises(ConfigError, match="extra_allowed_hosts"):
            parse_config(raw)


# ----------------------------------------------------------------------------- cli


def test_run_builds_the_client_with_the_config_allowlist(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    http = FakeHttp()
    monkeypatch.setattr(cli, "make_http", lambda hosts: seen.append(hosts) or http)
    assert cli.main([*env["argv"], "run", "all", "--dry-run"]) == 0
    assert seen == [frozenset({"feed.example", "www.cisa.gov"})]


def test_check_prints_allowlist_size(
    env: dict[str, Any],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([*env["argv"], "check"]) == 0
    assert "allowed hosts: 2 (feed.example, www.cisa.gov)" in capsys.readouterr().out


def test_check_flags_config_urls_whose_host_is_not_allowed(
    env: dict[str, Any],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = yaml.safe_load(yaml.safe_dump(CONFIG))
    raw["topics"][0]["sources"][0]["url"] = "https://127.0.0.1/rss"
    cfg = tmp_path / "ip.yaml"
    cfg.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://10.0.0.1/api/webhooks/1/secret")
    argv = ["--config", str(cfg), "--env-file", str(tmp_path / "no-env")]
    assert cli.main([*argv, "check"]) == 1
    out = capsys.readouterr().out
    assert "allowed hosts: 1 (www.cisa.gov)" in out
    assert "ai_news: source 'Feed': host not allowed: 127.0.0.1" in out
    assert "DISCORD_WEBHOOK_URL: host not allowed" in out and "secret" not in out
