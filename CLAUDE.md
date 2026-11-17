# Project: Job Discovery & Tracking Platform

## Mission

Act as my lead full-stack engineer, technical architect, and career-search strategist. Build a personal web app that finds, filters, ranks, and tracks full-time jobs so I can apply to the best ones MANUALLY. The main output is a ranked "Jobs to Apply To" list: each job with a competitiveness tier, a plain-English explanation, and a direct link to the employer's application page. The app never submits or fills applications.

## Hard rules

- NO application automation: no Playwright autofill, no auto-submission, no interaction with employer application forms.
- No scraping that violates a platform's terms. Prefer official APIs and public feeds. LinkedIn/Indeed: no scraping or account automation; accept pasted links or job-alert emails only.
- Never bypass CAPTCHAs, logins, or anti-bot defenses.
- Never store or request government IDs, SSNs, passwords, or employer-portal credentials.
- Secrets live in a git-ignored `.env`. Never hardcode credentials or log personal data. Private files (resume, profile) go in `data/private/` (git-ignored).
- Never invent facts about me. Work authorization, graduation date, and relocation availability are Settings fields I fill in; blank until then.
- Don't add infrastructure that simpler components can handle.
- Never claim a feature works without running it.

## About the user (for scoring only)

The user's background, target roles, pay and location preferences are private and live in a git-ignored file:

@data/private/about_me.md

If that file doesn't exist (a new user), ask them for these facts or point them to `docs/SETUP.md`; never guess them.
The user's résumé (`data/private/master_resume.pdf`) is the authoritative source. Never invent projects, metrics,
skills, or credentials. Internships are separate from full-time experience unless the employer counts them.

## Target roles

Target titles live in Settings (primary / secondary). Roles pass only when entry-level (new grad / 0-2 yrs, up to the
Settings maximum); senior titles are excluded. Match on responsibilities and skills when titles differ. Role types that
are never targets (`off_target_title_words`) and the user's hands-on tools (`tool_skills`) are in `config/scoring.yaml`.

## Compensation

- Minimum base and preferred range are Settings fields.
- Base, signing bonus, bonus, equity, total comp are separate fields. Bonus/equity never satisfy the base minimum.
- Unlisted pay: show a clearly labeled ESTIMATE with confidence and evidence. Keep such jobs eligible; never present estimates as confirmed (stored in separate `est_*` columns).

## Location

Configurable U.S. preferences in Settings: remote, hybrid, on-site, preferred locations, secondary cities (eligible only above `secondary_location_min_score`), exclusions, relocation preference. Workday/own-career-site employers: pasted links and job-alert emails only, no Workday scraping. Reddit: pasted post links (API only if approved).

## Sources

Greenhouse, Lever and Ashby public board APIs (`config/companies.yaml`); public/shared Google Sheets (CSV export); public GitHub job lists (`config/lists.yaml`, raw.githubusercontent.com only); CSV files I download myself (`import-csv`); Reddit via official API; pasted links.

Checked 2026-11-05: LinkedIn and Indeed are pasted links / alert emails only. simplify.jobs, newgrad.ai, welcometothejungle.com and boardsweep.io prohibit automated access in their terms: not fetched. jobright.ai site is not fetched (its GitHub lists are). swejoblists.com CSV export is behind reCAPTCHA: I download it manually. Modular source interface for Ashby/career pages later. Each source: rate limiting, retry with backoff, its own access policy.

Apply hub (`config/apply_hub.yaml` + private `data/private/apply_hub.yaml`, `python -m app.cli hub`; a page in the web app in Phase 5): links to everything the app doesn't fetch (pre-filtered LinkedIn/Indeed searches, job sites, Workday/custom employer career sites) so I check them by hand.

Adzuna API (terms permit personal job search; keys in .env): `ingest adzuna`, never part of `ingest all`. Paid per call: caps in `config/adzuna.yaml` (daily + total), every run records `api_calls`, no retries. Predicted salaries are never stored as posted pay.

Dedup across sources: normalized company + title + location, plus canonical apply URL. Prioritize recent postings; periodically re-check listings are active.

## Ranking

- Match score 0-100, configurable weights: skills 30%, experience 25%, responsibilities/career 20%, compensation 15%, location/arrangement 10%.
- Competitiveness tier: Strong fit / Competitive / Reach. A qualitative label, NEVER a probability of interview or offer.
- Hard requirements (certifications, sponsorship/citizenship language, clearance) flagged separately, not folded into the score. With work-auth blank, show "Needs your check".
- Per job: why competitive, best-supporting experiences, missing qualifications, experience-level fit, comp vs. target, recommendation (Apply / Maybe / Skip) with reasons.
- Rule-based scorer `rules-v1` (`backend/app/scoring/`): `extract.py` reads pay/experience/seniority/flags from text, `estimate.py` estimates unposted pay from similar postings (never "high" confidence), `location.py` classifies locations, `rules.py` scores. Tuning in `config/scoring.yaml` (rotational bonus 8, posting-age penalty up to 10 points after 30 days, tier/recommendation thresholds, senior pay floor $180k); skill vocabulary in `config/skills.yaml`; my skills in `data/private/profile.yaml` (resume-only, strong/moderate with evidence). Off-target role types (engineering, design, trading, ...) are Skip even when the description uses my tools. Claude scorer plugs in later and writes to `job_scores` under its own scorer name.
- Weight tuning loop: `python -m app.cli score` then `top --sample`; I react, we adjust weights/rules.

## Tracking

Statuses: New, Shortlisted, Applied, Interviewing, Offer, Rejected, Skipped, Expired. I mark Applied myself; record date and optional resume version. One `job_status` row per job (no duplicates). Every change logged in `status_events`.

Skip feedback (2026-11-12): marking a job Skipped opens a popup of reasons (`SKIP_REASONS`, stored on the `status_events` row). `app/feedback.py` turns patterns into suggested Settings changes (hide a company, skip a title word, exclude a city, drop a work arrangement, raise min pay, lower max experience), shown on the Dashboard. Settings change only when I click Apply; undo in Settings. When asked to tune scoring, read `GET /api/skip-feedback` first.

Application backlog (2026-11-12): `app/backlog.py` writes `backlog/applications.md`, `applications.csv` and `rollup.md` (git-ignored; never exported) for every job with status applied/interviewing/offer/rejected. Regenerated in the background whenever such a status is saved, or by `python -m app.cli backlog`. Rule-based and free: requirement lines are quoted exactly from the stored posting, missing facts are "not stated", fit is 1-5 (must-have skill coverage minus years/advanced-degree stretch; never a probability). Patterns and generic gap fixes in `config/backlog.yaml`; my facts and personal gap fixes in `data/private/backlog_me.yaml`. Rollup flags anything resting on fewer than 3 postings. When asked to sharpen my résumé or review my applications, read `backlog/rollup.md` first.

Résumé gap analysis (2026-11-12, private, git-ignored): `analysis/README.md` holds my instructions and profile corrections; results in `analysis/00_summary.md` to `06_stories.md`. `analysis/LOG.md` records which applications each run covered. When I say "update my resume analysis" (or after new applications), follow the README's "How to update": diff `backlog/applications.md` against the last LOG entry, re-run steps 1-2 for new roles only, update the summary, and tell me only what changed.

## Web app

Encouragement banner lines: `config/encouragement.yaml` + private `data/private/encouragement.yaml`.

Jobs to Apply To (home, ranked, Apply/Maybe by default, "Open application" button), Dashboard, Job detail, Settings, CSV export, Apply hub. Clean and responsive; clarity over decoration.

Built in Phase 5 (2026-11-06): Next.js 16 / React 19 / Tailwind 4 in `frontend/`. Pages are thin server components wrapping client components (`src/components/`) in `<Suspense>`; data comes from FastAPI `/api/*` (`backend/app/api.py`) through a Next.js rewrite, no CORS. Fetching uses the small `useResource` hook in `components/ui.tsx` (no data library). This Next.js version differs from older ones: read `frontend/node_modules/next/dist/docs/` before changing framework-level code. Settings scoring uses: salary, locations, titles, experience max, weights, work arrangements, work authorization, sponsorship. Also used: excluded locations (a job is Skip when all its locations are excluded), hidden companies, hidden title words. Not yet used: graduation date, relocation (labeled in the UI).

## Scheduled updates (Phase 6, 2026-11-09)

`backend/app/scheduler.py` (`worker` service, `python -m app.scheduler [--once]`): fetch all configured sources, `app/freshness.py` closes list/sheet/Adzuna-only jobs (`config/schedule.yaml`), rescore, record a `scheduled_runs` row (digest: new Apply/Maybe ids, tracked jobs whose listing closed). Interval = `user_settings.search_frequency_hours`; failed attempts retry after 1 hour; a Postgres advisory lock prevents overlapping updates; runs stuck "running" > 3 h are marked failed. Adzuna only when `run_adzuna: true`. Digest is in-app (Dashboard), no email: sending email would need an SMTP password, which conflicts with the no-passwords rule unless I decide otherwise. Non-authoritative sources can't reopen a job an ATS board closed.

## Stack and layout

Next.js + React + TS + Tailwind (`frontend/`); FastAPI + SQLAlchemy 2 + Alembic + psycopg 3 (`backend/`); PostgreSQL 16; a plain polling worker for scheduled updates (Phase 6); Docker Compose; pytest. Enum-like columns are VARCHAR + CHECK constraints (easier migrations than PG enums). Config files in `config/`.

## Sharing

`docs/SETUP.md` is the guide for a new user. `python3 scripts/export_shareable.py <dir>` writes a copy of the last commit with fresh git history and fails if any term from `data/private/share_denylist.txt` (or the git email) appears in it. Never copy `data/private/`, `backlog/`, `analysis/`, `ResumeProject.md` or `.env` anywhere.

## Development process

For each step: inspect existing code, short plan, implement real working code, tests and error handling, list files changed, show how to run/verify, state limitations honestly. Commit after each working step. Ask before installing anything unusual.

Build order:
1. Repo setup, .gitignore, Docker Compose, Postgres, schema, migrations
2. Greenhouse and Lever connectors, normalization, dedup
3. Google Sheets and Reddit importers
4. Filtering and rule-based scoring with tiers and explanations
5. Frontend: ranked list, job detail, dashboard, status tracking, settings, CSV export
6. Scheduling, active-listing checks, notifications/digest
7. Claude-powered scoring and explanations
8. Optional: tailored resume suggestions I review myself (never overwrite master resume; no fabricated bullets)

## Common commands

```bash
docker compose up -d db                     # start Postgres
cd backend && source .venv/bin/activate
alembic upgrade head                        # apply migrations
pytest                                      # run tests (needs db running)
python -m app.cli ingest all                # boards, sheets, and GitHub lists in config/
python -m app.cli import-csv <file.csv>     # a job-list CSV I downloaded
python -m app.cli hub                       # apply hub: links I check by hand
python -m app.cli score                     # enrich + score all active jobs (~1 min)
python -m app.cli top [--n 25] [--sample]   # ranked Apply/Maybe list, or a tuning sample
uvicorn app.main:app --reload               # API on http://localhost:8000
docker compose up -d --build api worker     # or: API + scheduled-update worker in Docker (rebuild after backend changes)
python -m app.scheduler --once              # run one full update (fetch, close gone listings, rescore) now
cd frontend && npm run dev                  # web app on http://localhost:3000 (PATH needs ~/.local/node/bin)
```
