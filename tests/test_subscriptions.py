"""Subscription contract: topic order, start-link payloads, audience mapping and settings."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    Settings,
    load_topics,
    parse_max_subscribers,
    parse_topics,
)
from mybuddy.subscriptions import (
    audience_map,
    decode_payload,
    encode_payload,
    followers_of,
    mask_chat_id,
    parse_hex_mask,
    topic_bits,
    topic_order,
)

ORDER = [
    "ai_news",
    "security",
    "github_trending",
    "deep_dive",
    "research_papers",
    "startups",
    "ai_models",
]
BITS = {topic_id: bit for bit, topic_id in enumerate(ORDER)}  # contract v2 bits


def test_shipped_bits_match_contract() -> None:
    topics = load_topics(DEFAULT_CONFIG_PATH)
    assert topic_order(topics) == ORDER
    assert topic_bits(topics) == BITS
    assert audience_map(topics)["kev_alerts"] == "security"
    assert {t: a for t, a in audience_map(topics).items() if t != "kev_alerts"} == {
        t: t for t in ORDER
    }
    assert [t.id for t in followers_of("security", topics)] == ["kev_alerts"]


@pytest.mark.parametrize(
    ("topics", "payload"),
    [
        (ORDER, "t7f"),
        (["ai_news", "security"], "t3"),
        (["ai_news"], "t1"),
        (["ai_models"], "t40"),
        (["security", "startups"], "t22"),
    ],
)
def test_encode_and_decode(topics: list[str], payload: str) -> None:
    assert encode_payload(topics, BITS) == payload
    assert decode_payload(payload, BITS) == [t for t in ORDER if t in topics]


def test_decode_accepts_leading_zeros() -> None:
    assert decode_payload("t03", BITS) == ["ai_news", "security"]


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "t",
        "t0",  # empty mask
        "t000",
        "T3",  # wrong prefix case
        "x3",  # wrong prefix
        "3",
        "t3F",  # uppercase hex
        "tg",
        "t-1",
        "t3 ",
        "t3\n",
        " t3",
        "t80",  # bit 7: no topic owns it
        "tff",
        "t" + "1" * 15,  # valid shape, unknown bits
        "t" + "f" * 16,  # too long
        "t0x3",
    ],
)
def test_decode_rejects_invalid(payload: str) -> None:
    assert decode_payload(payload, BITS) is None


def test_encode_rejects_empty_and_unknown() -> None:
    with pytest.raises(ValueError):
        encode_payload([], BITS)
    with pytest.raises(ValueError):
        encode_payload(["kev_alerts"], BITS)


def test_parse_hex_mask() -> None:
    assert parse_hex_mask("0", BITS) == 0
    assert parse_hex_mask("7f", BITS) == 0x7F
    assert parse_hex_mask("80", BITS) is None
    assert parse_hex_mask("7F", BITS) is None
    assert parse_hex_mask("", BITS) is None


def _shipped_raw() -> dict[str, Any]:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _bits_of(raw: dict[str, Any]) -> dict[str, int]:
    return topic_bits(parse_topics(raw, source_types=None))


def test_reordering_topics_never_changes_a_payload() -> None:
    raw = _shipped_raw()
    original = _bits_of(raw)
    for shuffled in (list(reversed(raw["topics"])), raw["topics"][3:] + raw["topics"][:3]):
        bits = _bits_of({"topics": shuffled})
        assert bits == original  # same topic -> same bit, whatever the order
        assert decode_payload("t3", bits) is not None
        assert set(decode_payload("t3", bits) or []) == {"ai_news", "security"}
        assert set(decode_payload("t40", bits) or []) == {"ai_models"}
        assert encode_payload(["security", "startups"], bits) == "t22"


def test_removing_a_topic_never_shifts_other_payloads() -> None:
    raw = _shipped_raw()
    kept = [t for t in raw["topics"] if t["id"] != "security"]
    for t in kept:
        t.pop("audience", None)  # kev_alerts pointed at the removed topic
    bits = _bits_of({"topics": kept})
    assert "security" not in bits
    assert decode_payload("t1", bits) == ["ai_news"]
    assert decode_payload("t40", bits) == ["ai_models"]  # still AI models, not shifted
    assert decode_payload("t4", bits) == ["github_trending"]
    assert decode_payload("t3", bits) is None  # bit 1 is no longer owned -> picker
    assert decode_payload("t2", bits) is None
    assert encode_payload(["ai_models"], bits) == "t40"


def test_bit_validation() -> None:
    assert _parse(_topic(bit=59))[0].bit == 59
    with pytest.raises(ConfigError, match="need a permanent 'bit'"):
        _parse({k: v for k, v in _topic().items() if k != "bit"})
    for bad in (-1, 60, "1", True, 1.0, None):
        with pytest.raises(ConfigError, match="bit: must be an integer between 0 and 59"):
            _parse(_topic(bit=bad))
    with pytest.raises(ConfigError, match="already used by 'ai_news'"):
        _parse(_topic(), _topic(id="other", bit=0))
    with pytest.raises(ConfigError, match="only AI"):
        _parse(_topic(), _kev(bit=3))


def test_mask_chat_id_hides_most_digits() -> None:
    assert mask_chat_id(123456789) == "…789"
    assert mask_chat_id(-1001234) == "…234"
    assert mask_chat_id(12) == "…"


def _topic(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ai_news",
        "title": "AI news",
        "emoji": "🧠",
        "description": "d",
        "schedule": {"time": "06:30", "days": "daily"},
        "ai": True,
        "bit": 0,
        "prompt": "ai_news.md",
        "sources": [{"type": "rss", "name": "A", "url": "https://a.example/feed"}],
    }
    base.update(kw)
    return base


def _kev(**kw: Any) -> dict[str, Any]:
    kev = _topic(
        id="kev_alerts",
        ai=False,
        schedule={"every_minutes": 60},
        sources=[{"type": "cisa_kev", "name": "KEV"}],
    )
    del kev["bit"]
    kev.update(kw)
    return kev


def _parse(*topics: dict[str, Any]) -> Any:
    return parse_topics({"topics": list(topics)}, source_types={"rss", "cisa_kev"})


def test_audience_config_validation() -> None:
    topics = _parse(_topic(), _kev(audience="ai_news"))
    assert topics[1].audience == "ai_news"
    assert audience_map(topics) == {"ai_news": "ai_news", "kev_alerts": "ai_news"}
    assert audience_map(_parse(_topic(), _kev())) == {"ai_news": "ai_news"}  # owner only
    with pytest.raises(ConfigError, match="must be the id of an AI topic"):
        _parse(_topic(), _kev(audience="nope"))
    with pytest.raises(ConfigError, match="must be the id of an AI topic"):
        _parse(_topic(), _kev(audience="kev_alerts"))
    with pytest.raises(ConfigError, match="only non-AI topics"):
        _parse(_topic(audience="ai_news"))
    with pytest.raises(ConfigError, match="non-empty string"):
        _parse(_topic(), _kev(audience=3))


def test_subscriber_settings_from_env() -> None:
    s = Settings.from_env({})
    assert s.max_subscribers == 100 and s.bot_username == "myBuddy_update_bot"
    s = Settings.from_env({"MYBUDDY_MAX_SUBSCRIBERS": "5", "MYBUDDY_BOT_USERNAME": "@other_bot"})
    assert s.max_subscribers == 5 and s.bot_username == "other_bot"
    s = Settings.from_env({"MYBUDDY_MAX_SUBSCRIBERS": "lots", "MYBUDDY_BOT_USERNAME": "x y"})
    assert s.max_subscribers == 100 and s.bot_username == "myBuddy_update_bot"


@pytest.mark.parametrize("raw", ["-1", "abc", "1.5", "10001", "1e3"])
def test_parse_max_subscribers_rejects(raw: str) -> None:
    with pytest.raises(ConfigError, match="MYBUDDY_MAX_SUBSCRIBERS"):
        parse_max_subscribers(raw)


def test_parse_max_subscribers_accepts() -> None:
    assert parse_max_subscribers(None) == 100
    assert parse_max_subscribers(" ") == 100
    assert parse_max_subscribers("0") == 0
    assert parse_max_subscribers(" 250 ") == 250
