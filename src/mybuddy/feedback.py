"""👍/👎 feedback on delivered reports and the summariser's preference hint.

Each Telegram report message carries two buttons whose callback data is only
"fb:<u|d>:<topic id>:<yymmddHHMM>" (the report's IST run time): no secrets, no chat ids.
Votes are stored per (report, salted voter hash), so a second tap only changes the vote.
The summariser gets a short hint built only from vote counts and report lengths: no text
from any user ever reaches a prompt. The owner's /note text is shown in /feedback only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from mybuddy.config import Topic
from mybuddy.models import Report
from mybuddy.store import Store, report_ref

CALLBACK_RE = re.compile(r"fb:([ud]):([a-z][a-z0-9_]{0,39}):([0-9]{10})")
HINT_DAYS = 30
HINT_REPORTS = 10
HINT_MIN_RATED = 2
HINT_MAX_CHARS = 200
SUMMARY_DAYS = 7


def callback_data(report: Report, vote: str) -> str:
    return f"fb:{vote}:{report.topic_id}:{report_ref(report.created_at)}"


def feedback_markup(report: Report) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "👍", "callback_data": callback_data(report, "u")},
                {"text": "👎", "callback_data": callback_data(report, "d")},
            ]
        ]
    }


def preference_hint(store: Store, topic_id: str, now: datetime) -> str:
    """e.g. "Readers liked 4 of 5 recent briefs; disliked ones were longer, so keep it tight."

    Built only from counts and lengths; empty when there are too few votes."""
    rated = store.report_votes(topic_id, now - timedelta(days=HINT_DAYS), HINT_REPORTS)
    rated = [r for r in rated if r[0] != r[1]]  # ties say nothing
    if len(rated) < HINT_MIN_RATED:
        return ""
    liked = [chars for up, down, chars in rated if up > down]
    disliked = [chars for up, down, chars in rated if down > up]
    hint = f"Readers liked {len(liked)} of {len(rated)} recent briefs"
    if liked and disliked:
        avg_liked = sum(liked) / len(liked)
        avg_disliked = sum(disliked) / len(disliked)
        if avg_disliked > avg_liked * 1.2:
            hint += "; disliked ones were longer, so keep it tight"
        elif avg_disliked < avg_liked * 0.8:
            hint += "; disliked ones were shorter, so add a little more detail"
    elif disliked and not liked:
        hint += "; keep it short, concrete and useful"
    return (hint + ".")[:HINT_MAX_CHARS]


def weekly_summary(store: Store, topics: Sequence[Topic], now: datetime) -> str:
    totals = store.vote_totals(now - timedelta(days=SUMMARY_DAYS))
    lines = [f"Feedback, last {SUMMARY_DAYS} days:"]
    known = {t.id for t in topics}
    for t in topics:
        up, down = totals.get(t.id, (0, 0))
        if up or down:
            lines.append(f"• {t.emoji} {t.id}: 👍 {up} · 👎 {down}")
    for topic_id, (up, down) in totals.items():
        if topic_id not in known:
            lines.append(f"• {topic_id}: 👍 {up} · 👎 {down}")
    if len(lines) == 1:
        lines.append("No votes yet.")
    notes = store.notes_since(now - timedelta(days=SUMMARY_DAYS))
    if notes:
        lines += ["", "Your notes:"] + [f"• {n}" for n in notes]
    return "\n".join(lines)
