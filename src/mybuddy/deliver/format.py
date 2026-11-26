"""Render a report as Discord markdown or Telegram HTML."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from mybuddy.models import Report

_MD_SPECIAL = re.compile(r"([\\*_~`|\[\]<>#])")


def escape_md(text: str) -> str:
    """Escape Discord markdown so item text can't break formatting or fake links."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = _MD_SPECIAL.sub(r"\\\1", text)
    # Neutralise mass mentions even though allowed_mentions already blocks them.
    return text.replace("@everyone", "@​everyone").replace("@here", "@​here")


def safe_link_url(url: str) -> str:
    """Encode characters that would end a <url> or (url) Discord link early."""
    return quote(url.strip(), safe=":/?#[]@!$&'*+,;=%-._~")


def format_report(report: Report, emoji: str) -> str:
    lines = [f"{emoji} **{escape_md(report.title)}**"]
    if report.headline:
        lines.append(f"**{escape_md(report.headline)}**")
    if report.summary:  # AI summaries are one line; the health check's has several
        lines += ["", *(escape_md(line) for line in report.summary.split("\n"))]
    lines.append("")
    for n, item in enumerate(report.items, start=1):
        parts = [f"{n}. [{escape_md(item.title)}](<{safe_link_url(item.url)}>)"]
        if item.source:
            parts.append(escape_md(item.source))
        if item.blurb:
            parts.append(escape_md(item.blurb))
        lines.append(" — ".join(parts))
    return "\n".join(lines).strip() + "\n"


def format_telegram(report: Report, emoji: str) -> str:
    """Telegram HTML. Every line closes its own tags, so splitting on lines stays valid."""
    esc = html.escape
    lines = [f"{esc(emoji)} <b>{esc(report.title)}</b>"]
    if report.headline:
        lines.append(f"<b>{esc(report.headline)}</b>")
    if report.summary:
        lines += ["", esc(report.summary)]
    lines.append("")
    for n, item in enumerate(report.items, start=1):
        head = f'{n}. <a href="{esc(item.url, quote=True)}">{esc(item.title)}</a>'
        if item.source:
            head += f" · <i>{esc(item.source)}</i>"
        lines.append(head)
        if item.blurb:
            lines.append(esc(item.blurb))
    return "\n".join(lines).strip()
