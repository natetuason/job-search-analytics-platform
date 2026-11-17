from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api as api
from app.db import get_session
from app.ingestion.normalize import dedup_key, parse_locations
from app.ingestion.pipeline import get_or_create_company
from app.main import app
from app.models import Job, JobScore, JobStatus, StatusEvent, UserSettings

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[api.get_session_factory] = lambda: (
        lambda: Session(bind=session.connection(), join_transaction_mode="create_savepoint"))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_job(session: Session, title: str, rec: str = "apply", score: float = 80, company: str = "Acme",
            location: str = "New York, NY", loc_class: str = "preferred", rotational: bool = False, **over) -> Job:
    c = get_or_create_company(session, company)
    job = Job(company_id=c.id, title=title, normalized_title=title.lower(), location_raw=location,
              locations=parse_locations(location), employment_type="full_time", description_text="SQL",
              apply_url=f"https://jobs.acme.com/{title}", canonical_apply_url=f"https://jobs.acme.com/{title}",
              dedup_key=dedup_key(company, title, location), **over)
    session.add(job)
    session.flush()
    session.add(JobScore(job_id=job.id, scorer="rules-v1", settings_version=1, score=Decimal(str(score)),
                         breakdown={"categories": {}, "location_class": loc_class}, tier="competitive",
                         recommendation=rec,
                         explanation={"summary": f"{title} summary", "rotational": rotational, "reasons": []},
                         hard_flags=[{"type": "no_sponsorship", "label": "Says it won't sponsor work visas",
                                      "status": "ok", "evidence": "..."}]))
    session.flush()
    return job


# ---------- jobs list ----------

def test_list_defaults_to_apply_and_maybe_ranked(client: TestClient, session: Session) -> None:
    add_job(session, "Data Analyst", score=90, base_min=Decimal("95000"), base_max=Decimal("110000"), pay_period="year")
    add_job(session, "Business Analyst", rec="maybe", score=70, est_base_min=Decimal("90000"),
            est_base_max=Decimal("100000"), est_pay_period="year", est_confidence="low")
    add_job(session, "Barista", rec="skip", score=10)
    body = client.get("/api/jobs").json()
    assert body["total"] == 2
    first, second = body["items"]
    assert (first["title"], first["pay"]["kind"], first["status"]) == ("Data Analyst", "posted", "new")
    assert second["pay"] == {"kind": "estimate", "min": 90000.0, "max": 100000.0, "period": "year", "confidence": "low"}
    assert first["flags"] == [{"label": "Says it won't sponsor work visas", "status": "ok"}]
    assert first["is_new"] is True and first["is_active"] is True


def test_list_filters(client: TestClient, session: Session) -> None:
    a = add_job(session, "Data Analyst", company="Zeta", loc_class="remote", location="Remote - US")
    add_job(session, "Rotational Analyst", rotational=True)
    session.add(JobStatus(job_id=a.id, status="skipped"))
    session.flush()
    assert client.get("/api/jobs", params={"q": "zeta"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"location": "remote"}).json()["items"][0]["id"] == a.id
    assert [j["title"] for j in client.get("/api/jobs", params={"rotational": "true"}).json()["items"]] == [
        "Rotational Analyst"]
    # "new" includes jobs I've never touched.
    assert [j["title"] for j in client.get("/api/jobs", params={"status": "new,shortlisted"}).json()["items"]] == [
        "Rotational Analyst"]
    assert client.get("/api/jobs", params={"status": "skipped"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"rec": "apply,nope"}).status_code == 422


def test_same_role_in_several_cities_is_one_row(client: TestClient, session: Session) -> None:
    add_job(session, "Data Analyst", score=70, location="Chicago, IL", loc_class="secondary")
    best = add_job(session, "Data Analyst", score=90)
    add_job(session, "Data Analyst", score=60, location="Remote - US", loc_class="remote")
    add_job(session, "Data Analyst", score=80, company="Other Co")
    items = client.get("/api/jobs").json()["items"]
    assert [(j["id"], j["company"], j["other_locations"]) for j in items] == [(best.id, "Acme", 2),
                                                                            (items[1]["id"], "Other Co", 0)]
    assert client.get("/api/jobs").json()["total"] == 2
    assert client.get("/api/jobs", params={"group": "false"}).json()["total"] == 4
    # Filters apply before grouping: the remote posting is the one shown for a remote search.
    remote = client.get("/api/jobs", params={"location": "remote"}).json()["items"]
    assert [(j["location"], j["other_locations"]) for j in remote] == [("Remote - US", 0)]


def test_acting_on_one_city_moves_the_whole_role(client: TestClient, session: Session) -> None:
    nyc = add_job(session, "Data Analyst", score=90)
    add_job(session, "Data Analyst", score=70, location="Chicago, IL", loc_class="secondary")
    client.put(f"/api/jobs/{nyc.id}/status", json={"status": "applied"})
    assert client.get("/api/jobs", params={"status": "new,shortlisted"}).json()["total"] == 0
    applied = client.get("/api/jobs", params={"status": "applied"}).json()["items"]
    assert [(j["id"], j["other_locations"]) for j in applied] == [(nyc.id, 0)]
    assert client.get("/api/jobs", params={"status": "new", "group": "false"}).json()["total"] == 1


def test_inactive_jobs_hidden_unless_tracked(client: TestClient, session: Session) -> None:
    add_job(session, "Old Analyst", is_active=False)
    applied = add_job(session, "Applied Analyst", is_active=False)
    session.add(JobStatus(job_id=applied.id, status="applied", applied_at=applied.first_seen_at))
    session.flush()
    items = client.get("/api/jobs", params={"status": ""}).json()["items"]
    assert [(j["title"], j["is_active"]) for j in items] == [("Applied Analyst", False)]


# ---------- detail + status ----------

def test_detail_and_404(client: TestClient, session: Session) -> None:
    job = add_job(session, "Data Analyst")
    d = client.get(f"/api/jobs/{job.id}").json()
    assert d["description"] == "SQL" and d["score_detail"]["explanation"]["summary"] == "Data Analyst summary"
    assert d["tracking"]["events"] == []
    assert client.get("/api/jobs/999999").status_code == 404


def test_status_changes_are_logged_once_per_job(client: TestClient, session: Session) -> None:
    job = add_job(session, "Data Analyst")
    rv = client.post("/api/resume-versions", json={"label": "v1 analytics"}).json()
    r = client.put(f"/api/jobs/{job.id}/status", json={"status": "shortlisted"})
    assert r.status_code == 200 and r.json()["applied_at"] is None
    r = client.put(f"/api/jobs/{job.id}/status", json={"status": "applied", "resume_version_id": rv["id"],
                                                        "notes": "referral from alum"})
    assert r.json()["applied_at"] is not None
    client.put(f"/api/jobs/{job.id}/status", json={"status": "applied", "notes": "followed up"})  # no status change

    assert session.query(JobStatus).filter_by(job_id=job.id).count() == 1
    events = session.scalars(select(StatusEvent).where(StatusEvent.job_id == job.id).order_by(StatusEvent.id)).all()
    assert [(e.from_status, e.to_status) for e in events] == [(None, "shortlisted"), ("shortlisted", "applied")]
    d = client.get(f"/api/jobs/{job.id}").json()
    assert d["tracking"]["notes"] == "followed up" and d["tracking"]["resume_version"]["label"] == "v1 analytics"
    assert client.get("/api/jobs", params={"status": "applied"}).json()["total"] == 1


def test_status_with_explicit_date_and_bad_input(client: TestClient, session: Session) -> None:
    job = add_job(session, "Data Analyst")
    r = client.put(f"/api/jobs/{job.id}/status", json={"status": "applied", "applied_at": "2026-11-01"})
    assert r.json()["applied_at"].startswith("2026-11-01")
    assert client.put(f"/api/jobs/{job.id}/status", json={"status": "hired"}).status_code == 422
    assert client.put(f"/api/jobs/{job.id}/status", json={"status": "new", "resume_version_id": 99999}).status_code == 422
    assert client.put("/api/jobs/999999/status", json={"status": "new"}).status_code == 404
    assert client.post("/api/resume-versions", json={"label": "a"}).status_code == 201
    assert client.post("/api/resume-versions", json={"label": "a"}).status_code == 409


# ---------- settings ----------

def test_settings_update_bumps_version_only_on_change(client: TestClient, session: Session) -> None:
    before = client.get("/api/settings").json()
    assert before["work_authorization"] is None and before["graduation_date"] is None
    after = client.put("/api/settings", json={"work_authorization": "U.S. citizen", "needs_sponsorship": False,
                                              "graduation_date": "2027-05-15"}).json()
    assert after["version"] == before["version"] + 1 and after["graduation_date"] == "2027-05-15"
    same = client.put("/api/settings", json={"work_authorization": "U.S. citizen"}).json()
    assert same["version"] == after["version"]
    cleared = client.put("/api/settings", json={"graduation_date": None}).json()
    assert cleared["graduation_date"] is None
    assert session.get(UserSettings, 1).needs_sponsorship is False


@pytest.mark.parametrize("body", [
    {"salary_min": None},
    {"salary_min": -1},
    {"scoring_weights": {"skills": 100}},
    {"scoring_weights": {"skills": 0, "experience": 0, "responsibilities": 0, "compensation": 0, "location": 0}},
    {"salary_preferred_min": 150000, "salary_preferred_max": 120000},
    {"work_arrangements": ["office"]},
])
def test_settings_validation(client: TestClient, body: dict) -> None:
    assert client.put("/api/settings", json=body).status_code == 422


def test_settings_stale_flag(client: TestClient, session: Session) -> None:
    add_job(session, "Data Analyst")
    session.get(UserSettings, 1).version = 1
    session.flush()
    assert client.get("/api/settings").json()["scores_stale"] is False
    assert client.put("/api/settings", json={"salary_min": 95000}).json()["scores_stale"] is True


# ---------- rescoring ----------

def test_rescore_runs_in_background(client: TestClient, session: Session, tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("skills:\n  SQL: {level: strong, evidence: x}\n")
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(config_dir=REPO / "config", private_dir=tmp_path))
    job = add_job(session, "Data Analyst")
    r = client.post("/api/score")  # TestClient runs the background task before returning
    assert r.status_code == 202
    status = client.get("/api/score").json()
    assert status["running"] is False and status["error"] is None and status["result"]["scored"] >= 1
    score = session.scalar(select(JobScore).where(JobScore.job_id == job.id))
    session.refresh(score)
    assert score.breakdown["location_class"] == "preferred"


def test_rescore_refuses_while_running(client: TestClient) -> None:
    api.SCORE_STATE.running = True
    try:
        assert client.post("/api/score").status_code == 409
    finally:
        api.SCORE_STATE.running = False


# ---------- dashboard, export, hub ----------

def test_dashboard_counts(client: TestClient, session: Session) -> None:
    job = add_job(session, "Data Analyst")
    add_job(session, "Barista", rec="skip")
    client.put(f"/api/jobs/{job.id}/status", json={"status": "applied"})
    d = client.get("/api/dashboard").json()
    assert d["by_recommendation"]["apply"] >= 1 and d["by_status"]["applied"] == 1
    assert d["applied_this_week"] == 1 and d["recent_activity"][0]["job_id"] == job.id


def test_csv_export_escapes_formulas(client: TestClient, session: Session) -> None:
    add_job(session, "=HYPERLINK(\"http://evil\")", base_min=Decimal("95000"), base_max=Decimal("110000"),
            pay_period="year")
    r = client.get("/api/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    header, row = text.splitlines()[:2]
    assert header.startswith("Score,Recommendation,Tier,Title,Company,Location,Same role, other locations".replace(
        "Same role, other locations", '"Same role, other locations"'))
    assert "'=HYPERLINK" in row and "$95,000–$110,000/yr" in row and "https://jobs.acme.com/" in row


def test_hub_lists_links(client: TestClient) -> None:
    links = client.get("/api/hub").json()
    assert links and {"group", "name", "url", "note", "checked"} <= set(links[0])
    assert all(link["url"].startswith("https://") for link in links)


def test_encouragement_merges_private_lines(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    cfg, private = tmp_path / "config", tmp_path / "private"
    cfg.mkdir(), private.mkdir()
    (cfg / "encouragement.yaml").write_text('lines: ["Keep going.", "You got this."]\n')
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(config_dir=cfg, private_dir=private))
    assert client.get("/api/encouragement").json() == ["Keep going.", "You got this."]
    (private / "encouragement.yaml").write_text('lines: ["A line about my own internship.", "Keep going."]\n')
    assert client.get("/api/encouragement").json() == ["Keep going.", "You got this.", "A line about my own internship."]
