"""Hugging Face daily papers and trending models (public JSON APIs)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    parse_iso,
    spec_int,
    spec_str,
)

DAILY_PAPERS_API = "https://huggingface.co/api/daily_papers"
MODELS_API = "https://huggingface.co/api/models"
_PAPER_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$")


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class HfPapersSource:
    """Today's Hugging Face curated papers: newest day first, then most upvoted."""

    def __init__(self, name: str, http: HttpClient, *, limit: int = 10) -> None:
        self.name = name
        self.limit = limit
        self._http = http

    def collect(self) -> list[Item]:
        payload = self._http.get_json(DAILY_PAPERS_API)
        if not isinstance(payload, list):
            raise ValueError(f"{self.name}: unexpected daily_papers response")
        scored: list[tuple[Item, int]] = []
        seen: set[str] = set()
        for entry in payload:
            paper = entry.get("paper") if isinstance(entry, dict) else None
            if not isinstance(paper, dict):
                continue
            paper_id = paper.get("id")
            if not isinstance(paper_id, str) or not _PAPER_ID.fullmatch(paper_id):
                continue
            if paper_id in seen:
                continue
            title = clean_title(str(paper.get("title") or entry.get("title") or ""))
            if not title:
                continue
            seen.add(paper_id)
            upvotes = _as_int(paper.get("upvotes"))
            published = parse_iso(entry.get("publishedAt")) or parse_iso(paper.get("publishedAt"))
            item = Item(
                title=title,
                url=f"https://huggingface.co/papers/{paper_id}",
                source=self.name,
                published=published,
                snippet=clean_snippet(str(paper.get("summary") or entry.get("summary") or "")),
                extra={"upvotes": upvotes, "arxiv_url": f"https://arxiv.org/abs/{paper_id}"},
            )
            scored.append((item, upvotes))
        floor = datetime.min.replace(tzinfo=UTC)
        scored.sort(key=lambda s: (s[0].published or floor, s[1]), reverse=True)
        return [item for item, _ in scored[: self.limit]]


class HfModelsSource:
    """Models ranked by Hugging Face's trending score (API order is kept)."""

    def __init__(self, name: str, http: HttpClient, *, limit: int = 10) -> None:
        self.name = name
        self.limit = limit
        self._http = http

    @property
    def url(self) -> str:
        return f"{MODELS_API}?sort=trendingScore&limit={min(100, self.limit * 2)}"

    def collect(self) -> list[Item]:
        payload = self._http.get_json(self.url)
        if not isinstance(payload, list):
            raise ValueError(f"{self.name}: unexpected models response")
        items: list[Item] = []
        for model in payload:
            if not isinstance(model, dict) or model.get("private") or model.get("disabled"):
                continue
            model_id = model.get("modelId") or model.get("id")
            if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
                continue
            pipeline = model.get("pipeline_tag")
            downloads = _as_int(model.get("downloads"))
            likes = _as_int(model.get("likes"))
            parts = [str(pipeline)] if isinstance(pipeline, str) and pipeline else []
            parts.append(f"{downloads:,} downloads")
            parts.append(f"{likes:,} likes")
            items.append(
                Item(
                    title=clean_title(model_id),
                    url=f"https://huggingface.co/{model_id}",
                    source=self.name,
                    published=parse_iso(model.get("createdAt")),
                    snippet=clean_snippet(" · ".join(parts)),
                    extra={
                        "pipeline_tag": pipeline if isinstance(pipeline, str) else None,
                        "downloads": downloads,
                        "likes": likes,
                        "trending_score": model.get("trendingScore"),
                    },
                )
            )
            if len(items) >= self.limit:
                break
        return items


def build_hf_papers(spec: dict[str, Any], http: HttpClient, clock: Clock) -> HfPapersSource:
    del clock
    return HfPapersSource(spec_str(spec, "name"), http, limit=spec_int(spec, "limit", 10))


def build_hf_models(spec: dict[str, Any], http: HttpClient, clock: Clock) -> HfModelsSource:
    del clock
    return HfModelsSource(spec_str(spec, "name"), http, limit=spec_int(spec, "limit", 10))
