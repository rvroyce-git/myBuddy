## What

<!-- One or two sentences: what this PR changes. -->

## Why

<!-- The problem or request it solves. Link issues. -->

## Tests

<!-- What you ran and what you added. Paste short results. -->

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest --ignore=tests/ui` (new behaviour has offline unit tests, no real network)
- [ ] `pytest tests/ui` (if `site/` changed)

## Checklist

- [ ] **Secrets:** no keys, tokens, webhook URLs, chat ids or IP addresses in code, tests, docs,
      logs or this description (pre-commit gitleaks passed; fake test values allowlisted).
- [ ] **Free tier:** no paid service. Extra Groq calls/tokens per day: <!-- none / estimate -->
      (shared free quota ≈ 1K requests and 200K tokens per day). Oracle VM memory/CPU impact:
      <!-- none / estimate -->.
- [ ] **Hosts:** every new outbound host is in the allowlist.
- [ ] **Untrusted input:** feed/chat text is treated as data; no new tools exposed to an LLM.
- [ ] **Dependencies:** none added, or justified and added to `pyproject.toml` + both lock files.
- [ ] **Settings / docs:** new env vars or config keys are documented with defaults
      (README, docs/).
- [ ] **Server:** owner actions needed after merge (redeploy, new secret, systemd change) are
      listed below.

## Owner actions after merge

<!-- "None", or exact steps. -->
