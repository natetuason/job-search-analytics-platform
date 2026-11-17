"""Close listings whose only sources (lists, sheets, Adzuna) have stopped listing them.

Employer boards (Greenhouse, Lever, Ashby) close their own postings during ingestion; this handles the rest.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.pipeline import ATS_KINDS
from app.models import IngestionRun, Job, JobSource

# Sources that are re-fetched in full, so "missing from a later fetch" means removed.
_REFETCHED = ("github_list", "google_sheet")


def expire_unlisted(session: Session, expire_after_days: dict[str, int], now: datetime | None = None) -> int:
    """Mark jobs inactive when every source they came from is stale. Returns how many were closed."""
    now = now or datetime.now(UTC)
    has_ats = select(JobSource.job_id).where(JobSource.source_kind.in_(ATS_KINDS))
    rows = session.execute(
        select(JobSource.job_id, JobSource.source_kind, JobSource.source_ref, JobSource.last_seen_at)
        .join(Job, Job.id == JobSource.job_id)
        .where(Job.is_active.is_(True), JobSource.job_id.not_in(has_ats))
    ).all()
    last_success = dict(session.execute(
        select(func.concat(IngestionRun.source_kind, "|", IngestionRun.source_ref), func.max(IngestionRun.started_at))
        .where(IngestionRun.status == "succeeded", IngestionRun.source_kind.in_(_REFETCHED))
        .group_by(IngestionRun.source_kind, IngestionRun.source_ref)
    ).all())

    def stale(kind: str, ref: str | None, last_seen: datetime) -> bool:
        days = expire_after_days.get(kind)
        if days is None:  # CSV imports, pasted links: I added them, I close them
            return False
        if last_seen > now - timedelta(days=days):
            return False
        if kind in _REFETCHED:
            fetched = last_success.get(f"{kind}|{ref}")
            return fetched is not None and fetched > last_seen
        return True

    by_job: dict[int, list[bool]] = defaultdict(list)
    for job_id, kind, ref, last_seen in rows:
        by_job[job_id].append(stale(kind, ref, last_seen))
    closing = [job_id for job_id, flags in by_job.items() if all(flags)]
    if closing:
        for start in range(0, len(closing), 1000):
            session.query(Job).filter(Job.id.in_(closing[start:start + 1000])).update(
                {Job.is_active: False, Job.expired_at: now, Job.last_checked_at: now}, synchronize_session=False)
        session.commit()
    return len(closing)
