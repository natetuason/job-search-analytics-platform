"""Database constraints that protect data integrity."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CandidateProfile, Company, Job, JobScore, JobSource, JobStatus, StatusEvent, UserSettings


def make_job(session: Session, dedup_key: str = "k1", **overrides) -> Job:
    company = session.scalar(select(Company).where(Company.normalized_name == "acme"))
    if company is None:
        company = Company(name="Acme Inc.", normalized_name="acme", ats_type="greenhouse", ats_token="acme")
        session.add(company)
        session.flush()
    fields = dict(
        company_id=company.id,
        title="Data Analyst",
        normalized_title="data analyst",
        apply_url="https://boards.greenhouse.io/acme/jobs/1?gh_src=x",
        canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1",
        dedup_key=dedup_key,
    )
    fields.update(overrides)
    job = Job(**fields)
    session.add(job)
    session.flush()
    return job


def assert_rejected(session: Session, obj) -> None:
    session.add(obj)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_seeded_settings_leave_personal_facts_blank(session: Session) -> None:
    s = session.get(UserSettings, 1)
    assert s is not None
    assert s.graduation_date is None
    assert s.work_authorization is None
    assert s.needs_sponsorship is None
    assert s.open_to_relocation is None
    assert s.salary_min == Decimal("90000")
    assert sum(s.scoring_weights.values()) == 100
    assert "Data Analyst" in s.target_titles_primary
    assert "Demand Planning Analyst" in s.target_titles_primary
    assert "Associate Product Manager" in s.target_titles_secondary
    assert {"Risk Analyst", "Technology Analyst", "Project Analyst"} <= set(s.target_titles_primary)
    assert {"Product Manager", "Project Manager"} <= set(s.target_titles_secondary)
    assert len(s.target_titles_primary) == len(set(s.target_titles_primary))
    assert not set(s.target_titles_primary) & set(s.target_titles_secondary)
    assert s.preferred_locations == ["New York, NY", "Jersey City, NJ", "NJ"]
    assert "Chicago, IL" in s.secondary_locations
    assert s.secondary_location_min_score == Decimal("35")
    assert session.get(CandidateProfile, 1) is not None


def test_settings_is_single_row(session: Session) -> None:
    assert_rejected(session, UserSettings(id=2))


def test_job_defaults(session: Session) -> None:
    job = make_job(session)
    session.refresh(job)
    assert job.is_active is True
    assert job.work_arrangement == "unknown"
    assert job.required_quals == []
    assert job.first_seen_at is not None


def test_duplicate_dedup_key_rejected(session: Session) -> None:
    make_job(session, dedup_key="same")
    company_id = session.scalar(select(Company.id))
    assert_rejected(
        session,
        Job(
            company_id=company_id,
            title="Data Analyst",
            normalized_title="data analyst",
            apply_url="u",
            canonical_apply_url="u",
            dedup_key="same",
        ),
    )


def test_invalid_work_arrangement_rejected(session: Session) -> None:
    with pytest.raises(IntegrityError):
        make_job(session, work_arrangement="sometimes")


def test_estimate_requires_confidence(session: Session) -> None:
    with pytest.raises(IntegrityError):
        make_job(session, est_base_min=Decimal("90000"), est_base_max=Decimal("110000"))
    session.rollback()
    job = make_job(session, est_base_min=Decimal("90000"), est_base_max=Decimal("110000"), est_confidence="low")
    assert job.base_min is None  # estimate never lands in the confirmed columns


def test_inverted_base_range_rejected(session: Session) -> None:
    with pytest.raises(IntegrityError):
        make_job(session, base_min=Decimal("120000"), base_max=Decimal("100000"))


def test_same_source_listing_recorded_once(session: Session) -> None:
    job = make_job(session)
    session.add(JobSource(job_id=job.id, source_kind="greenhouse", external_id="123"))
    session.flush()
    assert_rejected(session, JobSource(job_id=job.id, source_kind="greenhouse", external_id="123"))


def test_one_status_record_per_job(session: Session) -> None:
    job = make_job(session)
    session.add(JobStatus(job_id=job.id, status="shortlisted"))
    session.flush()
    session.expunge_all()
    assert_rejected(session, JobStatus(job_id=job.id, status="new"))


def test_applied_requires_date(session: Session) -> None:
    job = make_job(session)
    assert_rejected(session, JobStatus(job_id=job.id, status="applied"))
    job = make_job(session, dedup_key="k2")
    session.add(JobStatus(job_id=job.id, status="applied", applied_at=datetime.now(UTC)))
    session.flush()


def test_invalid_status_rejected(session: Session) -> None:
    job = make_job(session)
    assert_rejected(session, StatusEvent(job_id=job.id, from_status="new", to_status="ghosted"))


def test_score_constraints(session: Session) -> None:
    job = make_job(session)
    base = dict(job_id=job.id, settings_version=1, breakdown={}, explanation={}, recommendation="apply")
    assert_rejected(session, JobScore(scorer="rules-v1", score=Decimal("101"), tier="reach", **base))
    job = make_job(session, dedup_key="k2")
    base["job_id"] = job.id
    assert_rejected(session, JobScore(scorer="rules-v1", score=Decimal("80"), tier="likely", **base))
    job = make_job(session, dedup_key="k3")
    base["job_id"] = job.id
    session.add(JobScore(scorer="rules-v1", score=Decimal("80"), tier="strong_fit", **base))
    session.flush()
    assert_rejected(session, JobScore(scorer="rules-v1", score=Decimal("70"), tier="competitive", **base))


def test_deleting_job_cascades(session: Session) -> None:
    job = make_job(session)
    session.add_all([
        JobSource(job_id=job.id, source_kind="lever", external_id="abc"),
        JobStatus(job_id=job.id, status="new"),
        StatusEvent(job_id=job.id, from_status=None, to_status="new"),
    ])
    session.flush()
    session.delete(job)
    session.flush()
    session.expunge_all()
    assert session.scalar(select(JobSource)) is None
    assert session.scalar(select(StatusEvent)) is None
