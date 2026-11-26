# Walkthrough: three security mechanisms, explained with the code

Manager feedback item 8. Each section says what the code does, why, where it lives, and how to
demo it. Line numbers are as of 26 Nov 2026.

## 1. HTTP redirects and credential stripping

**File:** `src/mybuddy/http.py`. All outbound traffic (news sources, Groq, Telegram, GitHub)
goes through `UrllibHttpClient`; nothing else in the app opens network connections.

**The problem.** Python's `urllib` follows redirects automatically and copies the request's
headers to the new location. Our requests carry secrets in headers (`Authorization: Bearer
<GROQ_API_KEY>`). A compromised or misconfigured server could answer `302 Location:
https://evil.example/` and urllib would hand the key to that host. A redirect could also lead to
plain `http://`, to an internal address (`https://169.254.169.254/`, the cloud metadata service)
or to a host we never meant to talk to (SSRF).

**What the code does.**

* `UrllibHttpClient.__init__` (line 201) builds the opener with our own redirect handler:
  `urllib.request.build_opener(_HttpsOnlyRedirect(self.allowed_hosts))`.
* `_HttpsOnlyRedirect.redirect_request` (lines 154-178) runs for every redirect hop, *before*
  urllib connects to the new location:
  1. `_require_https(newurl, self.allowed_hosts)` (line 171) raises `HttpError` unless the new
     URL is `https`, has no spaces/control characters, its host is not an IP literal (including
     shorthand forms like `127.1` or `2130706433`, see `_is_ip_literal`, line 50) and the host
     is in the allowlist (exact match after lowercasing and dropping a trailing dot).
  2. It lets urllib build the new request (`super().redirect_request`), then, if the host
     changed (`url_host(newurl) != url_host(req.full_url)`), removes every header in
     `_CREDENTIAL_HEADERS = ("Authorization", "Cookie", "Proxy-authorization")` (line 25). The odd
     capitalisation is deliberate: urllib stores header names with `str.capitalize()`, so
     `"Proxy-Authorization"` is kept internally as `"Proxy-authorization"`; `remove_header`
     needs that exact key.
  3. A same-host redirect (e.g. `api.groq.com/a` → `api.groq.com/b`) keeps the key, so normal
     API behaviour still works.
* Belt and braces: `request()` checks the first URL (line 226) and the final URL after all
  redirects (`resp.geturl()`, line 233).
* Other limits on the same client: 20 s socket timeout, a total deadline of 3 × that for slow-drip
  responses, a 5 MB body cap, and URLs in errors/logs are redacted by `_safe_url` (Telegram bot
  tokens and Discord webhook tokens never reach the journal).

Known limit: "same host" compares host names only, so a redirect to the same host on another
port would keep the header; such a redirect still has to be `https` to an allowlisted host.

**Demo.**

```bash
PYTHONPATH=src .venv/bin/pytest -v -k redirect tests/test_hardening.py tests/test_allowlist.py tests/test_engine_http.py
PYTHONPATH=src .venv/bin/python - <<'EOF'
import urllib.request
from email.message import Message
from mybuddy.http import _HttpsOnlyRedirect, HttpError
h = _HttpsOnlyRedirect(frozenset({"api.groq.com", "cdn.example"}))
req = urllib.request.Request("https://api.groq.com/x", headers={"Authorization": "Bearer k"})
print(h.redirect_request(req, None, 302, "Found", Message(), "https://cdn.example/").header_items())   # no Authorization
print(h.redirect_request(req, None, 302, "Found", Message(), "https://api.groq.com/y").header_items()) # kept
for bad in ("http://cdn.example/", "https://evil.example/", "https://127.1/"):
    try: h.redirect_request(req, None, 302, "Found", Message(), bad)
    except HttpError as e: print("refused:", e)
EOF
```

## 2. Subscription links: permanent bits and the `t` + hex payload

**Files:** `src/mybuddy/subscriptions.py` (shared logic), `src/mybuddy/bot.py` (uses it),
`config/topics.yaml` (`bit:` per topic), `src/mybuddy/config.py` (validation),
`src/mybuddy/export.py` line 99 (publishes `"bit"` in `reports.json`), `site/assets/app.js`
lines 1236-1280 and 1337-1351 (website side). Contract: `CONTRACT.md`, *Subscriptions contract*.

**The problem.** A website visitor picks topics, then must open Telegram and tap *Start*;
Telegram passes only a short `start` parameter (max 64 characters, `A-Z a-z 0-9 _ -`) to the bot.
The link must encode the chosen topics compactly, be safe to parse from untrusted input, and keep
meaning the same thing forever (links get shared, bookmarked and printed as QR codes).

**Design.**

* **Permanent bits.** Every website topic (`ai: true`) has a fixed `bit:` in `topics.yaml`
  (`ai_news 0`, `security 1`, `github_trending 2`, `deep_dive 3`, `research_papers 4`,
  `startups 5`, `ai_models 6`). Bits are never changed or reused, so reordering or removing a
  topic never changes what an old link means (`test_reordering_topics_never_changes_a_payload`,
  `test_removing_a_topic_never_shifts_other_payloads`). `config.py` rejects a missing bit, a
  bit outside 0..59 (`MAX_TOPIC_BIT`, line 24), a duplicate bit, and a bit on a non-website topic.
* **Encoding** (`encode_payload`, line 71): `mask = OR of (1 << bit)` (`mask_for`, line 42),
  payload = `"t" + format(mask, "x")`. AI news + security = `0b11` → `t3`; all seven = `0x7f` →
  `t7f`. An empty selection is an error.
* **Decoding** (`decode_payload`, line 78): the payload must fully match `t([0-9a-f]{1,15})`
  (`_PAYLOAD_RE`, line 22): lowercase only, at most 15 hex digits (60 bits, matching bits 0..59),
  so `int(..., 16)` is bounded and cheap. `topics_for_mask` (line 56) returns `None` when the mask
  sets any bit no current topic owns (`mask & ~full_mask(bits)`), and an empty mask (`t0`) is
  rejected too. Anything invalid → the bot shows the topic picker instead of guessing
  (`bot.py` line 276-281). Leading zeros are accepted (`t003` = `t3`).
* **The picker reuses the same format:** inline buttons carry `tog:<hex>:<topic>` and
  `save:<hex>` (`_TOGGLE_RE`/`_SAVE_RE`, `bot.py` lines 50-51, ≤ 64 bytes as Telegram requires),
  parsed with the same strict `parse_hex_mask` (line 63), so the bot keeps no per-chat picker
  state.
* **Website side** (`app.js`): `payload()` (line 1268) builds the hex string *nibble by nibble*
  (4 bits per hex digit) because JavaScript's bitwise operators work on 32-bit integers and
  `1 << 40` would overflow; this keeps all 60 bits exact. The bit comes from `reports.json`; for
  older data without `bit` it falls back to `TOPIC_BITS` (line 1252, same values as the YAML).
  `topicBits()` (line 1338) hides a topic whose bit is missing, out of range, or shared by two
  topics, so the website can never produce a link that means something else.
* **Limits:** at most 60 website topics ever (bits 0..59); payload ≤ 16 characters; at most
  `MYBUDDY_MAX_SUBSCRIBERS` (default 100) active subscribers (owner always allowed); per chat at
  most 10 commands and 40 button taps per minute (`FLOOD_LIMIT`, `CALLBACK_FLOOD_LIMIT`).

**Demo.**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
from pathlib import Path
from mybuddy.config import load_topics
from mybuddy.subscriptions import topic_bits, encode_payload, decode_payload
bits = topic_bits(load_topics(Path("config/topics.yaml")))
print(bits)
print(encode_payload(["ai_news", "security"], bits), encode_payload(list(bits), bits))  # t3 t7f
for p in ("t3", "t7f", "t80", "T3", "t0", "t003", "t" + "f" * 16):
    print(p[:20], "->", decode_payload(p, bits))   # None = invalid, the bot shows the picker
EOF
PYTHONPATH=src .venv/bin/pytest -q tests/test_subscriptions.py
```

On the website: open the *Daily updates* section, pick AI news and Cybersecurity; the button
and QR code point to `https://t.me/myBuddy_update_bot?start=t3`.

## 3. systemd hardening

**Files:** `deploy/mybuddy.service` (scheduler, `mybuddy serve`), `deploy/mybuddy-bot.service`
(Telegram bot), `deploy/mybuddy-publish.service` (+ `.path`; pushes `reports.json` to the `data`
branch). The idea: even if an attacker gets code execution inside a service (a parser bug in a
feed library, a malicious dependency update), the process can do very little.

| Directive | What it does | Protects against | sched. | bot | publish |
| --- | --- | --- | --- | --- | --- |
| `User=mybuddy` `Group=mybuddy` | run as the unprivileged app user | root compromise; mybuddy has no sudo | ✓ | ✓ | ✓ |
| `UMask=` | default permission of new files | `0077`: DB, logs and temp files private to mybuddy (reports.json is `chmod 644` explicitly by the app). Publish uses `0022`: a normal git checkout of public data | 0077 | 0077 | 0022 |
| `NoNewPrivileges=true` | no process can gain privileges via setuid/setgid binaries or file capabilities | `sudo`, `su`, setuid exploits after a compromise | ✓ | ✓ | ✓ |
| `CapabilityBoundingSet=` (empty) | removes every Linux capability, permanently | anything needing root-like powers (raw sockets, chown, binding low ports, loading BPF …) | ✓ | ✓ | ✓ |
| `PrivateTmp=true` | own empty `/tmp` and `/var/tmp` | temp-file races/symlink attacks; leaking temp files to other users | ✓ | ✓ | ✓ |
| `PrivateDevices=true` | minimal `/dev` (null, zero, random, tty …), no disks or `/dev/mem` | raw disk reads, hardware access | ✓ | ✓ | ✓ |
| `ProtectSystem=` | `strict`: the whole filesystem read-only except `ReadWritePaths`; `full`: `/usr`, `/boot`, `/etc` read-only | changing system binaries or config (`/etc/cron.d`, `/etc/passwd`) for persistence | strict | strict | full |
| `ProtectHome=read-only` | `/home`, `/root`, `/run/user` read-only | a compromised app rewriting its own code/venv, its env file, or adding a key to `~/.ssh/authorized_keys` | ✓ | ✓ | – |
| `ReadWritePaths=` | the only writable places | limits damage to the app's own data | data dir + `site/data` | data dir | – |
| `ProtectKernelTunables=true` | `/proc/sys`, `/sys` read-only | changing kernel settings (sysctl) | ✓ | ✓ | ✓ |
| `ProtectKernelModules=true` | no module loading | loading a rootkit module | ✓ | ✓ | ✓ |
| `ProtectKernelLogs=true` | no access to the kernel log buffer | reading kernel addresses/secrets from `dmesg` | ✓ | ✓ | ✓ |
| `ProtectControlGroups=true` | cgroup tree read-only | escaping its own `MemoryMax`/`CPUQuota` limits | ✓ | ✓ | ✓ |
| `ProtectClock=true` | cannot set the system clock | breaking TLS certificate checks, the schedule, logs | ✓ | ✓ | ✓ |
| `ProtectHostname=true` | cannot change the hostname | confusing logs and monitoring | ✓ | ✓ | ✓ |
| `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX` | only IP and local sockets | packet sniffing/spoofing (`AF_PACKET`), netlink tricks, exotic protocol bugs | ✓ | ✓ | ✓ |
| `RestrictNamespaces=true` | cannot create namespaces | a common first step of kernel exploits (user namespaces) | ✓ | ✓ | ✓ |
| `RestrictRealtime=true` | no realtime scheduling | starving the single vCPU (denial of service) | ✓ | ✓ | ✓ |
| `RestrictSUIDSGID=true` | cannot create setuid/setgid files | planting a privilege-escalation binary | ✓ | ✓ | ✓ |
| `LockPersonality=true` | cannot change the execution domain (`personality(2)`) | legacy modes such as READ_IMPLIES_EXEC that weaken memory protection | ✓ | ✓ | ✓ |
| `MemoryDenyWriteExecute=true` | no memory that is both writable and executable | injected shellcode / runtime-generated machine code | ✓ | ✓ | – |
| `SystemCallFilter=@system-service` | allowlist of syscalls normal services use; anything else kills the process (SIGSYS) | kernel attack surface: `mount`, `reboot`, `kexec_load`, `init_module`, `ptrace`, `swapon`, clock changes … | ✓ | ✓ | – |
| `SystemCallArchitectures=native` | only x86_64 syscalls | bypassing the filter through the 32-bit syscall ABI | ✓ | ✓ | – |
| `MemoryMax=` / `CPUQuota=` | resource caps for the unit's cgroup | a runaway feed or leak freezing the 1 GB VM; the OOM killer stays inside the unit | 300M / 50% | 150M / 25% | – |

Other unit settings: `Restart=on-failure` with `RestartSec=30` (scheduler, bot) recovers from
crashes without a tight loop; the bot's `RestartPreventExitStatus=78` stops restarts when
`TELEGRAM_BOT_TOKEN` is missing (`EX_CONFIG` in `cli.py`) and `TimeoutStopSec=45` lets a 25 s
long poll finish. Publish is `Type=oneshot`, started by `mybuddy-publish.path` when
`reports.json` changes, waits 20 s (`ExecStartPre=/bin/sleep 20`) so a burst of reports becomes
one commit, and is killed after `TimeoutStartSec=180`.

**Why the units differ.**

* The **bot** needs less than the scheduler: it only writes its SQLite DB, so `ReadWritePaths`
  has just the data dir and its memory/CPU caps are half.
* The **scheduler** also writes `site/data/reports.json`, hence the second writable path.
* The **publisher** runs `bash`, `git` and the OpenSSH client instead of our Python. git and ssh
  write in the home directory (`~/data-repo`, `~/.ssh/known_hosts` via
  `StrictHostKeyChecking=accept-new`), so the unit uses `ProtectSystem=full` and leaves home
  writable (the unit file says so). `MemoryDenyWriteExecute`, the syscall filter and resource caps
  are not set, to avoid breaking those tools; the run is short and bounded by the 180 s timeout.
  Possible future tightening: `ProtectSystem=strict` with
  `ReadWritePaths=/home/mybuddy/data-repo /home/mybuddy/.ssh`, then test a real publish.

**Demo (on the VM).**

```bash
systemd-analyze security mybuddy mybuddy-bot mybuddy-publish    # exposure score per unit (lower = safer)
systemctl show mybuddy -p ProtectSystem -p ProtectHome -p NoNewPrivileges -p MemoryMax
# The same sandbox in a throwaway unit: writing the app code is refused.
sudo systemd-run -q --wait --pipe -p User=mybuddy -p ProtectSystem=strict -p ProtectHome=read-only \
  touch /home/mybuddy/app/x          # -> touch: cannot touch ...: Read-only file system
sudo systemd-run -q --wait --pipe -p User=mybuddy -p NoNewPrivileges=true sudo -n true
                                     # -> sudo: The "no new privileges" flag is set ...
```
