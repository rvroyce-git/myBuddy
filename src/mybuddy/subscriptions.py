"""Pure subscription logic shared by the website contract, the Telegram bot and delivery.

* Website topics = `ai: true` topics unless they set `website: false` (Topic.on_website;
  export.py filters the same way). Each has a
  permanent `bit:` in topics.yaml (contract v2), so payloads survive reordering and removal.
* Start-link payload = "t" + lowercase hex bitmask; bit n set for each chosen topic's `bit`.
  Today (ai_news 0 ... ai_models 6): all -> "t7f", ai_news + security -> "t3".
  Bits that no current topic owns make the payload invalid (the bot shows the picker).
* Audience: a website topic's reports go to its own subscribers; any other topic with
  `audience: <website topic id>` in topics.yaml (kev_alerts -> security) goes to that topic's
  subscribers; any other topic (e.g. the health check) goes to the owner only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from mybuddy.config import Topic

PAYLOAD_PREFIX = "t"
MAX_MASK_HEX_DIGITS = 15
_PAYLOAD_RE = re.compile(r"t([0-9a-f]{1,15})")
_HEX_RE = re.compile(r"[0-9a-f]{1,15}")

TopicBits = Mapping[str, int]  # website topic id -> permanent bit, in config (display) order


def website_topics(topics: Sequence[Topic]) -> list[Topic]:
    return [t for t in topics if t.on_website]


def topic_order(topics: Sequence[Topic]) -> list[str]:
    """Website topic ids in config (display) order."""
    return [t.id for t in website_topics(topics)]


def topic_bits(topics: Sequence[Topic]) -> dict[str, int]:
    """Website topic id -> its permanent bit (config validates every AI topic has one)."""
    return {t.id: t.bit for t in website_topics(topics) if t.bit is not None}


def mask_for(topic_ids: Iterable[str], bits: TopicBits) -> int:
    """Bitmask for these topic ids. Raises ValueError for an id without a bit."""
    mask = 0
    for topic_id in topic_ids:
        if topic_id not in bits:
            raise ValueError(f"unknown topic id {topic_id!r}")
        mask |= 1 << bits[topic_id]
    return mask


def full_mask(bits: TopicBits) -> int:
    return mask_for(bits, bits)


def topics_for_mask(mask: int, bits: TopicBits) -> list[str] | None:
    """Topic ids (in display order) for a mask, or None when it sets a bit no topic owns."""
    if mask < 0 or mask & ~full_mask(bits):
        return None
    return [topic_id for topic_id, bit in bits.items() if mask & (1 << bit)]


def parse_hex_mask(text: str, bits: TopicBits) -> int | None:
    """Strict lowercase hex mask (zero allowed); None when malformed or has unknown bits."""
    if not _HEX_RE.fullmatch(text):
        return None
    mask = int(text, 16)
    return mask if topics_for_mask(mask, bits) is not None else None


def encode_payload(topic_ids: Iterable[str], bits: TopicBits) -> str:
    mask = mask_for(topic_ids, bits)
    if not mask:
        raise ValueError("choose at least one topic")
    return f"{PAYLOAD_PREFIX}{mask:x}"


def decode_payload(payload: str, bits: TopicBits) -> list[str] | None:
    """Topic ids for a /start payload, or None when it is invalid, empty or has unknown bits."""
    match = _PAYLOAD_RE.fullmatch(payload)
    if not match:
        return None
    topics = topics_for_mask(int(match.group(1), 16), bits)
    return topics or None


def audience_map(topics: Sequence[Topic]) -> dict[str, str]:
    """Report topic id -> the subscription topic id whose subscribers receive it."""
    out = {t.id: t.id for t in topics if t.on_website}
    for t in topics:
        if not t.on_website and t.audience and t.audience in out:
            out[t.id] = t.audience
    return out


def followers_of(topic_id: str, topics: Sequence[Topic]) -> list[Topic]:
    """Non-website topics delivered along with `topic_id` (e.g. kev_alerts with security)."""
    return [t for t in topics if not t.on_website and t.audience == topic_id]


def mask_chat_id(chat_id: int | str) -> str:
    """Log-safe chat id: only the last 3 digits ("…789")."""
    text = str(chat_id)
    return "…" + text[-3:] if len(text) > 3 else "…"
