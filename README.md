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
| Health check (no AI; also `mybuddy health`, exit 1 if stale/failing) | Daily 09:00 | owner only |
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

Allowed chats can ask about the latest reports: send a plain question or `/ask <question>`. Groq
answers (Gemini only as a fallback, if set) from the saved reports only, citing items like
`[security #2]`; the bot adds those items' stored links and removes any other URL. Messages and
feed text are treated as data, the model has no tools, and nothing is remembered between
messages (`/reset` just confirms that). Logs show masked chat ids and sizes, never text.

| Setting | Default | Meaning |
| --- | --- | --- |
| `PUBLIC_SIGNUP` | `false` | `true` lets anyone subscribe (chat stays owner + allowlist) |
| `CHAT_ALLOWLIST` | empty | extra chat ids, comma separated (the owner is always allowed) |
| `CHAT_ENABLED` | `true` if `GROQ_API_KEY` is set | `false` turns chat off |
| `CHAT_RATE_PER_MIN` | `5` | messages per minute per chat |
| `CHAT_DAILY_CAP` | `30` | messages per chat per day (IST) |
| `CHAT_DAILY_TOKEN_BUDGET` | `60000` | Groq tokens per day for all chat together, so reports keep their quota |

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
Approved edits rewrite the file without its comments (the first backup keeps them).

On the VM both services use `MYBUDDY_CONFIG=~/.config/mybuddy/config/topics.yaml`, a writable
copy that `deploy.sh` seeds once from `config/topics.yaml`. Later repo changes to the topics
file are **not** copied over it (deploy prints a note when they differ): merge them by hand, or
delete the copy and redeploy to re-seed. Only that folder is writable for the bot; the env
file stays read-only.

### Feedback

Every Telegram report has 👍 / 👎 buttons. Taps from the owner, allowlisted chats and
subscribers count once per person per report (a second tap can change the vote). Voters are
stored as salted hashes, not chat ids. `/feedback` (owner) shows the last 7 days per topic.
`/note <text>` (owner) saves a note that shows in `/feedback` and is never sent to the AI. The
summariser gets one short line per topic built from vote counts and report lengths only, e.g.
"Readers liked 4 of 5 recent briefs; disliked ones were longer, so keep it tight."

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
limited to this repository; GitHub Pages then republishes the website. The `Watchdog` workflow
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
