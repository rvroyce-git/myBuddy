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
