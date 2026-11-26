# MyBuddy design notes

How the parts of MyBuddy fit together: module responsibilities, data formats and the rules each
part relies on. MyBuddy is written from scratch; it is inspired by the idea behind
[Saathi](https://bhupendrasingh.com/saathi/) — a self-hosted agent that collects news on a schedule,
has an AI summarise it with source links, and delivers it privately.

Runtime target: Oracle Always Free VM.Standard.E2.1.Micro (1 GB RAM, x86_64), Ubuntu 24.04,
Python 3.12, timezone Asia/Kolkata. Keep memory small: stdlib + feedparser + PyYAML only at runtime.
Dev machine has Python 3.14 — code must work on 3.12 and 3.14.

## Shared types
`src/mybuddy/models.py` (keep these signatures stable; every module depends on them):
`Item`, `ReportItem`, `Report`, `HttpClient`, `Clock`, `Source`.

## Sources API
`src/mybuddy/sources/__init__.py` exposes:

```python
SOURCE_TYPES: dict[str, SourceBuilder]
def build_source(spec: dict[str, Any], http: HttpClient, clock: Clock) -> Source
```
`spec` is one entry from `config/topics.yaml` `sources:` list, always with `type` and `name`.
Source types (all free, no API keys):

| type | spec keys | notes |
| --- | --- | --- |
| `rss` | `url`, optional `max_age_hours` (default 48), `limit` (default 10) | RSS/Atom via feedparser; strip HTML from snippet |
| `google_news` | `query`, optional `limit` | builds https://news.google.com/rss/search?q=...&hl=en-IN&gl=IN&ceid=IN:en |
| `hackernews` | `keywords` (list), optional `min_score` (default 50), `limit` | Firebase API topstories + item |
| `reddit` | `subreddit`, optional `limit` | https://www.reddit.com/r/<sub>/top.json?t=day ; tolerate 403/429 by raising |
| `github_trending` | `topics` (list), optional `limit` | GitHub search API (unauthenticated). Rank by stars gained since the previous snapshot; needs a snapshot store: builder receives it via `spec["_state"]` (a `dict[str, int]`-like mapping repo->stars supplied by the engine, mutated in place). First run: rank by total stars of repos pushed in last 7 days. `extra={"stars": n, "stars_gained": d}` |
| `arxiv` | `query`, optional `limit` | export.arxiv.org API (Atom), newest first |
| `hf_papers` | optional `limit` | https://huggingface.co/api/daily_papers |
| `hf_models` | optional `limit` | Hugging Face models API sorted by trending |
| `cisa_kev` | optional `days` (default 2) | CISA Known Exploited Vulnerabilities JSON; only entries with dateAdded within `days`; title "CVE-x: vendor product — name", url to NVD |

Every source returns `Item`s with absolute https URLs. Verify each endpoint with `curl` first.

## Engine
* `config.py`: load `config/topics.yaml` (topics list: `id`, `title`, `emoji`, `description`, `schedule` {`time`: "HH:MM", `days`: list of mon..sun or "daily"} or {`every_minutes`: N}, `ai`: bool, `prompt`: file name in `src/mybuddy/prompts/`, `rotation` optional (weekday -> {title, sources}), `sources`: list). Settings from env: `GROQ_API_KEY`, `GROQ_MODEL` (default `openai/gpt-oss-120b`), `GEMINI_API_KEY` (optional fallback), `DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MYBUDDY_DATA_DIR` (default `~/.local/share/mybuddy`), `MYBUDDY_FAKE=1` (fake AI + fake delivery: write to files, no network).
* `http.py`: urllib client implementing `HttpClient`; https only and only to hosts in its `allowed_hosts` (checked for every request and redirect hop; IP addresses never allowed; built by `allowlist.py` from config source URLs, each source type's fixed API hosts, the configured AI/delivery providers and `extra_allowed_hosts` in `config/topics.yaml`), timeout 20 s, 5 MB cap, User-Agent "MyBuddy/0.1 (+personal news reader)".
* `store.py`: SQLite in data dir: seen item URLs per topic (dedupe), GitHub star snapshots, saved reports.
* `summarize.py`: OpenAI-compatible chat completions to Groq (`https://api.groq.com/openai/v1/chat/completions`), Gemini fallback (`https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`), JSON output {headline, summary, blurbs[]}; validate; on any failure fall back to a no-AI report built from snippets. Never invent links — URLs always come from collected items. Cap input items (~12) to stay inside free token limits.
* `deliver.py`: Discord webhook (split at 2000 chars) and/or Telegram bot (4096 chars); format with topic emoji + headline + items as `[title](<url>)` lines; fake mode writes to data dir `outbox/`.
* `engine.py`: run one topic: build sources, collect each in isolation (log failures), dedupe, summarise, deliver, save report, export.
* `schedule.py`: tiny in-process scheduler (no APScheduler): computes next run for each topic in Asia/Kolkata; `mybuddy serve` loops and sleeps.
* `export.py`: writes `site/data/reports.json` (path configurable) in the format below.
* `cli.py`: `mybuddy run <topic|all> [--dry-run]`, `mybuddy list`, `mybuddy serve`, `mybuddy check` (config + env + connectivity), `mybuddy export`, `mybuddy telegram-setup`, `mybuddy bot`, `mybuddy subscribers`.

## The 7 topics (exactly these; nothing else)
| id | title | when (IST) | ai |
| --- | --- | --- | --- |
| `ai_news` | AI news | daily 06:30 | yes |
| `security` | Cybersecurity alerts | daily 07:00 | yes |
| `kev_alerts` | Actively exploited vulnerabilities (part of cybersecurity) | every 60 min | no |
| `github_trending` | Trending GitHub repos | daily 07:15 | yes |
| `deep_dive` | One deep AI topic a day | mon-fri 07:30 (Mon RAG, Tue agents, Wed training & fine-tuning, Thu audio & video, Fri evaluation) | yes |
| `research_papers` | AI research papers | daily 07:45 | yes |
| `startups` | Startup & product launches | daily 08:00 | yes |
| `ai_models` | New open-source AI models | daily 08:15 | yes |

(`kev_alerts` is the hourly sub-alert of the cybersecurity topic, not an extra topic.)

## Website data contract: `site/data/reports.json`
```json
{
  "generated_at": "2026-11-23T08:15:02+05:30",
  "topics": [
    {
      "id": "ai_news",
      "title": "AI news",
      "emoji": "🧠",
      "description": "Models, labs, funding, policy and open source.",
      "schedule": "Daily · 06:30 IST",
      "report": {
        "created_at": "2026-11-23T06:30:11+05:30",
        "headline": "One-sentence takeaway.",
        "summary": "Two to four plain sentences.",
        "items": [
          {"title": "…", "url": "https://…", "source": "…", "blurb": "…", "published": "2026-11-23T01:02:03+00:00"}
        ]
      }
    }
  ]
}
```
`report` may be `null` when a topic has not run yet. The website must handle that.

## Subscriptions contract (website ↔ Telegram bot)

Visitors pick topics on the website and subscribe to the existing bot `@myBuddy_update_bot`
(Telegram cannot message a phone number; the visitor must open the bot and tap Start).

* **Stable topic bits** (v2): every website topic (`ai: true`, unless `website: false`) has a permanent `bit:` in
  `config/topics.yaml` — unique, 0–59, never changed or reused, even if topics are reordered or
  removed. Today: `ai_news 0, security 1, github_trending 2, deep_dive 3, research_papers 4,
  startups 5, ai_models 6` (identical to the old positional order, so existing links stay valid).
  `export.py` writes it into each topic of `reports.json` as `"bit": <int>`. The website uses the
  topic's `bit` from `reports.json`; if a topic has no `bit` (older data) it falls back to the
  built-in map above by topic id, and hides any chip whose bit is still unknown.
* **Start link**: `https://t.me/myBuddy_update_bot?start=<payload>` where payload is
  `t` + lowercase hex of a bitmask with bit *n* set for each chosen topic's `bit`.
  All 7 topics → `t7f`; AI news + security → `t3`. Payload must match `^t[0-9a-f]{1,15}$`;
  anything else (or bits that no current topic owns) is ignored and the bot shows the picker.
  `/start` with no payload also shows the picker.
* **Bot commands**: `/start [payload]`, `/topics` (inline-keyboard picker: one toggle button per
  topic, plus "Save"), `/stop` (unsubscribe), `/help`. Replies are short, friendly English.
* **Delivery**: each AI topic's report goes to the owner (`TELEGRAM_CHAT_ID`, all topics) and to
  every active subscriber of that topic; duplicates removed. `kev_alerts` (non-AI, hourly) goes to
  the owner and to subscribers of `security`. Telegram-only topics (`website: false`: digest,
  policy watch, recalls, industry signals, weekly review, health check) have no bit and are not
  in `reports.json`; they go to the owner, plus the subscribers of their `audience:` topic if
  set (the health check never has one). A subscriber error never blocks the owner's delivery;
  a subscriber who blocked the bot (HTTP 403) is marked inactive.
* **Storage**: SQLite `subscribers(chat_id, topics, active, created_at, updated_at)`. Only the
  Telegram chat id and topic ids are stored — no names, usernames or phone numbers.
* **Limit**: `MYBUDDY_MAX_SUBSCRIBERS` (default 100) active subscribers; new ones beyond that get a
  polite "full" message. Per-chat flood guard: ignore more than 10 commands (or 40 picker taps) per minute.
