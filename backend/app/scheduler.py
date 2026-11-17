"""Scheduled updates: fetch every source, close listings that are gone, rescore, and record what's new.

  python -m app.scheduler            # the worker: runs an update whenever one is due (Settings: search frequency)
  python -m app.scheduler --once     # run one update now and exit

Only one update runs at a time (a Postgres advisory lock), whether started by the worker or the Dashboard.
Nothing here contacts an application form; it only reads job listings, like `ingest all`.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_sessionmaker
from app.freshness import expire_unlisted
from app.ingestion.batch import load_adzuna, load_companies, load_lists, load_sheets, run_adzuna, run_boards, run_lists, run_sheets
from app.log import configure_logging, redact
from app.models import Job, JobScore, JobStatus, ScheduledRun, UserSettings
from app.scoring.rules import SCORER
from app.scoring.run import score_all

logger = logging.getLogger(__name__)

LOCK_KEY = 7_310_001  # arbitrary, app-wide advisory lock id for "an update is running"
TRACKED = ("shortlisted", "applied", "interviewing", "offer")
CHECK_EVERY_SECONDS = 300
RETRY_FAILED_AFTER = timedelta(hours=1)
STUCK_AFTER = timedelta(hours=3)


@dataclass
class ScheduleConfig:
    run_adzuna: bool = False
    expire_after_days: dict[str, int] = field(default_factory=dict)


def load_schedule(path: Path) -> ScheduleConfig:
    data = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    days = data.get("expire_after_days") or {}
    if not isinstance(days, dict) or any(not isinstance(v, int) or v < 1 for v in days.values()):
        raise ValueError(f"{path.name}: expire_after_days must map source kinds to whole days (1 or more)")
    return ScheduleConfig(bool(data.get("run_adzuna", False)), {str(k): int(v) for k, v in days.items()})


def _log(line: str) -> None:
    logger.info(line)


def run_update(session: Session, trigger: str, out: Callable[[str], None] = _log) -> ScheduledRun | None:
    """Run one full update. Returns the recorded run, or None if another update is already running."""
    lock_conn = session.get_bind().engine.connect()
    try:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return None
        try:
            return _update(session, trigger, out)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    finally:
        lock_conn.close()


def _update(session: Session, trigger: str, out: Callable[[str], None]) -> ScheduledRun:
    settings = get_settings()
    cfg_dir = settings.config_dir
    started = datetime.now(UTC)
    run = ScheduledRun(trigger=trigger, status="running", started_at=started)
    session.add(run)
    session.commit()
    try:
        sched = load_schedule(cfg_dir / "schedule.yaml")
        boards = load_companies(cfg_dir / "companies.yaml")
        sheets = load_sheets(cfg_dir / "sheets.yaml")
        lists = load_lists(cfg_dir / "lists.yaml")
        failed = run_boards(session, boards, out) + run_sheets(session, sheets, out) + run_lists(session, lists, out)
        total = len(boards) + len(sheets) + len(lists)
        if sched.run_adzuna:
            adzuna = load_adzuna(cfg_dir / "adzuna.yaml")
            failed += run_adzuna(session, adzuna, None, out)
            total += len(adzuna.searches)
        closed_unlisted = expire_unlisted(session, sched.expire_after_days, started)
        scored = score_all(session, cfg_dir, settings.private_dir / "profile.yaml")

        run.sources_ok, run.sources_failed = total - failed, failed
        run.new_jobs = session.scalar(select(func.count()).select_from(Job).where(Job.first_seen_at >= started)) or 0
        run.closed_jobs = session.scalar(select(func.count()).select_from(Job).where(
            Job.is_active.is_(False), Job.expired_at >= started)) or 0
        run.new_recommended_ids = list(session.scalars(
            select(Job.id).join(JobScore, JobScore.job_id == Job.id)
            .where(Job.first_seen_at >= started, Job.is_active.is_(True), JobScore.scorer == SCORER,
                   JobScore.recommendation.in_(("apply", "maybe")))
            .order_by(JobScore.score.desc())))
        run.closed_tracked_ids = list(session.scalars(
            select(Job.id).join(JobStatus, JobStatus.job_id == Job.id)
            .where(Job.is_active.is_(False), Job.expired_at >= started, JobStatus.status.in_(TRACKED))))
        run.details = {"closed_unlisted": closed_unlisted, "scored": scored.scored,
                       "by_recommendation": scored.by_recommendation, "adzuna": sched.run_adzuna}
        run.status = "succeeded"
    except Exception as exc:  # recorded on the run and shown on the Dashboard
        session.rollback()
        logger.exception("scheduled update failed")
        run.status, run.error = "failed", redact(f"{type(exc).__name__}: {exc}")[:1000]
    run.finished_at = datetime.now(UTC)
    session.add(run)
    session.commit()
    out(f"update {run.status}: {run.sources_ok} sources ok, {run.sources_failed} failed, {run.new_jobs} new jobs "
        f"({len(run.new_recommended_ids)} Apply/Maybe), {run.closed_jobs} closed")
    return run


def mark_stuck_runs(session: Session, now: datetime | None = None) -> int:
    """Runs left 'running' by a crash or shutdown are marked failed so they don't block the schedule."""
    now = now or datetime.now(UTC)
    result = session.execute(
        update(ScheduledRun)
        .where(ScheduledRun.status == "running", ScheduledRun.started_at < now - STUCK_AFTER)
        .values(status="failed", finished_at=now, error="interrupted (the worker stopped mid-update)"))
    session.commit()
    return result.rowcount or 0


def next_due(session: Session, now: datetime | None = None) -> datetime:
    """When the next scheduled update should run: one interval after the last success,
    but a failed attempt waits an hour before retrying."""
    now = now or datetime.now(UTC)
    hours = session.scalar(select(UserSettings.search_frequency_hours).where(UserSettings.id == 1)) or 24
    last_ok = session.scalar(select(func.max(ScheduledRun.started_at)).where(ScheduledRun.status == "succeeded"))
    last_try = session.scalar(select(func.max(ScheduledRun.started_at)))
    due = last_ok + timedelta(hours=hours) if last_ok else now
    if last_try and (last_ok is None or last_try > last_ok):
        due = max(due, last_try + RETRY_FAILED_AFTER)
    return due


def worker() -> None:
    """Check every few minutes and run an update when one is due. Survives the Mac sleeping:
    after waking, an overdue update runs on the next check."""
    logger.info("worker started")
    while True:
        try:
            with get_sessionmaker()() as session:
                mark_stuck_runs(session)
                if next_due(session) <= datetime.now(UTC):
                    run_update(session, "schedule")
        except Exception:  # keep the worker alive; the next check retries
            logger.exception("worker check failed")
        time.sleep(CHECK_EVERY_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scheduler", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run one update now and exit")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    if not args.once:
        worker()
        return 0
    with get_sessionmaker()() as session:
        run = run_update(session, "manual", print)
    if run is None:
        print("another update is already running")
        return 1
    return 0 if run.status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
