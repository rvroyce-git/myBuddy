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
* The GitHub data deploy key (`~/.ssh/github_data_deploy`) lives only on the VM. It is a
  write deploy key for this one repository, so it can push to the `data` branch **and to any
  other branch or tag no ruleset protects** (today that includes `gh-pages`). It cannot write
  `main`: the `protect-main` ruleset (PR, squash only, checks `test`/`ui`/`secrets`, linear
  history, no force push) applies to it too. See [Publishing trust boundary](#publishing-trust-boundary)
  for what that means and the steps that close the remaining gap.
* Nothing secret is logged: URLs are redacted before logging (Telegram `/bot<token>` and Discord
  webhook paths become `<redacted>`, query strings are dropped), provider error bodies have the
  API key removed, and chat ids are shown as their last 3 digits only (`src/mybuddy/http.py`
  `_safe_url`, `src/mybuddy/summarize.py`, `src/mybuddy/subscriptions.py` `mask_chat_id`).
* API keys never follow a redirect to another host (see
  [docs/walkthrough.md](docs/walkthrough.md#1-http-redirects-and-credential-stripping)).
* Every commit is scanned by gitleaks (pre-commit hook), and CI scans the **full history** with a
  pinned, checksum-verified gitleaks on every push and PR (`secrets` job). Fake test tokens are
  allowlisted value by value in `.gitleaks.toml`.

## Publishing trust boundary

How the website is published: the server commits `reports.json` to the `data` branch
(`deploy/publish-reports.sh`). The **Publish website** workflow on `main`
(`.github/workflows/pages.yml`) runs every 10 minutes, reads the `data` branch as plain data,
compares its commit with the one recorded in the live site (`.data-sha` on `gh-pages`), and
rebuilds `gh-pages` only when it changed (or when `site/` on `main` changed). Scheduled runs
always use `main`'s copy of the workflow, so nothing pushed to `data` is ever executed. Every
`actions/checkout` in this repository uses `persist-credentials: false`; only
`build-pages.sh` uses `GITHUB_TOKEN`, explicitly, to push `gh-pages`.

**Remaining risk (until the owner steps below are done):**

1. The `data` branch still holds the old `.github/workflows/publish-data.yml` (with
   `contents: write`). Push events run the workflow file *from the pushed branch*, so whoever
   holds the data deploy key could rewrite that file and use the workflow's token to push
   anything to `gh-pages` (deface the website). Code on `main` and the server stay safe.
2. The deploy key itself can push to any unprotected branch or tag, including `gh-pages`
   directly.

The next report normally triggers a rebuild that overwrites a defaced `gh-pages`, but an
attacker can also fake `.data-sha`, so do not rely on that: treat any unexpected `gh-pages`
commit (author other than `github-actions[bot]`) as an incident, rotate the data deploy key (see
below) and re-run the workflow (Actions > Publish website > Run workflow).

**Owner steps to close it:**

(a) After this change is merged **and** a scheduled *Publish website* run has published a new
report (Actions tab: a `schedule` run with the publish step not skipped, and the live site
shows the new report), delete the old workflow from the `data` branch:

```bash
git fetch origin data
git switch -c drop-data-workflow origin/data
git rm .github/workflows/publish-data.yml
git commit -m "Remove publish-data workflow: main's scheduled pages.yml publishes now"
git push origin HEAD:data
git switch main && git branch -D drop-data-workflow
```

(b) Add a ruleset so the deploy key can write only `data`: Settings > Rules > Rulesets > New
ruleset > *New branch ruleset* (then repeat as a *tag ruleset*): name `protect-other-refs`,
Enforcement **Active**, **no bypass list**, Target: *Include all branches* (all tags), *Exclude
by pattern* `data`; rules: **Restrict creations**, **Restrict updates**, **Restrict
deletions**. Caveat: `gh-pages` must stay pushable by GitHub Actions, and a ruleset with no
bypass would block the workflow too. Choose one:

* **Now:** also exclude `gh-pages` from the branch ruleset and accept that the deploy key can
  still push it (risk 2 above remains for that one branch).
* **Recommended future step** (not done in this change): publish with GitHub's Pages
  deployment instead of a branch, so no branch serves the site at all. Settings > Pages >
  Build and deployment > Source: **GitHub Actions**; Settings > Environments > `github-pages` >
  Deployment branches and tags: **Selected branches** > add `main` only. Then change
  `pages.yml` to build into a folder and use `actions/upload-pages-artifact` +
  `actions/deploy-pages` (pinned by full commit SHA) with `permissions: pages: write,
  id-token: write` and `environment: github-pages`, drop `contents: write`, and finally delete
  the `gh-pages` branch, so the branch ruleset needs no exception.

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

Server (Oracle Always Free VM, Ubuntu 24.04). The lockdown below was first done **by hand on
22 Nov 2026**; `deploy/harden.sh` was written afterwards to reproduce it on a fresh VM (it is
idempotent, has `--dry-run`, keeps backups of replaced files in `/var/backups/mybuddy/<time>/`,
and only *checks* Oracle's iptables rules, never changes them):

* Packages upgraded; `unattended-upgrades` installs security updates daily.
* SSH: keys only (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`), no root
  login, `MaxAuthTries 3` (`/etc/ssh/sshd_config.d/99-mybuddy.conf`).
* fail2ban: sshd jail, 5 failures in 10 min → banned 1 h (systemd journal backend).
* The app runs as `mybuddy`, a user without sudo; linger lets its services run without a login.
* Oracle's own iptables rules are kept (UFW is not used: it conflicts with Oracle images). The
  Oracle Security List allows only TCP 22 inbound; the app itself opens **no** ports (the bot
  uses long polling). Port 22 is open to `0.0.0.0/0` because of the owner's CGNAT mobile network;
  [docs/ssh-access.md](docs/ssh-access.md) describes how to close it.
* The server pushes to GitHub with GitHub's host keys pinned (`deploy/github_known_hosts`,
  checked against GitHub's published fingerprints; `StrictHostKeyChecking=yes`), so a
  spoofed github.com is refused instead of trusted on first use.
* 2 GB swap (`vm.swappiness=20`), timezone Asia/Kolkata.
* systemd sandboxing of all three services: read-only system and home, no capabilities, no new
  privileges, syscall filter, memory/CPU caps (each directive explained in
  [docs/walkthrough.md](docs/walkthrough.md#3-systemd-hardening)).

Application: HTTPS only, every host (including each redirect hop) must be on an allowlist, IP
addresses never allowed, 20 s timeout and 5 MB cap per response; feed and chat text is treated as
data, never as instructions, and no LLM has shell, file or network tools; links in reports come
only from collected items. Supply chain: hash-pinned dependencies, SHA-pinned GitHub Actions,
Dependabot.
