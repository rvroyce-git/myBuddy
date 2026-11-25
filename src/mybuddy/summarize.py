"""Turn collected items into a Report using an OpenAI-compatible LLM (Groq, then Gemini).

Links are never taken from model output: the model only refers to items by index, and every
URL in the report comes from the collected Item.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from mybuddy.config import PROMPTS_DIR, Settings, Topic
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Item, Report, ReportItem

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MAX_AI_ITEMS = 12
SNIPPET_CHARS = 280
TEMPERATURE = 0.3
# Reasoning models (e.g. gpt-oss) spend part of this budget thinking before the JSON answer.
MAX_TOKENS = 2000
RETRY_429_MAX_SECONDS = 8.0

# Short by design: the website widgets and Discord posts stay small and scannable.
HEADLINE_MAX = 110
SUMMARY_MAX = 220
SUMMARY_SENTENCES = 2
BLURB_MAX = 120
REPORT_ITEMS_MAX = 6

_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
# Feed boilerplate that adds length but no meaning.
_SNIPPET_NOISE_RE = re.compile(
    r"\\?\[(?:\.\.\.|…)\\?\]|\s*(?:…|\.\.\.)\s*$"
    r"|The post .+? appeared first on .+?\.|\b(?:Read more|Continue reading)\b.*$",
    re.IGNORECASE,
)


class SummaryError(Exception):
    """The model call failed or returned unusable output."""


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    api_key: str = field(repr=False)
    model: str


def providers_for(settings: Settings) -> list[Provider]:
    """Groq first, then Gemini, each only when its key is set."""
    out = []
    if settings.groq_api_key:
        out.append(Provider("groq", GROQ_URL, settings.groq_api_key, settings.groq_model))
    if settings.gemini_api_key:
        out.append(Provider("gemini", GEMINI_URL, settings.gemini_api_key, settings.gemini_model))
    return out


def clean_text(text: str, limit: int) -> str:
    """Collapse whitespace, strip any URLs the model slipped in, and cap length."""
    text = _URL_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
        text = (cut or text[: limit - 1]) + "…"
    return text


def first_sentences(text: str, count: int) -> str:
    """Keep only the first ``count`` sentences of ``text``."""
    parts = _SENTENCE_END_RE.split(_WS_RE.sub(" ", text).strip())
    return " ".join(parts[:count])


def _truncate(text: str, limit: int) -> str:
    text = _WS_RE.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _source_names(items: Sequence[Item], limit: int = 3) -> str:
    names: list[str] = []
    for item in items:
        if item.source and item.source not in names:
            names.append(item.source)
    if len(names) > limit:
        return ", ".join(names[:limit]) + " and more"
    return ", ".join(names) or "your sources"


def _report_item(item: Item, blurb: str) -> ReportItem:
    return ReportItem(
        title=_truncate(item.title, 200) or item.url,
        url=item.url,
        source=item.source,
        blurb=blurb,
        published=item.published,
    )


def snippet_blurb(item: Item) -> str:
    """A short blurb from a feed snippet: boilerplate removed, first sentence, capped."""
    if not item.snippet:
        return ""
    text = _SNIPPET_NOISE_RE.sub("", item.snippet).strip()
    return clean_text(first_sentences(text, 1), BLURB_MAX)


def plain_report(topic_id: str, title: str, items: Sequence[Item], clock: Clock) -> Report:
    """A no-AI report (used for ai: false topics such as kev_alerts)."""
    n = len(items)
    return Report(
        topic_id=topic_id,
        title=title,
        created_at=clock.now(),
        headline=f"{n} new {'item' if n == 1 else 'items'}: {title}",
        summary="",
        items=tuple(_report_item(i, snippet_blurb(i)) for i in items),
    )


def fallback_report(topic_id: str, title: str, items: Sequence[Item], clock: Clock) -> Report:
    """Used when every AI provider failed: items with their own snippets."""
    return Report(
        topic_id=topic_id,
        title=title,
        created_at=clock.now(),
        headline=f"Today's top items from {_source_names(items)}",
        summary="AI summary unavailable today; here are the top items as published.",
        items=tuple(_report_item(i, snippet_blurb(i)) for i in items[:REPORT_ITEMS_MAX]),
    )


def parse_model_json(content: str) -> dict[str, Any]:
    """Parse and validate {"headline", "summary", "blurbs": [{"index", "text"}]}."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SummaryError(f"model returned invalid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SummaryError("model JSON is not an object")
    headline = data.get("headline")
    summary = data.get("summary", "")
    blurbs = data.get("blurbs", [])
    if not isinstance(headline, str) or not headline.strip():
        raise SummaryError("model JSON has no headline")
    if not isinstance(summary, str):
        raise SummaryError("model JSON summary is not a string")
    if not isinstance(blurbs, list):
        raise SummaryError("model JSON blurbs is not a list")
    return {"headline": headline, "summary": summary, "blurbs": blurbs}


class Summarizer:
    def __init__(
        self,
        settings: Settings,
        http: HttpClient,
        clock: Clock,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        sleep: Callable[[float], None] = time.sleep,
        log: TextIO | None = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.clock = clock
        self.prompts_dir = prompts_dir
        self.sleep = sleep
        self.log = log

    def _log(self, msg: str) -> None:
        print(f"[mybuddy] {msg}", file=self.log or sys.stderr)

    def providers(self) -> list[Provider]:
        return providers_for(self.settings)

    # ---- prompt
    def build_messages(
        self, topic: Topic, title: str, items: Sequence[Item]
    ) -> list[dict[str, str]]:
        rules = (self.prompts_dir / "_rules.md").read_text(encoding="utf-8").strip()
        focus = ""
        if topic.prompt:
            focus = (self.prompts_dir / topic.prompt).read_text(encoding="utf-8").strip()
        system = f"{rules}\n\n{focus}".strip()
        lines = [
            f"Topic: {title}",
            f"There are {len(items)} items, numbered 1 to {len(items)}.",
            "",
        ]
        for i, item in enumerate(items, start=1):
            lines.append(f"[{i}] {_truncate(item.title, 200)}")
            lines.append(f"    Source: {item.source}")
            if item.snippet:
                lines.append(f"    Snippet: {_truncate(item.snippet, SNIPPET_CHARS)}")
            for key in ("stars", "stars_gained"):
                if key in item.extra:
                    lines.append(f"    {key.replace('_', ' ').title()}: {item.extra[key]}")
        lines += [
            "",
            "Reply with only a JSON object of this exact shape:",
            '{"headline": "one sentence, max 14 words", '
            '"summary": "1-2 short sentences, max 35 words in total", '
            '"blurbs": [{"index": 1, "text": "max 16 words about item 1"}]}',
            f"Include at most {REPORT_ITEMS_MAX} blurbs, most important first.",
        ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(lines)},
        ]

    # ---- model call
    def _call(self, provider: Provider, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        for attempt in range(2):
            try:
                resp = self.http.post_json(provider.url, payload, headers=headers)
                break
            except HttpError as exc:
                if exc.status == 429 and attempt == 0:
                    wait = min(exc.retry_after or 2.0, RETRY_429_MAX_SECONDS)
                    self._log(f"{provider.name} rate limited; retrying in {wait:.0f}s")
                    self.sleep(wait)
                    continue
                # Provider error bodies are logged; make sure one can never echo the key.
                body = exc.body.replace(provider.api_key, "<redacted>") if exc.body else ""
                detail = f" ({_WS_RE.sub(' ', body).strip()[:200]})" if body.strip() else ""
                raise SummaryError(f"{provider.name}: {exc}{detail}") from None
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise SummaryError(f"{provider.name}: unexpected response shape") from None
        if not isinstance(content, str):
            raise SummaryError(f"{provider.name}: empty response")
        return parse_model_json(content)

    def _build(
        self, topic_id: str, title: str, items: Sequence[Item], data: dict[str, Any]
    ) -> Report:
        blurbs: dict[int, str] = {}
        order: list[int] = []
        for entry in data["blurbs"]:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            text = entry.get("text")
            if isinstance(idx, str) and idx.strip().isdigit():
                idx = int(idx.strip())
            if not isinstance(idx, int) or isinstance(idx, bool) or not 1 <= idx <= len(items):
                continue
            if not isinstance(text, str) or idx in blurbs:
                continue
            blurbs[idx] = clean_text(text, BLURB_MAX)
            order.append(idx)
        if order:
            chosen = [
                _report_item(items[i - 1], blurbs[i] or snippet_blurb(items[i - 1])) for i in order
            ]
        else:
            chosen = [_report_item(i, snippet_blurb(i)) for i in items]
        chosen = chosen[:REPORT_ITEMS_MAX]
        headline = clean_text(data["headline"], HEADLINE_MAX)
        if not headline:
            raise SummaryError("headline empty after cleaning")
        return Report(
            topic_id=topic_id,
            title=title,
            created_at=self.clock.now(),
            headline=headline,
            summary=clean_text(first_sentences(data["summary"], SUMMARY_SENTENCES), SUMMARY_MAX),
            items=tuple(chosen),
        )

    def fake_report(self, topic_id: str, title: str, items: Sequence[Item]) -> Report:
        """Deterministic stand-in used in fake mode and dry runs. No network."""
        n = len(items)
        return Report(
            topic_id=topic_id,
            title=title,
            created_at=self.clock.now(),
            headline=f"(fake) {n} new {'item' if n == 1 else 'items'} for {title}",
            summary=f"Fake summary of {n} items from {_source_names(items)}. No AI was called.",
            items=tuple(
                _report_item(i, snippet_blurb(i) or f"Item {k} from {i.source}.")
                for k, i in enumerate(items[:REPORT_ITEMS_MAX], start=1)
            ),
        )

    def summarize(
        self,
        topic: Topic,
        items: Sequence[Item],
        *,
        title: str | None = None,
        fake: bool | None = None,
    ) -> Report:
        title = title or topic.title
        items = list(items)[:MAX_AI_ITEMS]
        if self.settings.fake if fake is None else fake:
            return self.fake_report(topic.id, title, items)
        providers = self.providers()
        if not providers:
            self._log(f"{topic.id}: no AI key set; using plain fallback")
            return fallback_report(topic.id, title, items, self.clock)
        try:
            messages = self.build_messages(topic, title, items)
        except (OSError, UnicodeDecodeError) as exc:
            self._log(f"{topic.id}: cannot read prompt files ({exc}); using plain fallback")
            return fallback_report(topic.id, title, items, self.clock)
        for provider in providers:
            try:
                return self._build(topic.id, title, items, self._call(provider, messages))
            except Exception as exc:  # any provider problem -> try the next one
                self._log(f"{topic.id}: summary via {provider.name} failed: {exc}")
        return fallback_report(topic.id, title, items, self.clock)
