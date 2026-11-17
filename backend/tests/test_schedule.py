from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.api as api
import app.scheduler as scheduler
from app.db import get_session
from app.freshness import expire_unlisted
from app.ingestion.normalize import dedup_key, parse_locations
from app.ingestion.pipeline import get_or_create_company, ingest
from app.main import app
from app.models import IngestionRun, Job, JobSource, JobStatus, ScheduledRun, UserSettings
from tests.test_pipeline import FakeSource, raw

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
DAYS = {"github_list": 3, "google_sheet": 3, "adzuna": 30}


def add_job(session: Session, title: str, sources: list[tuple[str, str, datetime]], **over) -> Job:
    c = get_or_create_company(session, "Acme")
    job = Job(company_id=c.id, title=title, normalized_title=title.lower(), location_raw="New York, NY",
              locations=parse_locations("New York, NY"), employment_type="full_time", description_text="SQL",
              apply_url=f"https://acme.com/{title}", canonical_apply_url=f"https://acme.com/{title}",
              dedup_key=dedup_key("Acme", title, "New York, NY"),
              **{"first_seen_at": NOW - timedelta(days=20), **over})
    session.add(job)
    session.flush()
    for i, (kind, ref, seen) in enumerate(sources):
        session.add(JobSource(job_id=job.id, source_kind=kind, external_id=f"{title}:{i}", source_ref=ref,
                              first_seen_at=seen, last_seen_at=seen))
    session.flush()
    return job


def fetched(session: Session, kind: str, ref: str, at: datetime) -> None:
    session.add(IngestionRun(source_kind=kind, source_ref=ref, status="succeeded", started_at=at, finished_at=at))
    session.flush()


# ---------- freshness ----------

def test_list_job_closes_only_after_a_later_fetch_leaves_it_out(session: Session) -> None:
    gone = add_job(session, "Gone", [("github_list", "simplify", NOW - timedelta(days=5))])
    fresh = add_job(session, "Fresh", [("github_list", "simplify", NOW - timedelta(days=1))])
    other_list = add_job(session, "Unfetched", [("github_list", "broken-list", NOW - timedelta(days=5))])
    fetched(session, "github_list", "simplify", NOW - timedelta(hours=2))
    assert expire_unlisted(session, DAYS, NOW) == 1
    for job in (gone, fresh, other_list):
        session.refresh(job)
    assert (gone.is_active, fresh.is_active, other_list.is_active) == (False, True, True)
    assert gone.expired_at == NOW


def test_adzuna_by_age_and_never_for_my_own_imports(session: Session) -> None:
    old = add_job(session, "Old Adzuna", [("adzuna", "a @ NJ", NOW - timedelta(days=31))])
    recent = add_job(session, "Recent Adzuna", [("adzuna", "a @ NJ", NOW - timedelta(days=10))])
    csv = add_job(session, "My CSV", [("csv_file", "swejoblists", NOW - timedelta(days=90))])
    mixed = add_job(session, "Mixed", [("adzuna", "a @ NJ", NOW - timedelta(days=60)),
                                       ("manual", "pasted", NOW - timedelta(days=60))])
    board = add_job(session, "Board", [("greenhouse", "acme", NOW - timedelta(days=60)),
                                       ("adzuna", "a @ NJ", NOW - timedelta(days=60))])
    expire_unlisted(session, DAYS, NOW)
    states = {}
    for job in (old, recent, csv, mixed, board):
        session.refresh(job)
        states[job.title] = job.is_active
    assert states == {"Old Adzuna": False, "Recent Adzuna": True, "My CSV": True, "Mixed": True, "Board": True}


def test_list_cannot_reopen_a_posting_the_board_closed(session: Session) -> None:
    board = FakeSource("greenhouse", [raw()])
    listing = FakeSource("github_list", [raw(kind="github_list", ext="list:1")], authoritative=False)
    ingest(session, board, "acme")
    ingest(session, listing, "simplify")
    board.jobs = []
    ingest(session, board, "acme")  # the board no longer lists it -> closed
    job = session.scalar(select(Job))
    assert not job.is_active
    ingest(session, listing, "simplify")  # still on a stale list
    session.refresh(job)
    assert not job.is_active
    board.jobs = [raw()]
    ingest(session, board, "acme")  # back on the board -> reopened
    session.refresh(job)
    assert job.is_active


def test_list_only_job_can_reopen(session: Session) -> None:
    listing = FakeSource("github_list", [raw(kind="github_list", ext="list:1")], authoritative=False)
    ingest(session, listing, "simplify")
    job = session.scalar(select(Job))
    job.is_active = False
    session.flush()
    ingest(session, listing, "simplify")
    session.refresh(job)
    assert job.is_active


# ---------- scheduler ----------

@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("skills.yaml", "scoring.yaml"):
        copy(REPO / "config" / name, cfg / name)
    (cfg / "schedule.yaml").write_text("run_adzuna: false\nexpire_after_days: {github_list: 3}\n")
    private = tmp_path / "private"
    private.mkdir()
    (private / "profile.yaml").write_text("skills:\n  SQL: {level: strong, evidence: x}\n")
    monkeypatch.setattr(scheduler, "get_settings", lambda: SimpleNamespace(config_dir=cfg, private_dir=private))
    return cfg


def test_update_records_digest(session: Session, config_dir: Path, monkeypatch) -> None:
    tracked = add_job(session, "Shortlisted Analyst", [("greenhouse", "acme", NOW)])
    session.add(JobStatus(job_id=tracked.id, status="shortlisted"))
    session.flush()

    def fake_boards(sess, boards, out):
        add_job(sess, "Data Analyst", [("greenhouse", "acme", datetime.now(UTC))], first_seen_at=datetime.now(UTC))
        add_job(sess, "Barista", [("greenhouse", "acme", datetime.now(UTC))], first_seen_at=datetime.now(UTC))
        tracked.is_active, tracked.expired_at = False, datetime.now(UTC)
        sess.flush()
        return 0

    monkeypatch.setattr(scheduler, "run_boards", fake_boards)
    lines: list[str] = []
    run = scheduler.run_update(session, "manual", lines.append)
    assert run.status == "succeeded", run.error
    data_analyst = session.scalar(select(Job).where(Job.title == "Data Analyst"))
    assert run.new_jobs == 2 and run.new_recommended_ids == [data_analyst.id]
    assert run.closed_tracked_ids == [tracked.id] and run.closed_jobs == 1
    assert "update succeeded" in lines[-1]


def test_update_failure_is_recorded(session: Session, config_dir: Path, monkeypatch) -> None:
    def boom(*_a):
        raise RuntimeError("source config broke")

    monkeypatch.setattr(scheduler, "run_boards", boom)
    run = scheduler.run_update(session, "schedule", lambda _l: None)
    assert run.status == "failed" and "source config broke" in run.error and run.finished_at


def test_only_one_update_at_a_time(session: Session, engine, config_dir: Path) -> None:
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": scheduler.LOCK_KEY})
        try:
            assert scheduler.run_update(session, "manual") is None
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": scheduler.LOCK_KEY})


def test_next_due_and_stuck_runs(session: Session) -> None:
    session.get(UserSettings, 1).search_frequency_hours = 24
    assert scheduler.next_due(session, NOW) == NOW  # never ran
    session.add(ScheduledRun(trigger="schedule", status="succeeded", started_at=NOW - timedelta(hours=5)))
    session.flush()
    assert scheduler.next_due(session, NOW) == NOW + timedelta(hours=19)
    # A failed attempt after the last success waits an hour before retrying.
    session.add(ScheduledRun(trigger="schedule", status="failed", started_at=NOW - timedelta(hours=30) + timedelta(hours=29)))
    session.flush()
    assert scheduler.next_due(session, NOW) == NOW + timedelta(hours=19)
    stuck = ScheduledRun(trigger="schedule", status="running", started_at=NOW - timedelta(hours=4))
    session.add(stuck)
    session.flush()
    assert scheduler.mark_stuck_runs(session, NOW) == 1
    session.refresh(stuck)
    assert stuck.status == "failed" and "interrupted" in stuck.error


def test_schedule_config_validation(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text("expire_after_days: {github_list: 0}\n")
    with pytest.raises(ValueError, match="whole days"):
        scheduler.load_schedule(p)
    assert scheduler.load_schedule(REPO / "config" / "schedule.yaml").run_adzuna is False


# ---------- API ----------

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


def test_updates_endpoint_and_digest(client: TestClient, session: Session) -> None:
    assert client.get("/api/updates").json()["digest"] is None
    session.add(ScheduledRun(trigger="schedule", status="succeeded", started_at=NOW, finished_at=NOW,
                             new_jobs=3, new_recommended_ids=[], closed_tracked_ids=[]))
    session.flush()
    body = client.get("/api/updates").json()
    assert body["digest"]["run"]["new_jobs"] == 3 and body["running"] is False and body["next_due_at"]


def test_update_now_starts_once(client: TestClient, session: Session, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(api, "run_update", lambda sess, trigger: calls.append(trigger))
    assert client.post("/api/updates").status_code == 202 and calls == ["manual"]
    session.add(ScheduledRun(trigger="schedule", status="running", started_at=datetime.now(UTC)))
    session.flush()
    assert client.post("/api/updates").status_code == 409
