# MyBuddy

Your own AI news buddy, self-hosted on an Oracle Cloud Always Free VM for $0.

Every morning MyBuddy collects public news on 7 topics, asks a free AI model (Groq) for a short
summary in which every item links to its source, and sends it to you on Telegram (or Discord). A
static website (`site/`) shows the latest reports as widgets.

| Topic | When (IST) |
| --- | --- |
| AI news | Daily 06:30 |
| Cybersecurity alerts | Daily 07:00 (+ hourly ping for actively exploited CVEs) |
| Trending GitHub repos | Daily 07:15 |
| One deep AI topic | Mon–Fri 07:30 |
| AI research papers | Daily 07:45 |
| Startup & product launches | Daily 08:00 |
| New open-source AI models | Daily 08:15 |

## Public subscriptions (Telegram)

Visitors pick topics on the website and open
`https://t.me/myBuddy_update_bot?start=<payload>`; the bot subscribes them when they tap Start.
The payload is `t` + a lowercase hex bitmask of each topic's permanent `bit:` in
`config/topics.yaml` (also exported as `"bit"` in `reports.json`): 0 = AI news, 1 = security,
2 = GitHub, 3 = deep dive, 4 = papers, 5 = startups, 6 = models. Never change or reuse a bit;
reordering or removing topics then never breaks old links. All seven = `t7f`; AI news + security = `t3`. A missing or
invalid payload shows a topic picker instead.

* `mybuddy bot` runs the bot (systemd unit `mybuddy-bot`, long polling, no open ports).
  Commands: `/start`, `/topics` (tap-to-toggle picker + Save), `/stop`, `/help`. Private chats
  only; at most 10 commands and 40 button taps a minute per chat; `MYBUDDY_MAX_SUBSCRIBERS` (default 100) caps
  active subscribers (the owner is always allowed).
* Each report goes to the owner (`TELEGRAM_CHAT_ID`, every topic) first, then to that topic's
  subscribers (~25 messages/s, at most 120 s per report, stops after 5 network errors in a
  row). Hourly KEV alerts go to `security` subscribers
  (`audience: security` in `topics.yaml`). Subscriber errors never fail a run; people who
  blocked the bot are marked inactive. Fake mode writes `*-subscribers.md` to the outbox.
* `mybuddy subscribers` prints counts per topic; `mybuddy check` shows the active total.
* Privacy: only the Telegram chat id and topic ids are stored (SQLite, mode 600) — no names,
  usernames or phone numbers. Logs show only the last 3 digits of a chat id, never messages.
  `/stop` turns delivery off at once.
* `mybuddy telegram-setup` and the bot both read updates, so stop `mybuddy-bot` before running
  it.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # offline unit tests
.venv/bin/ruff check . && .venv/bin/mypy
MYBUDDY_FAKE=1 .venv/bin/mybuddy run all --dry-run   # real feeds, fake AI + Discord
```

## Deploy

```bash
MYBUDDY_HOST=<vm-ip> bash deploy/deploy.sh       # copy, install, start mybuddy + mybuddy-bot
MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh  # run yourself: hidden prompt for keys
ssh mybuddy@<vm-ip> 'cd app && .venv/bin/mybuddy telegram-setup --save'  # after messaging the bot
```

Secrets live only in `~/.config/mybuddy/env` (mode 600) on the VM. Never commit them.
