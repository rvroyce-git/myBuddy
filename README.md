# MyBuddy

Your own AI news buddy, self-hosted on an Oracle Cloud Always Free VM for $0.

Every morning MyBuddy collects public news on 7 topics, asks a free AI model (Groq) for a short
summary in which every item links to its source, and posts it to a private Discord channel. A
static website (`site/`) shows the latest reports as widgets.

| Topic | When (IST) |
| --- | --- |
| AI news | Daily 06:30 |
| Cybersecurity alerts | Daily 07:00 (+ hourly Discord ping for actively exploited CVEs) |
| Trending GitHub repos | Daily 07:15 |
| One deep AI topic | Mon–Fri 07:30 |
| AI research papers | Daily 07:45 |
| Startup & product launches | Daily 08:00 |
| New open-source AI models | Daily 08:15 |

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # offline unit tests
.venv/bin/ruff check . && .venv/bin/mypy
MYBUDDY_FAKE=1 .venv/bin/mybuddy run all --dry-run   # real feeds, fake AI + Discord
```

## Deploy

```bash
MYBUDDY_HOST=<vm-ip> bash deploy/deploy.sh       # copy, install, start the service
MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh  # run yourself: hidden prompt for keys
```

Secrets live only in `~/.config/mybuddy/env` (mode 600) on the VM. Never commit them.
