from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.ingestion.normalize import dedup_key, parse_locations
from app.ingestion.pipeline import get_or_create_company
from app.main import app
from app.models import Job, JobScore, StatusEvent, UserSettings
from app.scoring.location import all_excluded
from app.scoring.run import score_all

REPO = Path(__file__).resolve().parents[2]


def add(session: Session, title: str, company: str = "Acme", location: str = "New York, NY", **over) -> Job:
    c = get_or_create_company(session, company)
    job = Job(company_id=c.id, title=title, normalized_title=title.lower(), location_raw=location,
              locations=parse_locations(location), employment_type="full_time",
              description_text="New grad role. Use SQL and Tableau.",
              apply_url=f"https://x.com/{company}/{title}/{location}",
              canonical_apply_url=f"https://x.com/{company}/{title}/{location}",
              dedup_key=dedup_key(company, title, location), **over)
    session.add(job)
    session.flush()
    return job


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def skip(client: TestClient, job: Job, *reasons: str, note: str | None = None) -> None:
    r = client.put(f"/api/jobs/{job.id}/status", json={"status": "skipped", "reasons": list(reasons), "note": note})
    assert r.status_code == 200, r.text


def test_reasons_are_recorded_and_validated(client: TestClient, session: Session) -> None:
    job = add(session, "Data Analyst")
    skip(client, job, "pay", "location", "pay")
    event = session.scalar(select(StatusEvent).where(StatusEvent.job_id == job.id))
    assert event.reasons == ["pay", "location"]
    assert client.put(f"/api/jobs/{job.id}/status", json={"status": "applied", "reasons": ["pay"]}).status_code == 422
    assert client.put(f"/api/jobs/{job.id}/status", json={"status": "skipped", "reasons": ["vibes"]}).status_code == 422
    detail = client.get(f"/api/jobs/{job.id}").json()
    assert detail["tracking"]["events"][0]["reasons"] == ["Pay is too low", "Location or commute"]


def test_only_jobs_still_skipped_count(client: TestClient, session: Session) -> None:
    job = add(session, "Data Analyst")
    skip(client, job, "company")
    client.put(f"/api/jobs/{job.id}/status", json={"status": "shortlisted"})
    body = client.get("/api/skip-feedback").json()
    assert body["skipped"] == 0 and body["suggestions"] == []


def test_suggestions_from_reasons(client: TestClient, session: Session) -> None:
    s = session.get(UserSettings, 1)
    s.salary_min, s.experience_max_years = Decimal("90000"), Decimal("3")
    s.work_arrangements = ["remote", "hybrid", "onsite"]
    s.preferred_locations, s.target_titles_primary = ["New York, NY", "NJ"], ["Data Analyst"]
    session.flush()

    skip(client, add(session, "Data Analyst", company="Globex"), "company", note="bad reviews")
    skip(client, add(session, "Marketing Analyst"), "role")
    skip(client, add(session, "Marketing Data Analyst", location="Austin, TX"), "role", "location")
    skip(client, add(session, "Data Analyst", location="Austin, TX"), "location")
    skip(client, add(session, "Data Analyst", location="Hoboken, NJ"), "location")  # NJ is preferred: no suggestion
    for i, top in enumerate((92000, 95000, 101000)):
        skip(client, add(session, f"Analyst {i}", base_min=Decimal("85000"), base_max=Decimal(top), pay_period="year"),
             "pay")

    body = client.get("/api/skip-feedback").json()
    counts = {r["reason"]: r["count"] for r in body["reasons"]}
    assert counts["location"] == 3 and counts["pay"] == 3 and counts["experience"] == 0
    assert body["notes"][0]["note"] == "bad reviews"
    changes = {sg["id"]: sg["change"] for sg in body["suggestions"]}
    assert changes == {
        "company:globex": {"excluded_companies": ["Globex"]},
        "word:marketing": {"excluded_title_words": ["marketing"]},
        "location:austin, tx": {"excluded_locations": ["Austin, TX"]},
        "pay:95000": {"salary_min": 95000},
    }
    # Applying one makes it go away.
    assert client.put("/api/settings", json=changes["company:globex"]).status_code == 200
    assert "company:globex" not in {sg["id"] for sg in client.get("/api/skip-feedback").json()["suggestions"]}


def test_experience_and_arrangement_need_a_pattern(client: TestClient, session: Session) -> None:
    session.get(UserSettings, 1).work_arrangements = ["hybrid", "onsite"]
    session.flush()
    for i in range(3):
        skip(client, add(session, f"Analyst {i}", work_arrangement="onsite", exp_min_years=Decimal(2 + i)),
             "experience", "arrangement")
    ids = {sg["id"] for sg in client.get("/api/skip-feedback").json()["suggestions"]}
    assert {"experience:2", "arrangement:onsite"} <= ids


def test_excluded_locations_need_every_location() -> None:
    austin, nyc = {"city": "Austin", "state": "TX", "country": "US"}, {"city": "New York", "state": "NY"}
    assert all_excluded([austin], ["Austin, TX"]) and all_excluded([austin], ["TX"])
    assert not all_excluded([austin, nyc], ["Austin, TX"])
    assert not all_excluded([{"remote": True, "country": "US"}], ["TX"]) and not all_excluded([], ["TX"])


def test_hidden_companies_words_and_places_are_skip(session: Session, tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("skills:\n  SQL: {level: strong, evidence: x}\n  Tableau: {level: strong, evidence: x}\n")
    s = session.get(UserSettings, 1)
    s.target_titles_primary = ["Data Analyst"]
    s.secondary_locations, s.secondary_location_min_score = ["Austin, TX"], Decimal("0")
    keep = add(session, "Data Analyst")
    hidden_co = add(session, "Data Analyst", company="Globex")
    hidden_word = add(session, "Marketing Data Analyst")
    hidden_place = add(session, "Data Analyst", location="Austin, TX")
    score_all(session, REPO / "config", profile)
    before = dict(session.execute(select(JobScore.job_id, JobScore.recommendation)).all())
    assert before[hidden_word.id] != "skip" and before[hidden_place.id] != "skip"

    s.excluded_companies, s.excluded_title_words, s.excluded_locations = ["globex"], ["marketing"], ["Austin, TX"]
    score_all(session, REPO / "config", profile)
    after = {j: (rec, expl) for j, rec, expl in
             session.execute(select(JobScore.job_id, JobScore.recommendation, JobScore.explanation)).all()}
    assert after[keep.id][0] != "skip"
    assert after[hidden_co.id][0] == after[hidden_word.id][0] == after[hidden_place.id][0] == "skip"
    assert "hidden jobs from Globex" in str(after[hidden_co.id][1])
    assert "excluded locations" in str(after[hidden_place.id][1])
