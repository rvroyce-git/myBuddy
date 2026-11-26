#!/usr/bin/env bash
# Runs on the VM as the mybuddy user whenever site/data/reports.json changes (see
# mybuddy-publish.path). Commits only reports.json to the repository's `data` branch; the
# scheduled "Publish website" workflow on `main` then rebuilds the website (within ~10 min).
# The `main` branch is never touched. Uses its own deploy key (~/.ssh/github_data_deploy);
# branch protection stops that key from writing `main` (see SECURITY.md for what it CAN write).
# GitHub's host keys are pinned in ~/.ssh/github_known_hosts (copied by deploy.sh from
# deploy/github_known_hosts): an unknown or changed host key makes the push fail.
set -euo pipefail

REPO_URL=${MYBUDDY_PAGES_REPO:-git@github.com:rvroyce-git/myBuddy.git}
BRANCH=${MYBUDDY_DATA_BRANCH:-data}
REPO_DIR=$HOME/data-repo
SRC=$HOME/app/site/data/reports.json
KEY=${MYBUDDY_DATA_KEY:-$HOME/.ssh/github_data_deploy}
KNOWN_HOSTS=${MYBUDDY_GITHUB_KNOWN_HOSTS:-$HOME/.ssh/github_known_hosts}
[ -s "$KNOWN_HOSTS" ] || { echo "missing $KNOWN_HOSTS (run deploy/deploy.sh)" >&2; exit 1; }
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$KNOWN_HOSTS -o StrictHostKeyChecking=yes"

[ -f "$SRC" ] || { echo "no $SRC yet"; exit 0; }
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$SRC"   # never publish broken JSON
[ -d "$REPO_DIR/.git" ] || git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
cd "$REPO_DIR"

# Ask GitHub to run main's trusted "Publish website" workflow right away, instead of waiting for
# its (often delayed) schedule. Needs a fine-grained token limited to this repository with only
# "Actions: Read and write" — it can start workflows but cannot change code or files.
# Optional: without GH_DISPATCH_TOKEN the scheduled run still publishes, just later.
request_rebuild() {
  local token=${GH_DISPATCH_TOKEN:-}
  [ -n "$token" ] || { echo "no GH_DISPATCH_TOKEN; the scheduled build will publish"; return 0; }
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -X POST \
    -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${MYBUDDY_REPO:-rvroyce-git/myBuddy}/actions/workflows/pages.yml/dispatches" \
    -d '{"ref":"main"}') || code=000
  if [ "$code" = 204 ]; then echo "requested website rebuild"; else echo "rebuild request failed (HTTP $code); the scheduled build will publish" >&2; fi
}

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
    request_rebuild
    exit 0
  fi
  echo "push failed (attempt $attempt); retrying" >&2
  sleep $((attempt * 10))
done
exit 1
