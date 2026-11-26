#!/usr/bin/env bash
# Run this YOURSELF. Saves a GitHub fine-grained token (this repo only, permission
# "Actions: Read and write" only) on the VM so each new report triggers the website build at once.
# The token is typed hidden and never shown, logged or stored in the repo.
# Usage: MYBUDDY_HOST=<vm-ip> bash deploy/set-dispatch-token.sh
set -euo pipefail
HOST=${MYBUDDY_HOST:?Set MYBUDDY_HOST to the VM public IP}
KEY=${MYBUDDY_SSH_KEY:-$HOME/.ssh/mybuddy}
read -rsp "GitHub token (hidden): " TOKEN; echo
[ -n "$TOKEN" ] || { echo "Nothing entered; unchanged."; exit 0; }
printf 'GH_DISPATCH_TOKEN=%s\n' "$TOKEN" | ssh -i "$KEY" -o BatchMode=yes "mybuddy@$HOST" \
  'umask 077; install -d -m 700 ~/.config/mybuddy; cat > ~/.config/mybuddy/dispatch.env; chmod 600 ~/.config/mybuddy/dispatch.env; echo "Saved ~/.config/mybuddy/dispatch.env (mode 600)"'
unset TOKEN
