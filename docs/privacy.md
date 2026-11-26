# Privacy: what MyBuddy stores and what leaves the server

MyBuddy reads **public** news and sends summaries. The only personal data it handles are
Telegram chat ids, 👍/👎 votes linked to them (see below), the owner's own notes, and the
questions allowed chats send the bot (forwarded to Groq, never stored).

## Stored on the Oracle VM

All under the `mybuddy` user, which no other account on the VM can read.

| What | Where | Retention |
| --- | --- | --- |
| Secrets (`GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional `DISCORD_WEBHOOK_URL`, `GEMINI_API_KEY`) and `TELEGRAM_CHAT_ID` | `~/.config/mybuddy/env`, mode 600 (`mybuddy check` warns if not) | until replaced |
| Seen-item hashes (deduplication) | `~/.local/share/mybuddy/mybuddy.db` (SQLite, dir mode 700), table `seen` | 30 days (`SEEN_RETENTION_DAYS`) |
| Saved reports (public news titles, links, summaries) | same DB, table `reports` | last 30 per topic (`REPORTS_PER_TOPIC`) |
| Source snapshots (GitHub star counts) and the bot's update offset | same DB, table `snapshots` | overwritten |
| Subscribers: Telegram chat id, topic ids, active flag, created/updated time. **No** names, usernames or phone numbers | same DB, table `subscribers` | kept; `/stop` or blocking the bot sets `active=0` |
| Run log and source failures (topic id, time, status, item counts; failed source name and a short error text; no personal data) | same DB, tables `runs`, `source_failures` | 14 days (`RUNS_RETENTION_DAYS`), for the health check |
| Chat usage: chat id, time, IST day, tokens used. **Never** the question or the answer | same DB, table `chat_usage` | 3 days (`CHAT_USAGE_RETENTION_DAYS`), for rate limits and daily caps |
| 👍/👎 votes on reports: topic, report reference, vote, report length, time, and the voter as a **salted hash** of the chat id (see below: not anonymous) | same DB, table `feedback` | 90 days (`FEEDBACK_RETENTION_DAYS`) |
| The owner's `/note` text | same DB, table `notes` | last 50 (`NOTES_KEPT`). Shown in `/feedback` only, **never sent to an AI model** |
| Config changes requested in the bot: change id, time, fixed-format summary, status (no secrets, no chat ids) | same DB, table `config_changes` | last 100 (`CONFIG_CHANGES_KEPT`) |
| Live topics file and its backups (approved chat config edits, `/undo`) | `~/.config/mybuddy/config/topics.yaml` and `~/.config/mybuddy/config/backups/` (files mode 600) | newest 20 backups (`BACKUPS_KEPT`) |
| Service logs | systemd journal | journald defaults (size-capped, rotated). Chat ids appear only as the last 3 digits; message text is never logged |
| Latest `reports.json` | `~/app/site/data/` | overwritten on every report |

**Feedback votes are not anonymous to anyone with the database.** The voter column is
`sha256(salt:chat_id)`, which only stops one person voting twice on the same report. The salt is
stored in the same database (table `snapshots`, key `feedback:salt`) next to the `subscribers`
chat ids, so anyone who can read `mybuddy.db` (the owner, or someone who gets the file) can hash
every subscriber's chat id and see who voted what. Treat votes as linkable to chat ids.

## What leaves the server

| To | What | Why |
| --- | --- | --- |
| Groq (`api.groq.com`) | For each AI report: up to 12 news titles + snippets and the prompt. For each chat question (owner and `CHAT_ALLOWLIST` chats only): the question text plus the stored text of the latest reports (up to ~6,000 characters) as context. No chat ids, names, notes or votes. | summarising, answering chat |
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

  Their votes (if any) stay in `feedback` under the salted hash and expire after 90 days;
  chat-usage rows expire after 3 days.

* **Everything on the server:** `sudo systemctl stop mybuddy mybuddy-bot mybuddy-publish.path`,
  then `sudo rm -rf /home/mybuddy/.local/share/mybuddy /home/mybuddy/.config/mybuddy`.
  Terminating the instance in the Oracle console deletes the boot volume as well (tick
  *Permanently delete the attached boot volume*).
* **The public website data:** reports contain only public news. Old versions stay in the
  `data` branch history; removing them needs a rewrite of that branch (not `main`).
* **Groq / Telegram:** revoke the key or token (see [SECURITY.md](../SECURITY.md#rotating-keys));
  data already sent follows their policies.
