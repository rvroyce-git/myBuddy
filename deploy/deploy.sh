#!/usr/bin/env bash
# Copy MyBuddy from this laptop to the Oracle VM, install it, and (re)start the service.
# Usage: MYBUDDY_HOST=<vm-ip> bash deploy/deploy.sh
set -euo pipefail

HOST=${MYBUDDY_HOST:?Set MYBUDDY_HOST to the VM public IP}
KEY=${MYBUDDY_SSH_KEY:-$HOME/.ssh/mybuddy}
SSH=(ssh -i "$KEY" -o BatchMode=yes)
ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "==> Copying code to mybuddy@$HOST:~/app"
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" \
  --exclude .venv --exclude .git --exclude '__pycache__' --exclude '.*_cache' \
  --exclude /data --exclude /site/data/reports.json \
  "$ROOT"/ "mybuddy@$HOST:app/"

echo "==> Installing Python package on the VM"
"${SSH[@]}" "mybuddy@$HOST" bash -s <<'EOF'
set -euo pipefail
cd ~/app
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .
install -d -m 700 ~/.local/share/mybuddy ~/.config/mybuddy
[ -f site/data/reports.json ] || cp site/data/reports.sample.json site/data/reports.json
.venv/bin/mybuddy list
EOF

echo "==> Installing and restarting the systemd service (admin user)"
"${SSH[@]}" "ubuntu@$HOST" bash -s <<'EOF'
set -euo pipefail
sudo install -m 644 /home/mybuddy/app/deploy/mybuddy.service /etc/systemd/system/mybuddy.service
sudo systemctl daemon-reload
sudo systemctl enable --now mybuddy
sudo systemctl restart mybuddy
sleep 3
systemctl --no-pager --lines=5 status mybuddy || true
EOF
echo "==> Done"
