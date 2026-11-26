# data branch

Only `reports.json` lives here. The MyBuddy server updates it with its own deploy key after
each report. The website is rebuilt by `.github/workflows/pages.yml` on `main` (scheduled every
10 minutes), which reads this file as plain data — no code on this branch is ever run.
