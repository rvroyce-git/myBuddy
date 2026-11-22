#!/usr/bin/env bash
# Runs on the VM as the mybuddy user whenever site/data/reports.json changes (see
# mybuddy-publish.path). Commits only that file to GitHub; GitHub Pages then republishes.
# Uses a deploy key that can write to this one repository and nothing else.
set -euo pipefail

REPO_URL=${MYBUDDY_PAGES_REPO:-git@github.com:rvroyce-git/myBuddy.git}
REPO_DIR=$HOME/pages-repo
SRC=$HOME/app/site/data/reports.json
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

[ -f "$SRC" ] || { echo "no $SRC yet"; exit 0; }
[ -d "$REPO_DIR/.git" ] || git clone -q --depth 1 "$REPO_URL" "$REPO_DIR"
cd "$REPO_DIR"

for attempt in 1 2 3; do
  git fetch -q --depth 1 origin main
  git reset -q --hard origin/main
  cp "$SRC" site/data/reports.json
  if git diff --quiet -- site/data/reports.json; then
    echo "reports.json unchanged; nothing to publish"
    exit 0
  fi
  git -c user.name="MyBuddy server" -c user.email="mybuddy-server@users.noreply.github.com" \
    commit -q -m "Update reports $(date '+%Y-%m-%d %H:%M %Z')" -- site/data/reports.json
  if git push -q origin HEAD:main; then
    echo "published reports.json (attempt $attempt)"
    exit 0
  fi
  echo "push failed (attempt $attempt); retrying" >&2
  sleep $((attempt * 10))
done
exit 1
