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

## Public subscriptions (Telegram)

Anyone can subscribe through the bot [@myBuddy_update_bot](https://t.me/myBuddy_update_bot).
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

## Setup

```bash
.venv/bin/mybuddy init       # guided: pick topics, delivery channel, optional times (IST)
.venv/bin/mybuddy doctor     # check everything: PASS / WARN / FAIL, exit 1 on any FAIL
```

`mybuddy init` asks which topics to enable (from `config/topics.yaml`), the delivery channel
(Telegram, Discord or the local outbox) and optional time changes; the timezone is always IST.
It then shows the complete plan (topics, times, sources and hosts, files it will write, the
secrets still missing, by name only) and writes nothing unless you type exactly `yes`. It
writes `config/topics.local.yaml` (mode 600, not committed; `--output` to change), validated
with the normal config loader, and sets `MYBUDDY_CONFIG` to it in the env file. It never asks
for secrets: set those with `deploy/set-secrets.sh`. Re-running with the same answers changes
nothing. Non-interactive: `mybuddy init --answers answers.yaml --yes` (keys `topics`: `all` or
a list of ids, `delivery`, `schedule`: `{topic_id: "HH:MM"}`); an existing, different file is
only overwritten with `--force`. The delivery channel decides which secrets the plan lists;
delivery itself still follows the secrets that are set.

`mybuddy doctor` checks the config, which env vars are set (names only), the env file (600)
and data dir (700) permissions, that the data dir is writable, SQLite integrity, each topic's
last run (stale after 26 h, or the topic's longest scheduled gap + 2 h), the age of
`reports.json`, Groq (if a key is set: reachable and `GROQ_MODEL` offered), the Telegram bot
(`getMe`; the token is never printed), that the host allowlist covers every config URL, free
disk and memory, and the systemd units (`mybuddy`, `mybuddy-bot`; `mybuddy-publish.path` only
warns). Checks that do not apply (no key; on macOS no `/proc/meminfo` or `systemctl`) are
SKIPped. `mybuddy check` is the older, more verbose subset.

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

## License

MIT — see [LICENSE](LICENSE).
