# MyBuddy

A self-hosted AI news buddy: a small Python app that runs for $0 on an Oracle Cloud Always Free
VM.

Every morning MyBuddy collects public news on 7 topics, asks a free AI model on Groq
(`openai/gpt-oss-120b` by default; Gemini is an optional fallback) to summarise it, and sends
each report on Telegram (and optionally Discord). Every item links to its source. The website at
<https://rvroyce-git.github.io/myBuddy/> (GitHub Pages, from `site/`) shows the latest reports.

| Topic | When (IST) |
| --- | --- |
| AI news | Daily 06:30 |
| Cybersecurity alerts | Daily 07:00 (+ hourly alerts for actively exploited CVEs) |
| Trending GitHub repos | Daily 07:15 |
| Deep dive (one AI topic a day) | Mon–Fri 07:30 |
| AI research papers | Daily 07:45 |
| Startup & product launches | Daily 08:00 |
| New open-source AI models | Daily 08:15 |

Telegram-only reports (`website: false` in `config/topics.yaml`; not on the website):

| Report | When (IST) | Goes to |
| --- | --- | --- |
| Morning digest (from today's reports, no fetching) | Daily 08:30 | owner |
| Health check (no AI; also `mybuddy health`, exit 1 if stale/failing) | Daily 10:15 (after the 09:xx reports) | owner only |
| Tech policy watch (India, US, EU) | Daily 09:15 | owner + AI news subscribers |
| Product recalls (FDA drugs/devices, CPSC) | Daily 09:30 | owner |
| Industry signals (AI, chips, cloud) | Daily 09:45 | owner + startup subscribers |
| Weekly review (last 7 days of reports) | Sun 10:00 | owner |

At most 11 Groq calls a day go to scheduled reports (about 50K tokens; free tier 1K req / 200K
tokens), 15 minutes apart. A topic with no successful run for over 26 h (longer for weekday or
weekly topics) is flagged STALE in the health check.

## Public subscriptions (Telegram)

When public signup is on (see [Chat and access](#chat-and-access)), anyone can subscribe
through the bot [@myBuddy_update_bot](https://t.me/myBuddy_update_bot).
Visitors pick topics on the website, which opens
`https://t.me/myBuddy_update_bot?start=<payload>`; the bot subscribes them when they tap Start.

* Commands: `/start`, `/topics` (tap-to-toggle picker + Save), `/stop`, `/help`. Private chats
  only; at most 10 commands and 40 button taps per minute per chat.
* `MYBUDDY_MAX_SUBSCRIBERS` (default 100) caps active subscribers; the owner is always allowed.
* The payload is `t` + a lowercase hex bitmask of each topic's permanent `bit:` in
  `config/topics.yaml` (also exported as `"bit"` in `reports.json`): 0 = AI news, 1 = security,
  2 = GitHub, 3 = deep dive, 4 = papers, 5 = startups, 6 = models. All seven = `t7f`; AI news +
  security = `t3`. Never change or reuse a bit, so old links keep working. A missing or invalid
  payload shows the topic picker instead.
* Each report goes to the owner (`TELEGRAM_CHAT_ID`, every topic) first, then to that topic's
  subscribers (~25 messages/s, at most 120 s per report, stopping after 5 network errors in a
  row). Hourly CVE alerts go to `security` subscribers. Subscriber errors never fail a run;
  people who blocked the bot are marked inactive.
* `mybuddy bot` runs the bot (systemd unit `mybuddy-bot`, long polling, no open ports).
  `mybuddy subscribers` prints counts per topic; `mybuddy check` shows the active total.
* `mybuddy telegram-setup` and the bot both read updates, so stop `mybuddy-bot` before running
  it.

## Chat and access

MyBuddy is **private by default**: only the owner (`TELEGRAM_CHAT_ID`) and chats in
`CHAT_ALLOWLIST` can subscribe or chat. Anyone else gets one polite "private" reply (at most every
10 minutes; no AI call, nothing stored). People who subscribed earlier keep getting reports and
can always `/stop`.

Two switches open it up (both default `false`):

* **Open signup** – `PUBLIC_SIGNUP=true`: anyone who taps Start can pick topics and subscribe.
  The website's subscribe card then says "Tap Start, pick topics, ask anything." (it reads the
  `signup` field of `reports.json`; when private it says "Invite-only for now").
* **Public chat** – `CHAT_PUBLIC=true`: anyone can ask questions, still limited per person
  (`CHAT_RATE_PER_MIN`, `CHAT_DAILY_CAP`), by the shared daily token budget and quiet window, and
  by `CHAT_MAX_USERS_PER_DAY` distinct people per day (new people get a friendly "paused until
  tomorrow" reply; the owner is never capped). Owner commands (`/config`, `/set`, `/note`,
  `/feedback` …) stay owner-only.

For open access put `PUBLIC_SIGNUP=true` and `CHAT_PUBLIC=true` in `~/.config/mybuddy/env` on
the VM, then `sudo systemctl restart mybuddy-bot` (the website line updates at the next report
export, or run `mybuddy export`). Set them back to `false` (and restart) to go private again.
`mybuddy check` and `mybuddy doctor` show both modes and today's chatting people vs the limit.

Allowed chats can ask about the latest reports: send a plain question or `/ask <question>`. Groq
answers (Gemini only as a fallback, if set) from the saved reports only, citing items like
`[security #2]`; the bot adds those items' stored links and removes any other URL. Messages and
feed text are treated as data and the model has no tools. Follow-up questions work: the bot
keeps the last 6 question/answer pairs per chat (at most ~1,500 characters, oldest dropped
first) **in memory only** for 30 minutes of inactivity and passes them back as an untrusted
data block. They are never written to disk or logged; `/reset` (or a restart) forgets them.
Logs show masked chat ids and sizes, never text.

Reports on demand:

* **Anyone** who can use the bot: `/latest <topic>` or "show me today's security" replies with
  that topic's latest saved report, formatted like the scheduled message (no fetch, no AI call).
* **Owner only**: `/run <topic>` or phrases like "send me the security report now", "run github
  trending", "refresh ai news" run the topic immediately in the background and send it to the
  owner only (no subscriber fan-out), then reply "Done" or the error. Topics are matched by
  fixed patterns on ids, titles and aliases (no AI decides). One run at a time; a file lock
  (`run.lock` in the data dir) keeps it from overlapping scheduled runs. Others get a polite no.

| Setting | Default | Meaning |
| --- | --- | --- |
| `PUBLIC_SIGNUP` | `false` | `true` lets anyone subscribe (chat stays owner + allowlist) |
| `CHAT_ALLOWLIST` | empty | extra chat ids, comma separated (the owner is always allowed) |
| `CHAT_PUBLIC` | `false` | `true` lets anyone chat (within the limits below) |
| `CHAT_MAX_USERS_PER_DAY` | `50` | distinct people who may chat per day (IST), owner excluded |
| `CHAT_ENABLED` | `true` if `GROQ_API_KEY` is set | `false` turns chat off |
| `CHAT_RATE_PER_MIN` | `2` | messages per minute per chat |
| `CHAT_DAILY_CAP` | `30` | messages per chat per day (IST) |
| `CHAT_DAILY_TOKEN_BUDGET` | `60000` (capped at `80000`) | Groq tokens per day for all chat together, so reports keep their quota |
| `SITE_URL` | `https://rvroyce-git.github.io/myBuddy/` | website linked from the bot's private-mode reply |

Chat pauses for about 2 minutes either side of each scheduled AI report, so a report never
competes with chat for Groq's per-minute limit. Groq usage and limits: [docs/costs.md](docs/costs.md).

Limits are counted in SQLite, so restarts don't reset them. To add someone: ask them to message
the bot, get their chat id (e.g. from @userinfobot), then on the VM add
`CHAT_ALLOWLIST=123456789,987654321` to `~/.config/mybuddy/env` and
`sudo systemctl restart mybuddy-bot`. To open signup to everyone, add `PUBLIC_SIGNUP=true` the
same way. `mybuddy check` prints the current mode, allowlist size and limits.

### Config changes from chat (owner only)

The owner (never allowlisted chats) can change the config from Telegram. No AI is involved;
the bot understands only these commands:

`/topic on|off <id>` · `/time <id> HH:MM` · `/source add <topic> <https feed url>` ·
`/source remove <topic> <name>` · `/limit chat-daily|chat-rate|chat-tokens <n>` · `/undo`
(also as `/set …`; `/set help` lists them). `/config` shows the current settings and
`/changes` the change log.

Each request is answered with a YAML diff (or the limit change), the result of the real config
validation and a change id. Nothing changes until you send `/approve <id>` within 10 minutes;
`/reject <id>` cancels it. On approval the bot keeps a timestamped backup (`backups/`, mode
600, newest 20), writes the file atomically, and the scheduler reloads it within a minute.
`/undo` restores the newest backup through the same approval. A new feed must be https; its
host is added to `extra_allowed_hosts`, and nothing is fetched until the next run. Secrets
can't be read or set from chat; `/limit` values are stored as overrides in the database.
Approved edits rewrite the file **without its comments** (YAML is re-written from the parsed
data); the first backup, taken before the first approved edit, still has them. `/undo` is one
step: it previews restoring the newest file in `backups/` and, once approved, backs up the
current file first, so a second `/undo` re-applies what the first one undid. To go back further
(or to get the commented original back), copy an older backup over the live file on the VM:
`ls ~/.config/mybuddy/config/backups/`, then `cp <backup> ~/.config/mybuddy/config/topics.yaml`
and `touch ~/.local/share/mybuddy/reload-config`, as the `mybuddy` user.

**Plain English (owner only).** The owner can also just type a request; it is matched by fixed
patterns (no AI) and answered with the same preview and change id. Reply `yes` to apply the
**most recent** pending change (or `/approve <id>`), `no` to cancel it; after 10 minutes it
expires. Text that matches none of these goes to normal chat; nobody else's text is ever
treated as a config request.

| You type | Change |
| --- | --- |
| `stop ai_news` / `disable the security report` | topic off |
| `enable startups` / `start startups` | topic on |
| `send security at 08:30` | daily time 08:30 IST |
| `make policy_watch weekly` / `… daily` | weekly = Sundays at the same time |
| `add https://example.org/feed.xml to ai_news` | add an RSS source |
| `remove TechCrunch AI from industry_signals` | remove a source |
| `stop reddit` | remove every source whose name contains "reddit", in all topics |
| `track quantum computing news daily` | add a Google News search to industry_signals |

On the VM both services use `MYBUDDY_CONFIG=~/.config/mybuddy/config/topics.yaml`, a writable
copy that `deploy.sh` seeds once from `config/topics.yaml`. Later repo changes to the topics
file are **not** copied over it (deploy prints a note when they differ): merge them by hand, or
delete the copy and redeploy to re-seed. Only that folder is writable for the bot; the env
file stays read-only.

### Feedback

Every Telegram report has 👍 / 👎 buttons. Taps from the owner, allowlisted chats and
subscribers count once per person per report (a second tap can change the vote). Voters are
stored as salted hashes, not chat ids; the salt is in the same database, so votes are not
anonymous to anyone who can read it (see [docs/privacy.md](docs/privacy.md)). `/feedback` (owner) shows the last 7 days per topic.
`/note <text>` (owner) saves a note that shows in `/feedback` and is never sent to the AI. The
summariser gets one short line per topic built from vote counts and report lengths only, e.g.
"Readers liked 4 of 5 recent briefs; disliked ones were longer, so keep it tight."

Weekly check-in: on Sundays at 19:00 IST the bot asks the owner "What was useful this week,
and what should change?". The owner's next free-text reply within 24 h, or `/feedback <text>`
at any time, is saved as a reader preference (max 500 characters each; the last 10 are kept).
Summariser prompts include them as a short, fenced "Reader preferences (not facts)" block of at
most ~600 characters, treated as data. Only the owner's text is ever used. `/feedback` with no
text shows the vote summary and the saved preferences.

## Setup

```bash
.venv/bin/mybuddy init       # guided: pick topics, delivery channel, optional times (IST)
.venv/bin/mybuddy doctor     # check everything: PASS / WARN / FAIL, exit 1 on any FAIL
```

`mybuddy init` asks which topics to enable (from `config/topics.yaml`), the delivery channel
(Telegram, Discord or the local outbox) and optional time changes; the timezone is always IST.
It then shows the complete plan (topics, times, sources and hosts, files it will write, the
secrets still missing, by name only) and writes nothing unless you type exactly `yes`. It
writes the live config the services and `/set` use: `MYBUDDY_CONFIG` when set, else
`~/.config/mybuddy/config/topics.yaml` (mode 600, folder 700; `--output` to change), validated
with the normal config loader. It starts from that file when it exists (so feeds and changes
made from chat are kept; `--from` picks another base) and turns unchosen topics off with
`enabled: false` instead of deleting them. Overwriting keeps the old file in `backups/` (so
`/undo` can restore it) and the scheduler reloads within a minute. Only when nothing sets
`MYBUDDY_CONFIG` yet does it add that line to the env file. It never asks for secrets: set
those with `deploy/set-secrets.sh`. Re-running with the same answers changes nothing.
Non-interactive: `mybuddy init --answers answers.yaml --yes` (keys `topics`: `all` or a
list of ids, `delivery`, `schedule`: `{topic_id: "HH:MM"}`); an existing, different file is
only overwritten with `--force`. The delivery channel decides which secrets the plan lists;
delivery itself still follows the secrets that are set.

`mybuddy doctor` checks the config (and that the live copy is in use and private), which env
vars are set (names only), the access mode (private or public signup, allowlist size, chat
limits; never chat ids), the env file (600) and data dir (700) permissions, that the data dir
is writable, SQLite integrity, each topic's last successful run and failed sources from the
health check's run log (stale after 26 h, longer for weekday or weekly topics; the health
report itself included), the age of `reports.json`, Groq (if a key is set: reachable and
`GROQ_MODEL` offered), the Telegram bot (`getMe`; the token is never printed), that the host allowlist covers every config URL, free
disk and memory, and the systemd units (`mybuddy`, `mybuddy-bot`; `mybuddy-publish.path` only
warns). Checks that do not apply (no key; on macOS no `/proc/meminfo` or `systemctl`) are
SKIPped. `mybuddy check` is the older, more verbose subset.

`mybuddy doctor` and `mybuddy health` grade problems the same way: a failed source (one feed
or API erroring in the last 24 h) is a WARN and keeps exit code 0; a topic whose last
successful run is older than its stale threshold is a FAIL and exits 1.

`mybuddy telegram-setup` reports the configured chat (masked) and exits when
`TELEGRAM_CHAT_ID` is already set. If it finds no messages while the `mybuddy-bot` service is
running (the bot consumes updates), run `sudo systemctl stop mybuddy-bot`, message the bot,
run `mybuddy telegram-setup --save`, then `sudo systemctl start mybuddy-bot`.

## Develop and test

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # or the pinned set, see below
.venv/bin/pytest                                      # offline unit tests
.venv/bin/ruff check . && .venv/bin/mypy
MYBUDDY_FAKE=1 .venv/bin/mybuddy run all --dry-run    # real feeds, no AI, no delivery
```

Optional browser tests for the website (`tests/ui`):

```bash
.venv/bin/pip install -e '.[ui]'
.venv/bin/python -m playwright install chromium
.venv/bin/pytest tests/ui
```

Pre-commit hooks (gitleaks secret scan, ruff lint + format, YAML/whitespace checks):

```bash
.venv/bin/pre-commit install            # once per clone
.venv/bin/pre-commit run --all-files    # optional: check everything now
```

The fake tokens in `tests/` are allowlisted value-by-value in `.gitleaks.toml`; CI also scans
the full git history with a pinned, checksum-verified gitleaks binary.

The website's JavaScript (`site/assets/app.js`, the entry module, plus the ES modules in
`site/assets/js/`; no build step) is linted with ESLint (`eslint.config.js`: recommended rules,
browser globals). CI jobs: `test` (ruff, mypy, pytest), `ui` (browser tests), `secrets`
(gitleaks) and `lint-js` (ESLint on Node LTS, `npm ci` from the committed `package-lock.json`).
Lint locally with `npm ci && npx eslint .`.

### Updating pinned dependencies

`pyproject.toml` keeps compatible version ranges; CI and the server install the exact,
hash-pinned versions from `requirements/runtime.txt` and `requirements/dev.txt`
(runtime + `dev` + `ui` extras), resolved for Python 3.12. Regenerate them with pip-tools
from a throwaway Python 3.12 venv (not the project venv), then commit both files:

```bash
python3.12 -m venv /tmp/pt && /tmp/pt/bin/pip install pip-tools
CUSTOM_COMPILE_COMMAND="see README: Updating pinned dependencies" \
  /tmp/pt/bin/pip-compile --upgrade --generate-hashes --strip-extras --allow-unsafe \
  --no-emit-index-url -o requirements/runtime.txt pyproject.toml
CUSTOM_COMPILE_COMMAND="see README: Updating pinned dependencies" \
  /tmp/pt/bin/pip-compile --upgrade --generate-hashes --strip-extras --allow-unsafe \
  --no-emit-index-url --extra dev --extra ui -c requirements/runtime.txt \
  -o requirements/dev.txt pyproject.toml
# Install exactly what CI installs:
.venv/bin/pip install --require-hashes -r requirements/dev.txt && .venv/bin/pip install --no-deps -e .
```

Use `--upgrade-package NAME` instead of `--upgrade` to bump one package. pip-tools records the
hashes of every file PyPI publishes for each pinned version (all platforms), but environment
markers are evaluated on the machine you run it on; if you regenerate on macOS, check the
Linux x86_64 resolution too:

```bash
/tmp/pt/bin/pip install --dry-run --ignore-installed --require-hashes --only-binary=:all: \
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --python-version 3.12 \
  --implementation cp --target /tmp/pt-check -r requirements/dev.txt
```

Dependabot (`.github/dependabot.yml`) opens weekly PRs for `requirements/` and for the
SHA-pinned GitHub Actions. To pin an action by hand, resolve the release tag to its commit
(`gh api repos/OWNER/REPO/git/ref/tags/vX.Y.Z`, following annotated tags) and write
`uses: OWNER/REPO@<40-char sha> # vX.Y.Z`.

## Deploy

```bash
MYBUDDY_HOST=<vm-ip> bash deploy/deploy.sh       # copy, install, start mybuddy + mybuddy-bot
MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh  # run yourself: hidden prompts for keys
ssh mybuddy@<vm-ip> 'cd app && .venv/bin/mybuddy telegram-setup --save'  # after messaging the bot
```

`mybuddy-publish.path` pushes each new `site/data/reports.json` to GitHub with a deploy key
limited to this repository; the scheduled *Publish website* workflow on `main` picks it up
within about 10 minutes and republishes the website (see SECURITY.md, *Publishing trust
boundary*). The `Watchdog` workflow
fails (and GitHub emails the owner) if the live reports are more than 26 hours old.

## Security and privacy

* Secrets (`GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional `DISCORD_WEBHOOK_URL` and
  `GEMINI_API_KEY`) live only in `~/.config/mybuddy/env` (mode 600) on the VM. Never commit them.
* Subscribers: only the Telegram chat id and topic ids are stored (SQLite, mode 600) — no names,
  usernames or phone numbers. Logs show only the last 3 digits of a chat id, never message text.
  `/stop` turns delivery off at once.
* The services run as an unprivileged `mybuddy` user with systemd hardening. The app opens no
  inbound ports (the bot uses long polling).

## More documentation

* [SECURITY.md](SECURITY.md): reporting, secrets, key rotation, what was hardened
* [CONTRIBUTING.md](CONTRIBUTING.md): branch + PR workflow, checks, commit style
* [docs/ssh-access.md](docs/ssh-access.md): closing port 22 (OCI Bastion, Tailscale, fixed IPs)
* [docs/costs.md](docs/costs.md): free tiers and the budget alert ·
  [docs/privacy.md](docs/privacy.md): what is stored and sent where
* [docs/troubleshooting.md](docs/troubleshooting.md) ·
  [docs/walkthrough.md](docs/walkthrough.md): redirects, subscription links, systemd hardening
* Server scripts: `deploy/harden.sh`, `deploy/create-vm.sh`, `deploy/oci-security-list-ssh.sh`,
  `deploy/budget-alert.sh` (all support `--dry-run`)

## License

MIT — see [LICENSE](LICENSE).
