#!/usr/bin/env bash
# Run this YOURSELF in your own Terminal. It asks for your keys with hidden typing and saves
# them on the VM in a private file only the app user can read. Press Enter to keep a value
# that is already saved. Secrets never appear on screen, in shell history, or in any chat.
# Usage: MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh
set -euo pipefail

HOST=${MYBUDDY_HOST:?Set MYBUDDY_HOST to the VM public IP}
KEY=${MYBUDDY_SSH_KEY:-$HOME/.ssh/mybuddy}

ask() {  # ask VAR "Prompt": hidden input; empty answer = keep the saved value
  local value
  read -rsp "$2 (hidden, Enter = keep current): " value; echo
  [ -n "$value" ] && printf '%s=%s\n' "$1" "$value"
  return 0
}

UPDATES=$(
  ask GROQ_API_KEY "Groq API key"
  ask TELEGRAM_BOT_TOKEN "Telegram bot token from @BotFather"
  ask DISCORD_WEBHOOK_URL "Discord webhook URL (optional)"
  ask GEMINI_API_KEY "Gemini API key (optional)"
)

if [ -z "$UPDATES" ]; then
  echo "Nothing entered; saved keys are unchanged."
  exit 0
fi

# Merge on the VM: replace keys that were entered, keep every other line as it is.
printf '%s\n' "$UPDATES" | ssh -i "$KEY" -o BatchMode=yes "mybuddy@$HOST" '
  set -e; umask 077; install -d -m 700 ~/.config/mybuddy
  staged=~/.config/mybuddy/.incoming; cat > "$staged"
  python3 - "$staged" ~/.config/mybuddy/env <<"PY"
import os, sys
staged, path = sys.argv[1], sys.argv[2]
updates = dict(ln.split("=", 1) for ln in open(staged).read().splitlines() if "=" in ln)
lines = open(path).read().splitlines() if os.path.exists(path) else []
kept = [ln for ln in lines if ln.split("=", 1)[0] not in updates]
with open(path, "w") as f:
    f.write("\n".join(kept + [f"{k}={v}" for k, v in updates.items()]) + "\n")
os.chmod(path, 0o600)
print("Saved:", ", ".join(sorted(updates)), "-> ~/.config/mybuddy/env (mode 600)")
PY
  rm -f "$staged"'

unset UPDATES
echo "Tell Claude it's done; it will finish the setup and restart MyBuddy."
