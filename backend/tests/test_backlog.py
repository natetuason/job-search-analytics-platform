import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api as api
from app.backlog import Reader, write_backlog
from app.db import get_session
from app.ingestion.normalize import dedup_key, parse_locations
from app.ingestion.pipeline import get_or_create_company
from app.main import app
from app.models import Job, JobStatus

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)

POSTING = """About the role
You will build dashboards for the finance team.

What you'll need
- Bachelor's degree in Economics, Statistics or a related quantitative field
- 1+ years of experience in analytics (internships count)
- Proficiency in SQL and Excel
- Strong communication skills and attention to detail
- Experience with BI tools (e.g. Looker) is a plus

Nice to have
- Exposure to dbt

How to apply
Please include a cover letter. Candidates may only apply to one role per cycle.
This role requires travel up to 10%. We are unable to sponsor visas for this role.
Annual bonus eligible. Traded on the New York Stock Exchange.
"""


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / "private"
    private.mkdir()
    (private / "profile.yaml").write_text("skills:\n  SQL: {level: strong, evidence: x}\n  Excel: {level: strong, evidence: x}\n")
    (private / "backlog_me.yaml").write_text("full_time_years: 0\nextra_skills: [Generative AI]\n")
    return REPO / "config", private


def add(session: Session, title: str, text: str = POSTING, status: str = "applied", **over) -> Job:
    c = get_or_create_company(session, over.pop("company", "Acme"))
    job = Job(company_id=c.id, title=title, normalized_title=title.lower(), location_raw="New York, NY",
              locations=parse_locations("New York, NY"), employment_type="full_time", description_text=text,
              apply_url=f"https://acme.com/{title}", canonical_apply_url=f"https://acme.com/{title}",
              dedup_key=dedup_key(c.name, title, "New York, NY"), work_arrangement="hybrid", **over)
    session.add(job)
    session.flush()
    session.add(JobStatus(job_id=job.id, status=status, applied_at=NOW if status != "skipped" else None))
    session.flush()
    return job


def test_reads_a_posting_without_inventing(session: Session, dirs) -> None:
    job = add(session, "Data Analyst", exp_min_years=Decimal(1), exp_text="1+ years of experience in analytics")
    e = Reader(*dirs).read(job, "Acme", session.get(JobStatus, job.id))
    assert e.family == "data/business analyst" and e.arrangement == "hybrid"
    assert set(e.must) == {"SQL", "Excel"} and e.must["SQL"] == "Proficiency in SQL and Excel"
    assert set(e.preferred) == {"Looker", "dbt", "Dashboards & BI reporting"}  # "is a plus"; the Nice to have section
    assert e.preferred["Looker"] == "Experience with BI tools (e.g. Looker) is a plus"  # not split at "e.g."
    assert {"Communication", "Attention to detail"} <= set(e.soft)
    assert e.app_reqs.keys() == {"Cover letter"} and e.limits and "one role" in e.limits[0]
    assert e.relocation_travel and e.work_auth and {"statistics", "economics"} <= set(e.majors)
    assert all("Stock Exchange" not in b for b in e.bonus_equity) and e.bonus_equity
    assert e.pay == "not stated" and e.grad_year == []
    assert e.gaps_must == [] and e.gaps_pref == ["Dashboards & BI reporting", "Looker", "dbt"] and e.fit == 5


def test_years_ceiling_and_fit_penalties(session: Session, dirs) -> None:
    reader = Reader(*dirs)
    cap = add(session, "Associate Consultant", exp_min_years=Decimal(2), exp_max_years=Decimal(2),
              exp_text="up to 2 years postgraduate experience maximum")
    senior = add(session, "Product Manager", exp_min_years=Decimal(3), exp_text="3+ years of experience")
    e_cap = reader.read(cap, "Acme", session.get(JobStatus, cap.id))
    e_sen = reader.read(senior, "Acme", session.get(JobStatus, senior.id))
    assert e_cap.years_min == 0 and "ceiling" in e_cap.years
    assert e_sen.fit == 3 and "asks 3+ years" in e_sen.fit_reason
    empty = add(session, "Analyst", text="")
    e = reader.read(empty, "Acme", session.get(JobStatus, empty.id))
    assert e.fit == 3 and not e.has_text and e.years == "not stated" and e.must == {}


def test_write_backlog_files(session: Session, dirs, tmp_path: Path) -> None:
    add(session, "Data Analyst", base_min=Decimal(90000), base_max=Decimal(110000), pay_period="year")
    add(session, "=HYPERLINK(1)", company="Evil")
    add(session, "Skipped Analyst", status="skipped")
    out = tmp_path / "out"
    assert write_backlog(session, *dirs, out, now=NOW) == 2
    md = (out / "applications.md").read_text()
    assert "Acme: Data Analyst" in md and "Skipped Analyst" not in md and "$90,000–$110,000/yr" in md
    rows = list(csv.DictReader((out / "applications.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert {r["title"] for r in rows} == {"Data Analyst", "'=HYPERLINK(1)"}  # formula escaped
    roll = (out / "rollup.md").read_text()
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5."):
        assert heading in roll
    assert "⚠ fewer than 3 postings" in roll and "applications.md#j" in roll and "| Looker |" in roll


def test_marking_applied_refreshes_the_backlog(session: Session, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(api, "_refresh_backlog", lambda factory: calls.append(1))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[api.get_session_factory] = lambda: None
    try:
        client = TestClient(app)
        job = add(session, "Data Analyst", status="shortlisted")
        assert client.put(f"/api/jobs/{job.id}/status", json={"status": "shortlisted", "note": "x"}).status_code == 200
        assert calls == []
        assert client.put(f"/api/jobs/{job.id}/status", json={"status": "applied"}).status_code == 200
        assert calls == [1]
    finally:
        app.dependency_overrides.clear()
