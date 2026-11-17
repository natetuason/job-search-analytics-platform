"""Fetch from a source, normalize, de-duplicate, and store. No scoring happens here."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ingestion.normalize import (
    dedup_key,
    detect_employment_type,
    detect_work_arrangement,
    html_to_text,
    location_key,
    normalize_company,
    normalize_title,
    parse_locations,
)
from app.ingestion.urls import canonicalize_url
from app.log import redact
from app.models import Company, IngestionRun, Job, JobSource
from app.sources.base import RawJob, Source
from app.sources.http import SourceError

logger = logging.getLogger(__name__)

# Fields a source refresh is allowed to overwrite on an existing job.
_REFRESHABLE = (
    "title", "normalized_title", "location_raw", "locations", "work_arrangement", "employment_type",
    "description_text", "posted_at", "deadline", "apply_url", "canonical_apply_url",
    "base_min", "base_max", "pay_period", "currency", "comp_parse_source",
)

_PAY_FIELDS = ("base_min", "base_max", "pay_period", "currency", "comp_parse_source")


@dataclass
class _Counts:
    fetched: int = 0
    new: int = 0
    updated: int = 0


def get_or_create_company(session: Session, name: str, ats_type: str | None = None, ats_token: str | None = None) -> Company:
    norm = normalize_company(name)
    company = session.scalar(select(Company).where(Company.normalized_name == norm))
    if company is None:
        company = Company(name=name, normalized_name=norm, ats_type=ats_type, ats_token=ats_token)
        session.add(company)
        session.flush()
    elif ats_type and not company.ats_type:
        company.ats_type, company.ats_token = ats_type, ats_token
    return company


def job_fields(raw: RawJob) -> dict[str, Any]:
    description = raw.description_text or html_to_text(raw.description_html)
    locations = parse_locations(raw.location_raw, raw.country_hint)
    return {
        "title": raw.title,
        "normalized_title": normalize_title(raw.title),
        "location_raw": raw.location_raw,
        "locations": locations,
        "work_arrangement": detect_work_arrangement(raw.work_arrangement_hint, raw.location_raw, raw.title, description),
        "employment_type": detect_employment_type(raw.employment_type_hint, raw.title),
        "description_text": description or None,
        "posted_at": raw.posted_at,
        "deadline": raw.deadline,
        "apply_url": raw.apply_url,
        "canonical_apply_url": canonicalize_url(raw.apply_url),
        "base_min": raw.base_min,
        "base_max": raw.base_max,
        "pay_period": raw.pay_period,
        "currency": raw.currency,
        "comp_parse_source": raw.comp_parse_source,
        "dedup_key": dedup_key(raw.company_name, raw.title, location_key(locations, raw.location_raw)),
    }


def _upsert(session: Session, source: Source, ref: str, raw: RawJob, now: datetime, counts: _Counts) -> None:
    fields = job_fields(raw)
    existing_source = session.scalar(
        select(JobSource).where(JobSource.source_kind == raw.source_kind, JobSource.external_id == raw.external_id)
    )
    if existing_source is not None:
        job = existing_source.job
        if source.authoritative:
            for name in _REFRESHABLE:
                setattr(job, name, fields[name])
            # Keep the dedup key in sync unless another job already owns the new one.
            if fields["dedup_key"] != job.dedup_key and not session.scalar(
                select(Job.id).where(Job.dedup_key == fields["dedup_key"])
            ):
                job.dedup_key = fields["dedup_key"]
        else:
            _fill_gaps(job, fields)
        existing_source.raw_payload = raw.raw
        existing_source.source_url = raw.source_url
        existing_source.source_ref = ref
        existing_source.last_seen_at = now
        _mark_seen(session, job, now, source.authoritative)
        counts.updated += 1
        return

    company = get_or_create_company(session, raw.company_name, *(_ats(source, ref)))
    job = session.scalar(select(Job).where(Job.dedup_key == fields["dedup_key"])) or session.scalar(
        select(Job).where(Job.canonical_apply_url == fields["canonical_apply_url"]).limit(1)
    )
    if job is None:
        job = Job(company_id=company.id, first_seen_at=now, **fields)
        session.add(job)
        session.flush()
        counts.new += 1
    else:
        _fill_gaps(job, fields)
        counts.updated += 1
    _mark_seen(session, job, now, source.authoritative)
    session.add(JobSource(
        job_id=job.id, source_kind=raw.source_kind, external_id=raw.external_id, source_ref=ref,
        source_url=raw.source_url, raw_payload=raw.raw, first_seen_at=now, last_seen_at=now,
    ))


def _fill_gaps(job: Job, fields: dict[str, Any]) -> None:
    """A non-authoritative or secondary source only fills empty fields, never overwrites."""
    for name in _REFRESHABLE:
        if name in _PAY_FIELDS:
            continue
        if getattr(job, name) in (None, [], "unknown") and fields[name] not in (None, [], "unknown"):
            setattr(job, name, fields[name])
    # Pay is all-or-nothing so a range never mixes values from two sources.
    if job.base_min is None and job.base_max is None and fields["base_min"] is not None:
        for name in _PAY_FIELDS:
            setattr(job, name, fields[name])


def _ats(source: Source, ref: str) -> tuple[str | None, str | None]:
    return (source.kind, ref) if source.kind in ATS_KINDS else (None, None)


ATS_KINDS = ("greenhouse", "lever", "ashby")


def _mark_seen(session: Session, job: Job, now: datetime, authoritative: bool) -> None:
    job.last_seen_at = now
    job.last_checked_at = now
    if job.is_active:
        return
    # Only the employer's own board can reopen a posting it closed; a list that still mentions it can't.
    if authoritative or not session.scalar(
        select(JobSource.id).where(JobSource.job_id == job.id, JobSource.source_kind.in_(ATS_KINDS)).limit(1)
    ):
        job.is_active, job.expired_at = True, None


def _expire_missing(session: Session, source: Source, ref: str, run_started: datetime) -> int:
    """After a complete board fetch, postings the board no longer lists are inactive."""
    stale_job_ids = select(JobSource.job_id).where(
        JobSource.source_kind == source.kind,
        JobSource.source_ref == ref,
        JobSource.last_seen_at < run_started,
    )
    result = session.execute(
        update(Job)
        .where(Job.id.in_(stale_job_ids), Job.is_active.is_(True))
        .values(is_active=False, expired_at=run_started, last_checked_at=run_started)
    )
    return result.rowcount or 0


def ingest(session: Session, source: Source, ref: str, company_name: str | None = None) -> IngestionRun:
    """Run one fetch for one ref. Always returns the recorded run; never raises on source errors."""
    now = datetime.now(UTC)
    run = IngestionRun(source_kind=source.kind, source_ref=ref, status="running", started_at=now)
    session.add(run)
    session.commit()

    counts = _Counts()
    errors: list[dict[str, str]] = []
    client = getattr(source, "client", None)
    calls_before = getattr(client, "requests_made", None)
    try:
        raws = source.fetch(ref, company_name)
    except SourceError as exc:
        if calls_before is not None:
            run.api_calls = client.requests_made - calls_before
        run.status, run.finished_at = "failed", datetime.now(UTC)
        run.errors = [{"stage": "fetch", "error": redact(str(exc))}]
        session.commit()
        logger.warning("ingestion fetch failed", extra={"fields": {"source": source.kind, "ref": ref, "error": str(exc)}})
        return run

    if calls_before is not None:
        run.api_calls = client.requests_made - calls_before
    counts.fetched = len(raws)
    for raw in raws:
        try:
            with session.begin_nested():
                _upsert(session, source, ref, raw, now, counts)
        except Exception as exc:  # one bad posting must not abort the run
            errors.append({"stage": "upsert", "external_id": raw.external_id, "error": redact(f"{type(exc).__name__}: {exc}")[:500]})

    expired = _expire_missing(session, source, ref, now) if source.authoritative else 0
    run.fetched_count, run.new_count, run.updated_count = counts.fetched, counts.new, counts.updated
    run.errors = errors
    run.status, run.finished_at = "succeeded", datetime.now(UTC)
    session.commit()
    logger.info(
        "ingestion finished",
        extra={"fields": {"source": source.kind, "ref": ref, "fetched": counts.fetched, "new": counts.new,
                          "updated": counts.updated, "expired": expired, "errors": len(errors)}},
    )
    return run
