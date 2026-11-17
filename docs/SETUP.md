# Setup guide (new user)

A personal job-search app that runs on your own computer. It pulls listings from public job-board APIs, ranks them
against **your** résumé and preferences, and tracks what you apply to. It never applies for you: "Open application"
just opens the employer's page.

Everything is yours alone: your own database, your own keys, your own résumé. Nothing is shared with whoever gave
you this repo, and their data isn't in it.

---

## 1. Install the tools (once)

| Tool | Why | Get it |
|---|---|---|
| Docker Desktop | runs the database, API and the daily updater | docker.com/products/docker-desktop |
| Python 3.12+ | backend, tests, command line | python.org (or `brew install python`) |
| Node.js 24 LTS | the website | nodejs.org (or `brew install node@24`) |
| Claude Code (optional) | ask for changes in plain English | claude.com/claude-code |

## 2. Get the code and create your private files

```bash
git clone <repo url> job-platform && cd job-platform
cp .env.example .env                 # then open .env and set POSTGRES_PASSWORD (both places) to anything you like
mkdir -p data/private
cp config/profile.example.yaml data/private/profile.yaml
```

`.env` and everything in `data/private/` are **git-ignored**: they never get committed or pushed.

Edit `data/private/profile.yaml` from **your** résumé (instructions are at the top of the file). Optionally put your
résumé PDF at `data/private/master_resume.pdf` (only read by you / Claude Code; the app doesn't upload it anywhere).

## 3. Start everything

```bash
docker compose up -d --build          # database, API (http://localhost:8000) and the daily updater
cd backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/alembic upgrade head
.venv/bin/python -m app.scheduler --once     # first full fetch + scoring (~4 minutes)
cd ../frontend && npm install && npm run dev  # website at http://localhost:3000
```

Open **http://localhost:3000 → Settings** and fill in your own target titles, minimum pay, locations, and the
"About me" facts (work authorization, sponsorship, graduation date). The repo ships with example defaults
(analyst roles, NYC/NJ, $90k); change them. Then click **Rescore now**.

## 4. The job sources (the APIs this pulls from)

None of these need an account or key except Adzuna.

| Source | What it is | Where to configure | Key needed |
|---|---|---|---|
| Greenhouse, Lever, Ashby | employers' official public job-board APIs (~96 companies) | `config/companies.yaml` | no |
| GitHub job lists | public new-grad lists (SimplifyJobs, jobright, jobr) read from raw.githubusercontent.com | `config/lists.yaml` | no |
| Google Sheets | any sheet shared "anyone with the link" | `config/sheets.yaml` | no |
| Adzuna | job-search API aggregating many boards | `config/adzuna.yaml` | **yes, your own** (free trial; paid per call) |
| CSV files | a job-list CSV you downloaded yourself | `python -m app.cli import-csv file.csv` | no |
| Pasted links | any single job link (LinkedIn, Indeed, Reddit post, ...) | `python -m app.cli add-link <url>` | no |
| Apply hub | sites the app does **not** fetch (LinkedIn, Indeed, Workday career sites): pre-filtered links you open yourself | `config/apply_hub.yaml` | no |

**Adding a company:** find its careers page. If the URL is `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>` or
`jobs.ashbyhq.com/<token>`, add `- { name: Company, ats: greenhouse, token: <token> }` to `config/companies.yaml`.
A wrong token just shows as a failed source on the Dashboard.

**Adzuna (optional):** register at developer.adzuna.com, then put *your own* keys in `.env`:
`ADZUNA_APP_ID=...` and `ADZUNA_APP_KEY=...`. Run `python -m app.cli ingest adzuna` by hand. Call caps live in
`config/adzuna.yaml`; it's only part of the daily update if `run_adzuna: true` in `config/schedule.yaml`.
Never share or commit your keys.

**What it deliberately doesn't do:** scrape LinkedIn/Indeed/Workday, log in anywhere, solve CAPTCHAs, or touch an
application form. Sites whose terms forbid automated access are in the apply hub instead.

Fetch by hand any time (from `backend/`): `.venv/bin/python -m app.cli ingest all` (boards + lists + sheets),
then `.venv/bin/python -m app.cli score`. Or click **Update now** on the Dashboard.

## 5. Make it yours

| Want to change | File |
|---|---|
| Titles, pay, locations, weights, update frequency | Settings page |
| Your skills and experiences | `data/private/profile.yaml` |
| Skills the app recognizes in job descriptions | `config/skills.yaml` |
| Role types that are always skipped (engineer, designer, trader, ... by default) | `config/scoring.yaml` → `off_target_title_words` |
| Which of your skills count as hands-on tools | `config/scoring.yaml` → `tool_skills` |
| Companies / lists to pull from | `config/companies.yaml`, `config/lists.yaml` |
| Apply hub links (personal ones like your school's Handshake) | `config/apply_hub.yaml`, or private `data/private/apply_hub.yaml` |
| Banner quotes (personal ones) | `config/encouragement.yaml`, or private `data/private/encouragement.yaml` |

**Targeting engineering, design or research roles?** Remove those words from `off_target_title_words` (or set it to
`[]`), add your target titles in Settings, and add your tools to `config/skills.yaml` + `tool_skills`.

## 6. Application backlog

Every job you mark **Applied** gets a structured entry in `backlog/applications.md` (and `applications.csv`): pay,
years asked, degree, skills (must-have vs preferred, quoted from the posting), soft skills, application requirements,
your matches and gaps, and a 1–5 fit. `backlog/rollup.md` totals them up: most-asked skills, your gaps ranked by how
many postings they block with the cheapest fix, and patterns. Copy `config/backlog_me.example.yaml` to
`data/private/backlog_me.yaml` and fill it in. `backlog/` is git-ignored. Regenerate by hand with
`.venv/bin/python -m app.cli backlog`.

## 7. Using Claude Code with it

Run `claude` in the `job-platform` folder. `CLAUDE.md` gives it the project rules (no auto-applying, no scraping that
breaks terms, secrets only in `.env`). Put your own background and preferences in `data/private/about_me.md`
(git-ignored); `CLAUDE.md` imports it so Claude knows your targets. Example prompts:

- "Add these companies to companies.yaml: ..."
- "Why did job 1234 get Skip?"
- "I'm targeting product design roles; update the scoring config for that."

## 8. Everyday commands

```bash
docker compose up -d                         # start (Docker Desktop must be running)
cd frontend && npm run dev                   # website
cd backend && .venv/bin/pytest               # tests
docker compose logs -f worker                # watch the daily updater
docker compose up -d --build api worker      # after changing backend code
```

Scores are match scores, not chances of getting an interview.
