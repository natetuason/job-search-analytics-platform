from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.pipeline import ingest
from app.models import Company, IngestionRun, Job, JobSource
from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, SourceError

POLICY = AccessPolicy(name="fake", min_interval_s=0)


class FakeSource(Source):
    policy = POLICY

    def __init__(self, kind: str, jobs: list[RawJob] | Exception, authoritative: bool = True):
        self.kind = kind
        self.authoritative = authoritative
        self.jobs = jobs

    def fetch(self, ref, company_name=None):
        if isinstance(self.jobs, Exception):
            raise self.jobs
        return list(self.jobs)


def raw(kind: str = "greenhouse", ext: str = "acme:1", **kw) -> RawJob:
    fields = dict(
        source_kind=kind,
        external_id=ext,
        company_name="Acme Inc.",
        title="Data Analyst",
        apply_url="https://boards.greenhouse.io/acme/jobs/1?gh_src=li",
        location_raw="New York, NY",
        description_html="<p>SQL. This role is hybrid.</p>",
        posted_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    fields.update(kw)
    return RawJob(**fields)


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_ingest_creates_normalized_job(session: Session) -> None:
    run = ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    assert (run.status, run.fetched_count, run.new_count, run.updated_count) == ("succeeded", 1, 1, 0)
    job = session.scalar(select(Job))
    assert job.normalized_title == "data analyst"
    assert job.canonical_apply_url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.apply_url.endswith("gh_src=li")  # original link kept for the "Open application" button
    assert job.work_arrangement == "hybrid"
    assert job.description_text == "SQL. This role is hybrid."
    assert job.locations[0]["state"] == "NY"
    assert job.is_active
    company = session.get(Company, job.company_id)
    assert (company.normalized_name, company.ats_type, company.ats_token) == ("acme", "greenhouse", "acme")


def test_reingest_is_idempotent(session: Session) -> None:
    src = FakeSource("greenhouse", [raw()])
    ingest(session, src, "acme")
    run = ingest(session, src, "acme")
    assert (run.new_count, run.updated_count) == (0, 1)
    assert count(session, Job) == 1
    assert count(session, JobSource) == 1


def test_authoritative_refresh_updates_fields(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    ingest(session, FakeSource("greenhouse", [raw(title="Data Analyst, Pricing")]), "acme")
    job = session.scalar(select(Job))
    assert job.title == "Data Analyst, Pricing"
    assert job.normalized_title == "data analyst pricing"


def test_same_job_from_two_sources_is_merged(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    sheet_copy = raw(
        kind="google_sheet", ext="sheet1:row9", company_name="ACME", title="Data Analyst",
        apply_url="https://boards.greenhouse.io/acme/jobs/1?utm_source=sheet", location_raw="New York, New York",
        description_html=None,
    )
    run = ingest(session, FakeSource("google_sheet", [sheet_copy], authoritative=False), "sheet1")
    assert (run.new_count, run.updated_count) == (0, 1)
    assert count(session, Job) == 1
    assert sorted(s.source_kind for s in session.scalar(select(Job)).sources) == ["google_sheet", "greenhouse"]


def test_match_by_canonical_url_when_details_differ(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    other = raw(kind="manual", ext="m1", title="Analyst, Data", location_raw=None)
    ingest(session, FakeSource("manual", [other], authoritative=False), "pasted")
    assert count(session, Job) == 1


def test_non_authoritative_source_fills_gaps_without_overwriting(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw(description_html=None)]), "acme")
    filler = raw(kind="google_sheet", ext="s:1", description_html="<p>Sheet description</p>",
                 base_min=Decimal("100000"), base_max=Decimal("120000"), pay_period="year")
    ingest(session, FakeSource("google_sheet", [filler], authoritative=False), "s")
    job = session.scalar(select(Job))
    assert job.description_text == "Sheet description"
    assert job.title == "Data Analyst"


def test_missing_postings_are_expired_and_reactivated(session: Session) -> None:
    second = raw(ext="acme:2", title="Business Analyst", apply_url="https://boards.greenhouse.io/acme/jobs/2")
    ingest(session, FakeSource("greenhouse", [raw(), second]), "acme")
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    gone = session.scalar(select(Job).where(Job.title == "Business Analyst"))
    session.refresh(gone)
    assert gone.is_active is False and gone.expired_at is not None
    assert session.scalar(select(Job).where(Job.title == "Data Analyst")).is_active

    ingest(session, FakeSource("greenhouse", [raw(), second]), "acme")
    session.refresh(gone)
    assert gone.is_active is True and gone.expired_at is None


def test_other_boards_are_not_expired(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    other = raw(ext="beta:1", company_name="Beta", apply_url="https://boards.greenhouse.io/beta/jobs/1")
    ingest(session, FakeSource("greenhouse", [other]), "beta")
    assert all(j.is_active for j in session.scalars(select(Job)))


def test_failed_fetch_is_recorded_and_expires_nothing(session: Session) -> None:
    ingest(session, FakeSource("greenhouse", [raw()]), "acme")
    run = ingest(session, FakeSource("greenhouse", SourceError("greenhouse: HTTP 503 after retries", 503)), "acme")
    assert run.status == "failed"
    assert "503" in run.errors[0]["error"]
    assert session.scalar(select(Job)).is_active


def test_one_bad_posting_does_not_abort_run(session: Session) -> None:
    bad = raw(ext="acme:bad", title="x" * 600)  # longer than the title column allows
    good = raw(ext="acme:2", title="Business Analyst", apply_url="https://boards.greenhouse.io/acme/jobs/2")
    run = ingest(session, FakeSource("greenhouse", [bad, good]), "acme")
    assert run.status == "succeeded"
    assert run.new_count == 1
    assert run.errors[0]["external_id"] == "acme:bad"
    assert session.scalar(select(Job.title)) == "Business Analyst"


def test_runs_are_logged(session: Session) -> None:
    ingest(session, FakeSource("lever", [replace(raw(kind="lever", ext="acme:x"))]), "acme")
    run = session.scalar(select(IngestionRun))
    assert (run.source_kind, run.source_ref, run.status) == ("lever", "acme", "succeeded")
    assert run.finished_at is not None
