# Troubleshooting

Symptom → likely cause → fix. Commands run on the VM: as the admin user
(`ssh ubuntu@<vm-ip>`, has sudo and can read the journal) unless they start with
`sudo -u mybuddy`. Handy first steps:

```bash
systemctl status mybuddy mybuddy-bot mybuddy-publish.path --no-pager
journalctl -u mybuddy -n 100 --no-pager          # scheduler + reports ([mybuddy] lines)
sudo -u mybuddy bash -c 'cd ~/app && .venv/bin/mybuddy check'   # config, env, connectivity
sudo -u mybuddy bash -c 'cd ~/app && .venv/bin/mybuddy list'    # next run of each topic
```

## No reports arrive

| Cause | Check | Fix |
| --- | --- | --- |
| VM stopped (e.g. Oracle reclaimed an idle Always Free instance) | Console > Compute > Instances: state | *Start*. The Watchdog email usually tells you first. |
| Service not running | `systemctl status mybuddy` | `sudo systemctl restart mybuddy`; read the journal for the error |
| Nothing new to report | journal: `<topic>: no new items (… all seen)` | normal; items are deduplicated for 30 days |
| Config error after an edit | `mybuddy check` shows `ERROR` | fix `config/topics.yaml`, redeploy |
| A topic ran but delivery failed | journal around the scheduled time | see Telegram below |

Run one topic by hand: `sudo -u mybuddy bash -c 'cd ~/app && .venv/bin/mybuddy run ai_news'`
(add `--dry-run` to collect without AI or delivery).

## Groq errors (journal: `<topic>: summary via groq failed: groq: HTTP …`)

Every Groq failure falls back to a plain report from the snippets, so a report still arrives.

| Status | Cause | Fix |
| --- | --- | --- |
| 401 | key wrong, revoked or rotated | create a key at console.groq.com, `MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh`, `sudo systemctl restart mybuddy mybuddy-bot` |
| 429 | free-tier rate/day limit (≈ 30 req/min, 8K tokens/min, 1K req/day, 200K tokens/day) | MyBuddy waits once (≤ 8 s, `Retry-After`) and retries; otherwise wait for the quota to reset. Check nothing else uses the same key |
| 400 | bad request: usually `GROQ_MODEL` names a model Groq retired, or the input was too long | the logged body says which; set a current model (`GROQ_MODEL` in the env file, default `openai/gpt-oss-120b`) |

## Telegram not delivering

| Symptom / log | Cause | Fix |
| --- | --- | --- |
| HTTP 401 / 404 from `api.telegram.org/bot<redacted>` | bot token revoked or mistyped | new token from @BotFather, `set-secrets.sh`, restart both services |
| HTTP 400 `chat not found` for the owner | `TELEGRAM_CHAT_ID` wrong, or you never messaged the bot | send the bot a message, then `sudo systemctl stop mybuddy-bot`, `sudo -u mybuddy bash -c 'cd ~/app && .venv/bin/mybuddy telegram-setup --save'`, `sudo systemctl start mybuddy-bot` |
| HTTP 403 | the chat blocked the bot | subscribers are marked inactive automatically; the owner must unblock |
| bot journal: `HTTP 409 … another bot instance or a webhook active?` | two programs reading updates (e.g. `telegram-setup` while `mybuddy-bot` runs, or a second server) | stop one of them; only one poller per token |
| bot exits with status 78, not restarted | `TELEGRAM_BOT_TOKEN` not configured | set it with `set-secrets.sh`, `sudo systemctl start mybuddy-bot` |

## Website is stale (Watchdog email)

The `Watchdog` workflow fails when the live `reports.json` is older than 26 h. Follow the chain:

1. Reports made? `journalctl -u mybuddy --since today` (if not: *No reports arrive*).
2. Published? `journalctl -u mybuddy-publish -n 30` should say `published reports.json to data`.
   If the path unit is not active: `sudo systemctl enable --now mybuddy-publish.path`.
3. Built? GitHub > Actions > *Publish website* (`pages.yml`): a `schedule` run every ~10 min
   compares the `data` commit with `.data-sha` on `gh-pages` and publishes when it changed
   ("Nothing new to publish." otherwise). *Run workflow* forces a rebuild. GitHub disables
   scheduled workflows after 60 days without repository activity; re-enable it on that page.
4. Served? Settings > Pages: source is `gh-pages` / root.

## Publishing fails (deploy key)

Journal: `Permission denied (publickey)`, `ERROR: The key you are using is not a deploy key` or
`push failed (attempt 3)`.

```bash
sudo -u mybuddy ssh -i /home/mybuddy/.ssh/github_data_deploy -o IdentitiesOnly=yes -T git@github.com
# expected: "Hi rvroyce-git/myBuddy! You've successfully authenticated ..."
```

* Key missing or deleted on GitHub: repository Settings > Deploy keys must list the server key
  with **Allow write access**. To make a new one see [SECURITY.md](../SECURITY.md#rotating-keys).
* Rejected push: the publisher pushes only to `data`; `main` is protected by the ruleset and
  cannot take a direct push from any key.
* `Host key verification failed` or `missing .../github_known_hosts`: GitHub's host keys are
  pinned in `deploy/github_known_hosts` (copied to `~/.ssh/github_known_hosts` by `deploy.sh`).
  Re-run the deploy; if GitHub really rotated a key, refresh the file from
  `https://api.github.com/meta` (`ssh_keys`) and check `ssh-keygen -lf` against
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints>.
* Broken JSON is never published (`publish-reports.sh` validates it first).
* `deploy/deploy.sh` enables `mybuddy-publish.path` only once `~/.ssh/github_data_deploy` exists;
  on a fresh server create the key, add it on GitHub, then re-run the deploy (or step 2 above).

## SSH times out

| Cause | Fix |
| --- | --- |
| Your mobile IP changed (Jio CGNAT) and port 22 is limited to old IPs | see [ssh-access.md](ssh-access.md); rollback: re-add the TCP 22 rule in the Security List |
| Port 22 closed on purpose (Bastion/Tailscale) | open a Bastion session / connect Tailscale first |
| VM stopped | start it in the console |
| fail2ban banned you (below) | |

## SSH: fail2ban banned me

After 5 failed logins within 10 minutes your IP is banned for 1 hour (connection refused or
timeout). Options: wait an hour; switch network (toggle airplane mode for a new mobile IP);
or reach the VM another way (Bastion, Tailscale, serial console) and run:

```bash
sudo fail2ban-client status sshd                 # lists banned IPs
sudo fail2ban-client set sshd unbanip <your-ip>
```

Usual cause: an SSH client offering many keys. Use `-i ~/.ssh/mybuddy -o IdentitiesOnly=yes`.

## arXiv timeouts (journal: `research_papers: source 'arXiv AI/CL/LG' failed: HttpError: …`)

arXiv's API is often slow. MyBuddy retries after 5 s and 15 s (network errors, 429, 5xx), then
gives up on that source only; the other sources (e.g. Hugging Face Daily Papers) still make the
report. Nothing to fix unless it fails every day; then try `mybuddy run research_papers` by
hand to see the error.

## Disk or memory

```bash
df -h /            ; sudo journalctl --disk-usage
free -h            ; swapon --show       # 2 GB /swapfile expected
systemctl status mybuddy | grep -i memory
```

* Disk full: `sudo journalctl --vacuum-size=200M`, `sudo apt-get clean`. The database stays
  small (30-day / 30-report retention).
* `mybuddy` killed with `oom-kill` in the journal: it hit `MemoryMax=300M` (bot: 150M). Check
  for a source returning huge responses (each response is capped at 5 MB); restart
  the service. The swap file keeps the whole VM from freezing.
* Swap missing after a reboot: `grep swapfile /etc/fstab`; rerun `sudo bash deploy/harden.sh`.
