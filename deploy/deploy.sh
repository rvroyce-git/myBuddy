#!/usr/bin/env bash
# Copy MyBuddy from your machine to the Oracle VM, install it, and (re)start the services
# (mybuddy = scheduler, mybuddy-bot = Telegram subscription bot).
# Usage: MYBUDDY_HOST=<vm-ip> bash deploy/deploy.sh
set -euo pipefail

HOST=${MYBUDDY_HOST:?Set MYBUDDY_HOST to the VM public IP}
KEY=${MYBUDDY_SSH_KEY:-$HOME/.ssh/mybuddy}
SSH=(ssh -i "$KEY" -o BatchMode=yes)
ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "==> Copying code to mybuddy@$HOST:~/app"
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" \
  --exclude .venv --exclude .git --exclude .claude --exclude '__pycache__' --exclude '.*_cache' \
  --exclude /data --exclude /site/data/reports.json \
  "$ROOT"/ "mybuddy@$HOST:app/"

echo "==> Installing Python package on the VM"
"${SSH[@]}" "mybuddy@$HOST" bash -s <<'EOF'
set -euo pipefail
cd ~/app
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q --require-hashes -r requirements/runtime.txt   # exact, hash-checked versions
.venv/bin/pip install -q --no-deps -e .
install -d -m 700 ~/.local/share/mybuddy ~/.config/mybuddy ~/.config/mybuddy/config
[ -f site/data/reports.json ] || cp site/data/reports.sample.json site/data/reports.json
# The live topics file is a writable copy (the owner edits it from the bot with /approve),
# seeded once from the repo default. Later repo changes to config/topics.yaml are NOT copied
# over it: merge them by hand (or delete the copy to re-seed; backups/ keeps old versions).
LIVE=~/.config/mybuddy/config/topics.yaml
[ -f "$LIVE" ] || install -m 600 config/topics.yaml "$LIVE"
cmp -s config/topics.yaml "$LIVE" || echo "note: $LIVE differs from the repo's config/topics.yaml"
# Manual commands (mybuddy check, run ...) read the same file via the env file.
ENVF=~/.config/mybuddy/env
[ -f "$ENVF" ] || install -m 600 /dev/null "$ENVF"
grep -q '^MYBUDDY_CONFIG=' "$ENVF" || echo "MYBUDDY_CONFIG=$LIVE" >> "$ENVF"
.venv/bin/mybuddy list
EOF

echo "==> Installing and restarting the systemd services (admin user)"
"${SSH[@]}" "ubuntu@$HOST" bash -s <<'EOF'
set -euo pipefail
sudo install -m 644 /home/mybuddy/app/deploy/mybuddy.service /etc/systemd/system/mybuddy.service
sudo install -m 644 /home/mybuddy/app/deploy/mybuddy-bot.service /etc/systemd/system/mybuddy-bot.service
sudo install -m 644 /home/mybuddy/app/deploy/mybuddy-publish.service /etc/systemd/system/mybuddy-publish.service
sudo install -m 644 /home/mybuddy/app/deploy/mybuddy-publish.path /etc/systemd/system/mybuddy-publish.path
sudo systemctl daemon-reload
sudo systemctl enable --now mybuddy mybuddy-bot
# Publishing to GitHub Pages needs the server's deploy key added on GitHub first.
if sudo test -f /home/mybuddy/.ssh/github_data_deploy; then
  sudo systemctl enable --now mybuddy-publish.path
fi
sudo systemctl restart mybuddy mybuddy-bot
sleep 3
systemctl --no-pager --lines=5 status mybuddy mybuddy-bot || true
EOF
echo "==> Done"
