# Development guide

Personal app that finds, filters, ranks, and tracks full-time jobs so I can apply manually. See [`CLAUDE.md`](../CLAUDE.md) for rules and roadmap. **New here? Start with [`SETUP.md`](SETUP.md).**

## Prerequisites

- Docker Desktop (provides `docker compose`). If `docker` isn't found, add `export PATH="$HOME/.docker/bin:$PATH"` to `~/.zshrc`.
- Python 3.12+ for running the backend and tests locally.
- Node.js 24 LTS for the web app. Installed (no admin rights) in `~/.local/node`; add `export PATH="$HOME/.local/node/bin:$PATH"` to `~/.zshrc` so `node`/`npm` are found.

## First-time setup

```bash
cp .env.example .env            # then replace "change-me" with a real password (both places)
docker compose up -d db
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/alembic upgrade head
```

## Run

```bash
docker compose up -d --build api      # API at http://localhost:8000 (runs migrations on start)
curl localhost:8000/health            # {"status":"ok","database":"ok"}
```

Or locally with reload: `cd backend && .venv/bin/uvicorn app.main:app --reload`.
After changing backend code, rebuild the container: `docker compose up -d --build api`.

## Automatic updates (Phase 6)

```bash
docker compose up -d --build worker   # runs an update whenever one is due
docker compose logs -f worker         # watch it
cd backend && .venv/bin/python -m app.scheduler --once   # or run one update by hand
```

An update fetches every source in `config/companies.yaml`, `sheets.yaml` and `lists.yaml`, closes listings that are
gone, rescores, and records what's new (the Dashboard's "Latest update" card, with an **Update now** button).
How often: Settings → search frequency (default every 24 hours). The worker checks every 5 minutes, so if the Mac
was asleep or Docker was off, an overdue update runs soon after it's back. Only one update runs at a time.

Closing listings: employer boards (Greenhouse, Lever, Ashby) close their own postings when they stop listing them,
and a GitHub list can no longer reopen a posting the employer closed. Jobs found only through a list or sheet close
when a later successful fetch of that list leaves them out for 3 days; Adzuna-only jobs after 30 days; CSV imports
and pasted links never close automatically (`config/schedule.yaml`). Jobs I'm tracking (shortlisted, applied, ...)
stay visible with a "Listing closed" badge, and the digest lists them.

Adzuna is not part of scheduled updates unless `run_adzuna: true` in `config/schedule.yaml` (the call caps in
`adzuna.yaml` still apply).

## Web app

```bash
cd frontend
npm install          # first time only
npm run dev          # http://localhost:3000 (needs the API on :8000)
```

Pages: **Jobs to Apply To** (ranked Apply/Maybe list with filters, "Open application", status dropdown, CSV export),
**job detail** (why it ranked, score breakdown, flags with evidence, description, my tracking: status, date applied,
resume version, notes, history), **Dashboard**, **Apply hub**, **Settings** (pay, locations, titles, weights, the
facts only I fill in, and a "Rescore now" button). The browser only talks to Next.js; `/api/*` is proxied to
FastAPI (`BACKEND_URL`, default `http://127.0.0.1:8000`). Opening an application just opens the employer's page in a
new tab; nothing is filled in or submitted, and "Applied" is only set when I choose it.

CSV export opens in Excel/Sheets; cells starting with `=`, `+`, `-` or `@` are prefixed with `'` so they can't run
as formulas.

## Fetch jobs

```bash
cd backend
.venv/bin/python -m app.cli ingest all                         # every board in config/companies.yaml
.venv/bin/python -m app.cli ingest greenhouse stripe --name Stripe
.venv/bin/python -m app.cli ingest lever palantir --name Palantir
# or inside Docker:
docker compose exec api python -m app.cli ingest all
```

Each run is recorded in `ingestion_runs`. Re-running is safe: existing postings are updated, not duplicated. When a board stops listing a posting, the job is marked inactive (and reactivated if it comes back).

Google Sheets: add public ("anyone with the link") sheets to `config/sheets.yaml`, then `ingest sheets` (or `ingest sheet <url>` for a one-off). Rows whose link is hidden behind text are skipped and listed.

Public GitHub job lists (SimplifyJobs new grad, jobright-ai business analyst) are in `config/lists.yaml`; run `ingest lists`. Only raw GitHub files are fetched, never the list owners' websites.

Downloaded CSVs (e.g. SWE Job Lists' "Download CSV", which sits behind a CAPTCHA, so you download it yourself in the browser):

```bash
.venv/bin/python -m app.cli import-csv ~/Downloads/swejoblists-2026-11-05.csv
```

Pasted links (Reddit, LinkedIn, Indeed, Workday, anything):

```bash
# Greenhouse / Lever links: details (and published pay ranges) come from the official API
.venv/bin/python -m app.cli add-link "https://boards.greenhouse.io/acme/jobs/123"
# Any other site is never fetched; give the company and title yourself
.venv/bin/python -m app.cli add-link "https://www.linkedin.com/jobs/view/123" --company Pfizer --title "Supply Chain Analyst" --location "New York, NY"
# Record the Reddit post you found it in (Reddit is not contacted)
.venv/bin/python -m app.cli add-link "<employer link>" --via "https://www.reddit.com/r/.../comments/abc123/..."
# Pick job links out of a pasted post
pbpaste | .venv/bin/python -m app.cli find-links
```

Add companies in `config/companies.yaml`. The board token is the slug in `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>` or `jobs.ashbyhq.com/<token>`.

Apply hub: `.venv/bin/python -m app.cli hub` prints pre-filtered LinkedIn/Indeed searches, job sites and employer career sites the app doesn't fetch. Edit `config/apply_hub.yaml`.

## Score and rank

```bash
.venv/bin/python -m app.cli score              # read pay/experience/flags from postings, estimate pay, score everything
.venv/bin/python -m app.cli top --n 25         # ranked Apply/Maybe list with reasons and links
.venv/bin/python -m app.cli top --sample       # a slice across score bands, for tuning weights
```

Scoring needs `data/private/profile.yaml` (your skills, from your resume). Tuning: `config/scoring.yaml`; skill vocabulary: `config/skills.yaml`; category weights: Settings (`user_settings.scoring_weights`). Scores are match scores, not probabilities of an interview or offer.

## Test

```bash
docker compose up -d db
cd backend && .venv/bin/pytest
```

Tests drop and recreate a separate `jobs_test` database on every run; the dev database is never touched.

## Schema changes

Edit models in `backend/app/models/`, then:

```bash
cd backend && .venv/bin/alembic revision --autogenerate -m "describe change"
```

`tests/test_migrations.py` fails if models and migrations drift apart.

## Private data

`data/private/` and `.env` are git-ignored. Put `master_resume.pdf` in `data/private/`.
