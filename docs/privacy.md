# Privacy: what MyBuddy stores and what leaves the server

MyBuddy reads **public** news and sends summaries. The only personal data it handles are
Telegram chat ids (and, if a chat feature is enabled, the questions people send the bot).

## Stored on the Oracle VM

All under the `mybuddy` user, which no other account on the VM can read.

| What | Where | Retention |
| --- | --- | --- |
| Secrets (`GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional `DISCORD_WEBHOOK_URL`, `GEMINI_API_KEY`) and `TELEGRAM_CHAT_ID` | `~/.config/mybuddy/env`, mode 600 (`mybuddy check` warns if not) | until replaced |
| Seen-item hashes (deduplication) | `~/.local/share/mybuddy/mybuddy.db` (SQLite, dir mode 700), table `seen` | 30 days (`SEEN_RETENTION_DAYS`) |
| Saved reports (public news titles, links, summaries) | same DB, table `reports` | last 30 per topic (`REPORTS_PER_TOPIC`) |
| Source snapshots (GitHub star counts) and the bot's update offset | same DB, table `snapshots` | overwritten |
| Subscribers: Telegram chat id, topic ids, active flag, created/updated time. **No** names, usernames or phone numbers | same DB, table `subscribers` | kept; `/stop` or blocking the bot sets `active=0` |
| Service logs | systemd journal | journald defaults (size-capped, rotated). Chat ids appear only as the last 3 digits; message text is never logged |
| Latest `reports.json` | `~/app/site/data/` | overwritten on every report |

## What leaves the server

| To | What | Why |
| --- | --- | --- |
| Groq (`api.groq.com`) | For each AI report: up to 12 news titles + snippets and the prompt. If chat is enabled: the text of questions sent to the bot. No chat ids or names. | summarising |
| Google Gemini (only if `GEMINI_API_KEY` is set) | same as Groq, only when Groq fails | fallback |
| Telegram (`api.telegram.org`) | the reports, bot replies, and the chat ids they go to | delivery. Bot chats are not end-to-end encrypted: Telegram can read them |
| Discord (only if a webhook is set) | the reports | delivery |
| GitHub (`data` branch, public) | `reports.json`: public news items and summaries only, no subscriber data | the website |
| News sources (hosts in the allowlist, `mybuddy check` lists them) | ordinary HTTPS requests with the User-Agent `MyBuddy/0.1 (+personal news reader)` | collecting news |

Groq's and Telegram's own retention follows their privacy policies
(<https://groq.com/privacy-policy>, <https://telegram.org/privacy>). Anything sent to Groq is
untrusted *data* for the model, never instructions, and the model has no tools.

## The website

Static files on GitHub Pages. No cookies, analytics or trackers. The page loads fonts from Google
Fonts, so Google and GitHub see visitors' IP addresses as any web host does. Topic picks and
"already seen" markers are kept in the visitor's own browser (`localStorage`), never sent to us.
Subscribing happens in Telegram, not on the website.

## How to delete data

Commands run on the VM as the admin user (`ssh ubuntu@<vm-ip>`).

* **A subscriber leaving:** `/stop` in the bot stops delivery at once (the row stays with
  `active=0` so the subscriber count is honest). To remove the row too (the owner, on request):

  ```bash
  sudo -u mybuddy python3 -c 'import sqlite3,sys; db=sqlite3.connect("/home/mybuddy/.local/share/mybuddy/mybuddy.db"); n=db.execute("DELETE FROM subscribers WHERE chat_id=?", (int(sys.argv[1]),)).rowcount; db.commit(); print(n, "row(s) deleted")' <chat-id>
  ```

* **Everything on the server:** `sudo systemctl stop mybuddy mybuddy-bot mybuddy-publish.path`,
  then `sudo rm -rf /home/mybuddy/.local/share/mybuddy /home/mybuddy/.config/mybuddy`.
  Terminating the instance in the Oracle console deletes the boot volume as well (tick
  *Permanently delete the attached boot volume*).
* **The public website data:** reports contain only public news. Old versions stay in the
  `data` branch history; removing them needs a rewrite of that branch (not `main`).
* **Groq / Telegram:** revoke the key or token (see [SECURITY.md](../SECURITY.md#rotating-keys));
  data already sent follows their policies.
