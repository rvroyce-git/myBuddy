# Security policy

## Supported versions

MyBuddy is a single deployment: only the current `main` branch (what runs on the server) gets
fixes. There are no released versions to back-port to.

## Reporting a vulnerability

Please report privately through GitHub: the repository's **Security** tab > *Report a
vulnerability* (private vulnerability reporting). Do not open a public issue for security
problems. Include what you found, how to reproduce it, and the impact you expect. You will get an
answer as soon as the owner sees it; fixes go through the normal PR process and are credited if
you wish.

In scope: this repository's code, deploy scripts, workflows and the website. Out of scope: the
third-party services themselves (Oracle, Groq, Telegram, GitHub).

## How secrets are handled

* Secrets: `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional `DISCORD_WEBHOOK_URL` and
  `GEMINI_API_KEY`. They live **only** in `~/.config/mybuddy/env` (mode 600) on the VM, written
  by `deploy/set-secrets.sh` with hidden prompts (never on screen, in shell history or in a chat).
  `mybuddy check` warns if the file is readable by others.
* The GitHub data deploy key (`~/.ssh/github_data_deploy`) lives only on the VM and can push to
  this one repository; `main` is protected by a ruleset (PR, squash only, checks
  `test`/`ui`/`secrets`, linear history, no force push), so the key cannot change code that
  ships.
* Nothing secret is logged: URLs are redacted before logging (Telegram `/bot<token>` and Discord
  webhook paths become `<redacted>`, query strings are dropped), provider error bodies have the
  API key removed, and chat ids are shown as their last 3 digits only (`src/mybuddy/http.py`
  `_safe_url`, `src/mybuddy/summarize.py`, `src/mybuddy/subscriptions.py` `mask_chat_id`).
* API keys never follow a redirect to another host (see
  [docs/walkthrough.md](docs/walkthrough.md#1-http-redirects-and-credential-stripping)).
* Every commit is scanned by gitleaks (pre-commit hook), and CI scans the **full history** with a
  pinned, checksum-verified gitleaks on every push and PR (`secrets` job). Fake test tokens are
  allowlisted value by value in `.gitleaks.toml`.

### History scan (26 Nov 2026)

A full-history gitleaks scan found **no real secret ever committed**. One Groq API key was
exposed in a chat conversation (not in git) on 22 Nov 2026; it was revoked and replaced.

## Rotating keys

Do this when a key may have leaked, and otherwise about once a year. Run the commands from your
own terminal; `<vm-ip>` is the server.

**Groq API key**
1. console.groq.com > *API Keys* > *Create API Key*.
2. `MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh`: paste the new key at the Groq prompt,
   press Enter for the others.
3. `ssh ubuntu@<vm-ip> 'sudo systemctl restart mybuddy mybuddy-bot'`; check the next report (or
   `mybuddy run ai_news`) has an AI summary.
4. Delete the old key in the Groq console.

**Telegram bot token**
1. In Telegram, @BotFather > `/revoke` > choose the bot. The old token stops working at once and
   BotFather shows the new one.
2. `set-secrets.sh` (Telegram prompt), then restart `mybuddy` and `mybuddy-bot`.
3. Send `/help` to the bot to confirm it answers. Subscribers are unaffected (same bot).

**GitHub data deploy key** (on the VM)
```bash
ssh ubuntu@<vm-ip>
sudo -u mybuddy ssh-keygen -t ed25519 -N "" -C mybuddy-data-deploy -f /home/mybuddy/.ssh/github_data_deploy.new
sudo cat /home/mybuddy/.ssh/github_data_deploy.new.pub    # public part only
```
1. GitHub > repository Settings > *Deploy keys* > *Add deploy key*: paste it, tick **Allow write
   access**.
2. Swap: `sudo -u mybuddy mv /home/mybuddy/.ssh/github_data_deploy.new /home/mybuddy/.ssh/github_data_deploy`
   (and the same for `.pub`).
3. Test: `sudo -u mybuddy ssh -i /home/mybuddy/.ssh/github_data_deploy -o IdentitiesOnly=yes -T git@github.com`.
4. Delete the old deploy key on GitHub.

**Your SSH login key**: add the new public key to `~/.ssh/authorized_keys` of both `ubuntu`
and `mybuddy`, test a new login with it, then remove the old line.

**Optional keys** (Gemini, Discord webhook): create a new one in that service, `set-secrets.sh`,
restart, delete the old one.

## What was hardened

Server (Oracle Always Free VM, Ubuntu 24.04; reproducible with `deploy/harden.sh`):

* Packages upgraded; `unattended-upgrades` installs security updates daily.
* SSH: keys only (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`), no root
  login, `MaxAuthTries 3` (`/etc/ssh/sshd_config.d/99-mybuddy.conf`).
* fail2ban: sshd jail, 5 failures in 10 min → banned 1 h (systemd journal backend).
* The app runs as `mybuddy`, a user without sudo; linger lets its services run without a login.
* Oracle's own iptables rules are kept (UFW is not used: it conflicts with Oracle images). The
  Oracle Security List allows only TCP 22 inbound; the app itself opens **no** ports (the bot
  uses long polling). Port 22 is open to `0.0.0.0/0` because of the owner's CGNAT mobile network;
  [docs/ssh-access.md](docs/ssh-access.md) describes how to close it.
* 2 GB swap (`vm.swappiness=20`), timezone Asia/Kolkata.
* systemd sandboxing of all three services: read-only system and home, no capabilities, no new
  privileges, syscall filter, memory/CPU caps (each directive explained in
  [docs/walkthrough.md](docs/walkthrough.md#3-systemd-hardening)).

Application: HTTPS only, every host (including each redirect hop) must be on an allowlist, IP
addresses never allowed, 20 s timeout and 5 MB cap per response; feed and chat text is treated as
data, never as instructions, and no LLM has shell, file or network tools; links in reports come
only from collected items. Supply chain: hash-pinned dependencies, SHA-pinned GitHub Actions,
Dependabot.
