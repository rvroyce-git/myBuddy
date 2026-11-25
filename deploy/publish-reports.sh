#!/usr/bin/env bash
# Runs on the VM as the mybuddy user whenever site/data/reports.json changes (see
# mybuddy-publish.path). Commits only reports.json to the repository's `data` branch; a
# workflow on that branch then rebuilds the website. The `main` branch is never touched.
# Uses its own deploy key (~/.ssh/github_data_deploy); branch protection on `main` stops
# that key from writing anywhere that matters.
set -euo pipefail

REPO_URL=${MYBUDDY_PAGES_REPO:-git@github.com:rvroyce-git/myBuddy.git}
BRANCH=${MYBUDDY_DATA_BRANCH:-data}
REPO_DIR=$HOME/data-repo
SRC=$HOME/app/site/data/reports.json
KEY=${MYBUDDY_DATA_KEY:-$HOME/.ssh/github_data_deploy}
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

[ -f "$SRC" ] || { echo "no $SRC yet"; exit 0; }
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$SRC"   # never publish broken JSON
[ -d "$REPO_DIR/.git" ] || git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
cd "$REPO_DIR"

for attempt in 1 2 3; do
  git fetch -q --depth 1 origin "$BRANCH"
  git reset -q --hard "origin/$BRANCH"
  cp "$SRC" reports.json
  if git diff --quiet -- reports.json; then
    echo "reports.json unchanged; nothing to publish"
    exit 0
  fi
  git -c user.name="MyBuddy server" -c user.email="mybuddy-server@users.noreply.github.com" \
    commit -q -m "Update reports $(date '+%Y-%m-%d %H:%M %Z')" -- reports.json
  if git push -q origin "HEAD:$BRANCH"; then
    echo "published reports.json to $BRANCH (attempt $attempt)"
    exit 0
  fi
  echo "push failed (attempt $attempt); retrying" >&2
  sleep $((attempt * 10))
done
exit 1
