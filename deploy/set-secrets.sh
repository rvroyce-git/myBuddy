#!/usr/bin/env bash
# Run this YOURSELF in your own Terminal. It asks for your Groq key and Discord webhook
# with hidden typing and saves them on the VM in a private file only the app user can read.
# The secrets never appear on screen, in shell history, or in any chat.
# Usage: MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh
set -euo pipefail

HOST=${MYBUDDY_HOST:?Set MYBUDDY_HOST to the VM public IP}
KEY=${MYBUDDY_SSH_KEY:-$HOME/.ssh/mybuddy}

read -rsp "Groq API key (hidden): " GROQ; echo
read -rsp "Discord webhook URL (hidden, Enter to skip): " HOOK; echo
read -rsp "Gemini API key (optional, hidden, Enter to skip): " GEMINI; echo

{
  printf 'GROQ_API_KEY=%s\n' "$GROQ"
  [ -n "$HOOK" ] && printf 'DISCORD_WEBHOOK_URL=%s\n' "$HOOK"
  [ -n "$GEMINI" ] && printf 'GEMINI_API_KEY=%s\n' "$GEMINI"
  true
} | ssh -i "$KEY" -o BatchMode=yes "mybuddy@$HOST" \
  'umask 077; install -d -m 700 ~/.config/mybuddy; cat > ~/.config/mybuddy/env; chmod 600 ~/.config/mybuddy/env; echo "Saved to ~/.config/mybuddy/env (mode 600)"'

unset GROQ HOOK GEMINI
echo "Next: restart the service so it picks up the keys:"
echo "  ssh -i $KEY ubuntu@$HOST sudo systemctl restart mybuddy"
