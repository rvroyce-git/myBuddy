# Costs

MyBuddy is designed to cost **nothing**. Every part runs on a free tier:

| Part | Service | Free-tier terms that matter |
| --- | --- | --- |
| Server | Oracle Cloud **Always Free** VM.Standard.E2.1.Micro (1 GB RAM, x86_64), Ubuntu 24.04, Hyderabad, personal account | Free for as long as the account exists. Always Free also includes 200 GB of block storage in total (the boot volume uses ≤ 50 GB) and outbound data far beyond what MyBuddy sends. |
| AI summaries | Groq API, `openai/gpt-oss-120b` | ≈ 30 requests/min, 8K tokens/min, 1K requests/day, 200K tokens/day |
| Delivery | Telegram Bot API | free |
| Code, CI, website | GitHub public repository: Actions and Pages | free for public repositories |
| News sources | Hugging Face, GitHub, arXiv, CISA, Hacker News, Reddit, Google News and RSS feeds | public, no keys |
| Optional | Gemini (fallback AI), Discord webhook | free tiers, off unless a key/URL is set |

## How much of the free quota MyBuddy uses

* **Groq** (free: ≈ 30 requests/min, 8K tokens/min, 1K requests/day, 200K tokens/day, shared
  by everything below).
  * *Scheduled reports:* at most **11 AI calls a day** (the 7 website topics plus 4
    Telegram-only reports; the weekly review takes the deep dive's slot on Sundays), plus at
    most one retry each after a 429, so ≤ 22 requests. Each call sends at most 12 items
    (titles + 280-character snippets, ≈ 2.5K tokens in) and asks for at most 2,000 output
    tokens (`MAX_AI_ITEMS`, `SNIPPET_CHARS`, `MAX_TOKENS` in `src/mybuddy/summarize.py`):
    **≈ 40–50K tokens a day**. Hourly CVE alerts and the health check use no AI.
  * *Chat* (owner + `CHAT_ALLOWLIST` only): by default **2 messages/min and 30/day per
    person**, and a global **60K tokens/day** chat budget (`CHAT_DAILY_TOKEN_BUDGET`; it cannot
    be set above 80K, from the env file or `/limit`). Chat pauses for **±2 minutes around each
    scheduled report run**, so a report never shares a minute with chat.
  * *Total:* ≈ 50K (reports) + at most 80K (chat) stays **under the 200K tokens/day** free
    limit, with room for retries. Per minute: reports are 15 minutes apart and one call is at
    most ≈ 4.5K tokens; one chat answer is at most ≈ 2.5K tokens (≈ 6,000 characters of report
    context + the question, 700 output tokens), so even 2 chat messages in a minute stay under
    8K tokens/min, and the quiet window keeps them apart from report calls.
  * Any Groq error (including running out of quota) falls back to a plain report built from
    the snippets, or a "try later" chat reply: nothing is ever paid for, and reports still
    arrive. Any new feature that calls Groq must fit inside these numbers.
* **Oracle.** One micro VM. Services are capped (`MemoryMax=300M`/`CPUQuota=50%` for the
  scheduler, `150M`/`25%` for the bot). Oracle may stop Always Free instances it considers
  idle; the `Watchdog` workflow emails the owner if the website gets no fresh reports for 26 h.
* **GitHub.** CI on pushes and PRs; the *Publish website* check every 10 minutes (a few seconds when nothing changed, a full build only when new reports arrived); one watchdog run a day. All free for a public repository.

## Rules that keep it free

1. **Never upgrade the Oracle account to Pay As You Go.** An account that was never upgraded is not
   billed: once the free trial ends it can only create Always Free resources.
   `deploy/create-vm.sh` refuses anything but the Always Free shapes either way.
2. Use only VM.Standard.E2.1.Micro (or A1.Flex within the free allowance), boot volume ≤ 50 GB,
   no paid add-ons (extra block volumes beyond 200 GB in total, paid shapes, etc.).
3. Keep the GitHub repository public (Actions minutes and Pages are free for public repos).
4. No new paid APIs. New features must fit in the Groq free quota above.
5. Groq usage limit: in console.groq.com > *Settings* > *Limits* (or *Billing*), set a hard
   spend/usage limit. The free tier has no billing attached, so nothing can be charged today; but
   if a paid (Developer tier) key is ever used, set a low monthly spend limit there first so a
   leaked key or runaway chat cannot run up a bill.

## Safety net: a 1-unit budget alert

A budget does not stop spending; it emails you. Set to 1 unit of your account currency with an
alert at 1% *actual* spend, it fires on the first paisa/cent ever charged, which on this account
should be never. It is a cheap insurance against an accidental upgrade or a mistaken resource.
Budgets themselves are free.

**Script** (on your machine, `oci` configured; your email is passed in, never stored in git):

```bash
bash deploy/budget-alert.sh --email you@example.com --dry-run   # show the oci commands
bash deploy/budget-alert.sh --email you@example.com             # create (idempotent)
```

**Console instead:** *Billing & Cost Management > Budgets > Create Budget*:

1. Compartment: the **root** compartment (your tenancy name). Name `mybuddy-safety-net`.
2. Target: *Compartment*, the root compartment (covers everything).
3. Schedule: monthly. Budget amount: `1`.
4. Alert rule: threshold metric **Actual spend**, threshold type **Percentage of budget**,
   value `1`, email recipient = your address, message e.g. "Oracle is charging money".
5. Create. To check what (if anything) is being charged: *Billing & Cost Management > Cost
   Analysis*.

If the console says budgets are not available for your account type, nothing is lost: the
account still cannot be charged unless it is upgraded.
