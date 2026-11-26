# Contributing

## Workflow

`main` is protected by a ruleset: **no direct pushes**, changes land only through a pull request,
merged with **squash** (linear history, no force pushes), after the required checks pass.

1. Branch from an up-to-date `main`: `git switch main && git pull && git switch -c short-topic-name`.
2. Make a focused change (one feature or fix per PR) with tests.
3. Run the checks below locally.
4. Push the branch and open a PR; fill in the template (what, why, tests, checklist).
5. Required checks (`.github/workflows/ci.yml`):
   * `test`: ruff lint, ruff format, mypy (strict), backend pytest with coverage
   * `ui`: Playwright browser tests of the website (`tests/ui`)
   * `secrets`: gitleaks over the **full** git history
6. Squash-merge once green. The PR title becomes the commit subject on `main`, so make it a good
   one.

The server pushes `reports.json` to the separate `data` branch; never commit generated
`site/data/reports.json` to `main`.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements/dev.txt && .venv/bin/pip install --no-deps -e .
.venv/bin/pre-commit install          # gitleaks, ruff lint + format, YAML/TOML/whitespace on every commit
```

Fix what a hook reports; do not bypass hooks with `--no-verify`.

## Run the checks

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --ignore=tests/ui                 # backend, offline (fakes in tests/conftest.py)
.venv/bin/python -m playwright install chromium     # once
.venv/bin/pytest tests/ui                           # website
MYBUDDY_FAKE=1 .venv/bin/mybuddy run all --dry-run  # real feeds, no AI, no delivery
```

Tests never use the real network. New behaviour needs offline unit tests. Shell scripts in
`deploy/` are checked by `tests/test_deploy_scripts.py` (`bash -n`, and shellcheck when it is
installed: `brew install shellcheck` / `apt-get install shellcheck`).

## Rules that keep MyBuddy safe and free

* Free tiers only (see [docs/costs.md](docs/costs.md)). Anything that calls Groq shares one free
  quota with the morning reports.
* Runtime dependencies are `feedparser` and `PyYAML` only. Prefer the standard library; a new
  dependency must be small, pure Python, justified in the PR, and added to `pyproject.toml` and
  both lock files.
* Every outbound host must be allowlisted (`src/mybuddy/allowlist.py`, `SOURCE_HOSTS` in
  `src/mybuddy/sources/__init__.py`).
* Feed text and user messages are data, never instructions; no LLM gets shell/file/network tools.
* Secrets only in the server's env file; never in code, tests (use obviously fake values,
  allowlisted in `.gitleaks.toml`), logs, issues or PR text.

## Updating pinned dependencies

`pyproject.toml` has compatible ranges; CI and the server install the exact hash-pinned versions
in `requirements/runtime.txt` and `requirements/dev.txt`. Regenerate both with pip-tools from a
throwaway Python 3.12 venv, check the Linux x86_64 resolution, and commit both files in one PR.
The exact commands are in [README.md](README.md#updating-pinned-dependencies). Dependabot opens
weekly PRs for `requirements/` and for SHA-pinned GitHub Actions; review them like any PR.

## Commit messages

```text
Short imperative subject, at most ~72 characters

Why the change is needed and what it does, wrapped at ~72 columns. Mention
settings added (with defaults), behaviour changes and anything the owner must
do on the server.
```

Examples: `Add arXiv retry on 5xx`, `Fix Telegram split at 4096 chars`. One logical change per
commit; with squash merges, the PR title and description matter most.
