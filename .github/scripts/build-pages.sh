#!/usr/bin/env bash
# Build the GitHub Pages site from the code on `main` (site/) plus the latest reports from
# the `data` branch, and force-push it to `gh-pages`, which GitHub Pages serves.
# Usage: build-pages.sh <site-dir> <reports.json or "">   (needs GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA;
#        optional DATA_SHA, SITE_TREE are recorded as .data-sha / .site-tree)
set -euo pipefail

SITE_DIR=${1:?site dir}
REPORTS=${2:-}
out="$(mktemp -d)"
cp -R "$SITE_DIR"/. "$out/"
if [ -n "$REPORTS" ] && [ -s "$REPORTS" ]; then
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$REPORTS"   # refuse broken JSON
  cp "$REPORTS" "$out/data/reports.json"
else
  echo "no reports.json found; the site will show its sample data" >&2
fi
touch "$out/.nojekyll"   # serve files as they are (no Jekyll processing)
# Record what was published, so the scheduled run in pages.yml can tell whether the data
# branch or main's site/ changed since (it reads these from gh-pages and skips if equal).
printf '%s\n' "${DATA_SHA:-}" > "$out/.data-sha"
printf '%s\n' "${SITE_TREE:-}" > "$out/.site-tree"
cd "$out"
git init -q -b gh-pages
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "Publish website from ${GITHUB_SHA::7} (data ${DATA_SHA:0:7})"
git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" gh-pages
echo "published gh-pages"
