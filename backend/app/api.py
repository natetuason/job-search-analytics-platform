"""JSON API for the web app: ranked jobs, job detail, status tracking, settings, dashboard, CSV export, apply hub.

Read-only toward employers: nothing here contacts an application page. "Applied" is only ever set by me.
"""

import csv
import io
import logging
import threading
from pathlib import Path
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.backlog import APPLIED, write_backlog
from app.config import get_settings
from app.feedback import REASON_LABEL, skip_feedback
from app.db import get_session, get_sessionmaker
from app.hub import load_hub
from app.models import (
    Company, IngestionRun, Job, JobScore, JobSource, JobStatus, ResumeVersion, ScheduledRun, StatusEvent, UserSettings,
)
from app.models.enums import JOB_STATUSES, RECOMMENDATIONS, SKIP_REASONS, TIERS
from app.scheduler import next_due, run_update
from app.scoring.rules import CATEGORIES, SCORER
from app.scoring.run import score_all

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

Status = Literal["new", "shortlisted", "applied", "interviewing", "offer", "rejected", "skipped", "expired"]
assert set(Status.__args__) == set(JOB_STATUSES)  # type: ignore[attr-defined]
LOCATION_CLASSES = ("preferred", "remote", "secondary", "other", "foreign")
NEW_FOR = timedelta(days=2)  # how long a job wears the "New" badge
TRACKED_STATUSES = ("shortlisted", "applied", "interviewing", "offer", "rejected")


def get_session_factory() -> sessionmaker[Session]:
    """Sessions for work that outlives a request (background rescoring). Overridden in tests."""
    return get_sessionmaker()


# ---------- helpers ----------

def _num(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _csv_param(value: str | None, allowed: tuple[str, ...], name: str) -> list[str] | None:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise HTTPException(422, f"{name}: unknown value(s) {', '.join(bad)}; allowed: {', '.join(allowed)}")
    return items


def _pay(job: Job) -> dict[str, Any] | None:
    """Posted pay if the posting states it, else a labeled estimate, else None."""
    if job.base_min is not None:
        return {"kind": "posted", "min": _num(job.base_min), "max": _num(job.base_max), "period": job.pay_period}
    if job.est_base_min is not None:
        return {"kind": "estimate", "min": _num(job.est_base_min), "max": _num(job.est_base_max),
                "period": job.est_pay_period, "confidence": job.est_confidence}
    return None


def _job_row(score: JobScore, job: Job, company: Company, status: JobStatus | None, group_size: int = 1) -> dict[str, Any]:
    return {
        "other_locations": group_size - 1,
        "id": job.id, "title": job.title, "company": company.name, "location": job.location_raw,
        "location_class": score.breakdown.get("location_class"), "work_arrangement": job.work_arrangement,
        "score": _num(score.score), "tier": score.tier, "recommendation": score.recommendation,
        "summary": score.explanation.get("summary"), "pay": _pay(job),
        "flags": [{"label": f["label"], "status": f["status"]} for f in score.hard_flags],
        "rotational": bool(score.explanation.get("rotational")), "early_career": job.is_early_career_program,
        "status": status.status if status else "new",
        "applied_at": status.applied_at.isoformat() if status and status.applied_at else None,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "first_seen_at": job.first_seen_at.isoformat(), "apply_url": job.apply_url,
        "is_active": job.is_active, "closed_at": job.expired_at.isoformat() if job.expired_at else None,
        "is_new": job.first_seen_at >= datetime.now(UTC) - NEW_FOR,
    }


def _jobs_query(rec: list[str] | None, tier: list[str] | None, status: list[str] | None, loc: list[str] | None,
                q: str | None, rotational: bool | None) -> Select:
    stmt = (select(JobScore, Job, Company, JobStatus)
            .join(Job, Job.id == JobScore.job_id).join(Company, Company.id == Job.company_id)
            .outerjoin(JobStatus, JobStatus.job_id == Job.id)
            .where(JobScore.scorer == SCORER,
                   # A closed listing stays visible if I'm tracking it (shortlisted, applied, ...).
                   or_(Job.is_active.is_(True), JobStatus.status.in_(TRACKED_STATUSES))))
    if rec:
        stmt = stmt.where(JobScore.recommendation.in_(rec))
    if tier:
        stmt = stmt.where(JobScore.tier.in_(tier))
    if status:
        cond = JobStatus.status.in_(status)
        stmt = stmt.where(or_(cond, JobStatus.job_id.is_(None)) if "new" in status else cond)
    if loc:
        stmt = stmt.where(JobScore.breakdown["location_class"].astext.in_(loc))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Job.title.ilike(like), Company.name.ilike(like), Job.location_raw.ilike(like)))
    if rotational:
        stmt = stmt.where(JobScore.explanation["rotational"].as_boolean().is_(True))
    return stmt


_SORTS = {
    "score": (JobScore.score.desc(), Job.posted_at.desc().nulls_last()),
    "posted": (Job.posted_at.desc().nulls_last(), JobScore.score.desc()),
    "found": (Job.first_seen_at.desc(), JobScore.score.desc()),
}


def _group_same_role(stmt: Select) -> Select:
    """One row per company + title (the best-scoring of its locations), plus how many postings share it.

    Employers often post one role once per city; those are separate postings with separate links,
    so they're collapsed here for the list, not merged in the database.
    """
    key = (Job.company_id, func.lower(Job.title))
    ranked = stmt.with_only_columns(
        Job.id.label("job_id"),
        func.row_number().over(partition_by=key, order_by=(JobScore.score.desc(), Job.id)).label("rn"),
        func.count().over(partition_by=key).label("n"),
    ).subquery()
    return stmt.join(ranked, ranked.c.job_id == Job.id).where(ranked.c.rn == 1).add_columns(ranked.c.n)


class JobFilters:
    def __init__(self, rec: str | None = "apply,maybe", tier: str | None = None, status: str | None = None,
                 location: str | None = None, q: str | None = Query(None, max_length=200),
                 rotational: bool | None = None, sort: Literal["score", "posted", "found"] = "score",
                 group: bool = True):
        stmt = _jobs_query(_csv_param(rec, RECOMMENDATIONS, "rec"), _csv_param(tier, TIERS, "tier"),
                           _csv_param(status, JOB_STATUSES, "status"),
                           _csv_param(location, LOCATION_CLASSES, "location"), q, rotational)
        statuses = _csv_param(status, JOB_STATUSES, "status")
        if group and statuses:
            # Once I've acted on one city's posting of a role, its other cities follow it out of (or into) a view.
            sibling, sib_status = aliased(Job), aliased(JobStatus)
            stmt = stmt.where(~exists().where(
                sibling.company_id == Job.company_id, func.lower(sibling.title) == func.lower(Job.title),
                sibling.id != Job.id, sib_status.job_id == sibling.id, sib_status.status.not_in(statuses)))
        self.stmt = _group_same_role(stmt) if group else stmt
        self.order = _SORTS[sort]


# ---------- jobs ----------

@router.get("/jobs")
def list_jobs(filters: JobFilters = Depends(), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    total = session.scalar(select(func.count()).select_from(filters.stmt.subquery()))
    rows = session.execute(filters.stmt.order_by(*filters.order).limit(limit).offset(offset)).all()
    return {"total": total, "items": [_job_row(*r) for r in rows]}


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    score = session.scalar(select(JobScore).where(JobScore.job_id == job_id, JobScore.scorer == SCORER))
    status = session.get(JobStatus, job_id)
    events = session.scalars(select(StatusEvent).where(StatusEvent.job_id == job_id).order_by(StatusEvent.at.desc()))
    sources = session.scalars(select(JobSource).where(JobSource.job_id == job_id).order_by(JobSource.first_seen_at))
    resume = session.get(ResumeVersion, status.resume_version_id) if status and status.resume_version_id else None
    return {
        **(_job_row(score, job, job.company, status) if score else
           {"id": job.id, "title": job.title, "company": job.company.name, "location": job.location_raw,
            "apply_url": job.apply_url, "status": status.status if status else "new", "pay": _pay(job)}),
        "is_active": job.is_active,
        "description": job.description_text,
        "employment_type": job.employment_type,
        "experience": {"min_years": _num(job.exp_min_years), "max_years": _num(job.exp_max_years), "text": job.exp_text},
        "pay_estimate_evidence": job.est_evidence if job.base_min is None else None,
        "score_detail": None if not score else {
            "breakdown": score.breakdown, "explanation": score.explanation, "hard_flags": score.hard_flags,
            "computed_at": score.computed_at.isoformat(), "settings_version": score.settings_version,
        },
        "tracking": {
            "notes": status.notes if status else None,
            "resume_version": {"id": resume.id, "label": resume.label} if resume else None,
            "events": [{"from": e.from_status, "to": e.to_status, "at": e.at.isoformat(), "note": e.note,
                        "reasons": [REASON_LABEL[r] for r in e.reasons or []]} for e in events],
        },
        "sources": [{"kind": s.source_kind, "ref": s.source_ref, "url": s.source_url,
                     "first_seen_at": s.first_seen_at.isoformat()} for s in sources],
    }


class StatusUpdate(BaseModel):
    status: Status
    applied_at: date | None = None
    resume_version_id: int | None = None
    notes: str | None = Field(None, max_length=5000)
    note: str | None = Field(None, max_length=500, description="Optional note on this change, kept in the history")
    reasons: list[Literal[SKIP_REASONS]] | None = Field(None, description="Why I skipped (only with status skipped)")


def _refresh_backlog(factory: Callable[[], Session]) -> None:
    settings = get_settings()
    try:
        with factory() as session:
            write_backlog(session, settings.config_dir, settings.private_dir, settings.backlog_dir)
    except Exception:  # the status is saved either way; `python -m app.cli backlog` regenerates by hand
        logger.exception("backlog refresh failed")


@router.put("/jobs/{job_id}/status")
def set_status(job_id: int, body: StatusUpdate, tasks: BackgroundTasks, session: Session = Depends(get_session),
               factory: sessionmaker[Session] = Depends(get_session_factory)) -> dict[str, Any]:
    if not session.get(Job, job_id):
        raise HTTPException(404, "job not found")
    if body.resume_version_id is not None and not session.get(ResumeVersion, body.resume_version_id):
        raise HTTPException(422, "resume version not found")
    if body.reasons and body.status != "skipped":
        raise HTTPException(422, "reasons only go with status skipped")
    row = session.get(JobStatus, job_id)
    previous = row.status if row else None
    if row is None:
        row = JobStatus(job_id=job_id)
        session.add(row)
    row.status = body.status
    if body.applied_at:
        row.applied_at = datetime.combine(body.applied_at, datetime.min.time(), UTC)
    elif body.status == "applied" and row.applied_at is None:
        row.applied_at = datetime.now(UTC)
    if "resume_version_id" in body.model_fields_set:
        row.resume_version_id = body.resume_version_id
    if "notes" in body.model_fields_set:
        row.notes = body.notes
    reasons = list(dict.fromkeys(body.reasons or []))
    if previous != body.status or body.note or reasons:
        session.add(StatusEvent(job_id=job_id, from_status=previous, to_status=body.status, note=body.note,
                                reasons=reasons))
    session.commit()
    if body.status in APPLIED or previous in APPLIED:
        tasks.add_task(_refresh_backlog, factory)  # applications.md / .csv / rollup.md
    return {"job_id": job_id, "status": row.status, "applied_at": row.applied_at.isoformat() if row.applied_at else None}


# ---------- resume versions ----------

class ResumeVersionIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)


@router.get("/resume-versions")
def list_resume_versions(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [{"id": r.id, "label": r.label} for r in session.scalars(select(ResumeVersion).order_by(ResumeVersion.id))]


@router.post("/resume-versions", status_code=201)
def add_resume_version(body: ResumeVersionIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    label = body.label.strip()
    if session.scalar(select(ResumeVersion).where(ResumeVersion.label == label)):
        raise HTTPException(409, "a resume version with that label exists")
    r = ResumeVersion(label=label)
    session.add(r)
    session.commit()
    return {"id": r.id, "label": r.label}


# ---------- settings ----------

_SETTINGS_FIELDS = (
    "salary_min", "salary_preferred_min", "salary_preferred_max", "work_arrangements", "preferred_locations",
    "excluded_locations", "excluded_companies", "excluded_title_words", "secondary_locations", "secondary_location_min_score", "open_to_relocation",
    "target_titles_primary", "target_titles_secondary", "experience_max_years", "scoring_weights",
    "search_frequency_hours", "graduation_date", "work_authorization", "needs_sponsorship",
)


def _settings_out(s: UserSettings, session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in _SETTINGS_FIELDS:
        v = getattr(s, f)
        out[f] = float(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, date) else v
    stale = session.scalar(select(func.count()).select_from(JobScore).where(
        JobScore.scorer == SCORER, JobScore.settings_version < s.version))
    out["version"] = s.version
    out["scores_stale"] = bool(stale)
    return out


class SettingsUpdate(BaseModel):
    salary_min: float | None = Field(None, ge=0, le=1_000_000)
    salary_preferred_min: float | None = Field(None, ge=0, le=1_000_000)
    salary_preferred_max: float | None = Field(None, ge=0, le=1_000_000)
    work_arrangements: list[Literal["remote", "hybrid", "onsite"]] | None = None
    preferred_locations: list[str] | None = None
    excluded_locations: list[str] | None = None
    excluded_companies: list[str] | None = None
    excluded_title_words: list[str] | None = None
    secondary_locations: list[str] | None = None
    secondary_location_min_score: float | None = Field(None, ge=0, le=100)
    open_to_relocation: bool | None = None
    target_titles_primary: list[str] | None = None
    target_titles_secondary: list[str] | None = None
    experience_max_years: float | None = Field(None, ge=0, le=20)
    scoring_weights: dict[str, float] | None = None
    search_frequency_hours: int | None = Field(None, ge=1, le=168)
    graduation_date: date | None = None
    work_authorization: str | None = Field(None, max_length=100)
    needs_sponsorship: bool | None = None

    @field_validator("preferred_locations", "excluded_locations", "excluded_companies", "secondary_locations",
                     "target_titles_primary", "target_titles_secondary")
    @classmethod
    def _clean_list(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        items = [x.strip() for x in v if x.strip()]
        if any(len(x) > 200 for x in items):
            raise ValueError("entries must be 200 characters or fewer")
        return list(dict.fromkeys(items))

    @field_validator("excluded_title_words")
    @classmethod
    def _words(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else cls._clean_list([x.lower() for x in v])

    @field_validator("scoring_weights")
    @classmethod
    def _weights(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        if set(v) != set(CATEGORIES):
            raise ValueError(f"weights must have exactly: {', '.join(CATEGORIES)}")
        if any(w < 0 for w in v.values()) or sum(v.values()) <= 0:
            raise ValueError("weights must be non-negative and not all zero")
        return v

    @field_validator("work_authorization")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


# Fields where an explicit null means "clear it" (unknown facts about me, optional bounds).
_NULLABLE = {"salary_preferred_min", "salary_preferred_max", "secondary_location_min_score", "open_to_relocation",
             "graduation_date", "work_authorization", "needs_sponsorship"}


@router.get("/settings")
def get_user_settings(session: Session = Depends(get_session)) -> dict[str, Any]:
    return _settings_out(session.get(UserSettings, 1), session)


@router.put("/settings")
def update_settings(body: SettingsUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    s = session.get(UserSettings, 1)
    changed = False
    for f in body.model_fields_set:
        value = getattr(body, f)
        if value is None and f not in _NULLABLE:
            raise HTTPException(422, f"{f} can't be empty")
        current = getattr(s, f)
        if isinstance(current, Decimal) and value is not None:
            value = Decimal(str(value))
        if current != value:
            setattr(s, f, value)
            changed = True
    lo, hi = s.salary_preferred_min, s.salary_preferred_max
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(422, "preferred salary min must be at most the max")
    if changed:
        s.version += 1
    session.commit()
    return _settings_out(s, session)


# ---------- skip feedback ----------

@router.get("/skip-feedback")
def get_skip_feedback(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Why I've skipped jobs, and Settings changes those reasons suggest (applied only from the Dashboard)."""
    return skip_feedback(session)


# ---------- rescoring ----------

class _ScoreState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.result: dict[str, Any] | None = None
        self.error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"running": self.running, "started_at": self.started_at and self.started_at.isoformat(),
                "finished_at": self.finished_at and self.finished_at.isoformat(), "result": self.result,
                "error": self.error}


SCORE_STATE = _ScoreState()


def _rescore(factory: Callable[[], Session]) -> None:
    settings = get_settings()
    try:
        with factory() as session:
            summary = score_all(session, settings.config_dir, settings.private_dir / "profile.yaml")
        SCORE_STATE.result, SCORE_STATE.error = vars(summary), None
    except Exception as exc:  # reported to the UI; details in the log
        logger.exception("rescoring failed")
        SCORE_STATE.error = f"{type(exc).__name__}: {exc}"
    finally:
        SCORE_STATE.finished_at = datetime.now(UTC)
        SCORE_STATE.running = False


@router.post("/score", status_code=202)
def start_rescore(tasks: BackgroundTasks, factory: sessionmaker[Session] = Depends(get_session_factory)) -> dict[str, Any]:
    with SCORE_STATE.lock:
        if SCORE_STATE.running:
            raise HTTPException(409, "scoring is already running")
        SCORE_STATE.running, SCORE_STATE.started_at, SCORE_STATE.finished_at = True, datetime.now(UTC), None
    tasks.add_task(_rescore, factory)
    return SCORE_STATE.snapshot()


@router.get("/score")
def rescore_status() -> dict[str, Any]:
    return SCORE_STATE.snapshot()


# ---------- scheduled updates ----------

def _run_out(run: ScheduledRun) -> dict[str, Any]:
    return {"id": run.id, "trigger": run.trigger, "status": run.status, "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "sources_ok": run.sources_ok, "sources_failed": run.sources_failed, "new_jobs": run.new_jobs,
            "closed_jobs": run.closed_jobs, "new_recommended": len(run.new_recommended_ids or []), "error": run.error}


def _rows_for(session: Session, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = session.execute(
        select(JobScore, Job, Company, JobStatus).join(Job, Job.id == JobScore.job_id)
        .join(Company, Company.id == Job.company_id).outerjoin(JobStatus, JobStatus.job_id == Job.id)
        .where(JobScore.scorer == SCORER, Job.id.in_(ids))).all()
    order = {job_id: i for i, job_id in enumerate(ids)}
    return sorted((_job_row(*r) for r in rows), key=lambda j: order[j["id"]])


@router.get("/updates")
def updates(session: Session = Depends(get_session)) -> dict[str, Any]:
    """The latest scheduled update and its digest (new Apply/Maybe jobs, tracked jobs whose listing closed)."""
    history = list(session.scalars(select(ScheduledRun).order_by(ScheduledRun.started_at.desc()).limit(10)))
    latest_done = next((r for r in history if r.status == "succeeded"), None)
    return {
        "running": any(r.status == "running" for r in history),
        "next_due_at": next_due(session).isoformat(),
        "history": [_run_out(r) for r in history],
        "digest": None if latest_done is None else {
            "run": _run_out(latest_done),
            "new_jobs": _rows_for(session, (latest_done.new_recommended_ids or [])[:50]),
            "closed_tracked": _rows_for(session, latest_done.closed_tracked_ids or []),
        },
    }


def _update_in_background(factory: Callable[[], Session]) -> None:
    try:
        with factory() as session:
            run_update(session, "manual")
    except Exception:  # recorded on the run where possible; the log has the rest
        logger.exception("manual update failed")


@router.post("/updates", status_code=202)
def start_update(tasks: BackgroundTasks, session: Session = Depends(get_session),
                 factory: sessionmaker[Session] = Depends(get_session_factory)) -> dict[str, Any]:
    if session.scalar(select(ScheduledRun.id).where(ScheduledRun.status == "running").limit(1)):
        raise HTTPException(409, "an update is already running")
    tasks.add_task(_update_in_background, factory)
    return {"started": True}


# ---------- dashboard ----------

@router.get("/dashboard")
def dashboard(session: Session = Depends(get_session)) -> dict[str, Any]:
    scored = (select(JobScore).join(Job, Job.id == JobScore.job_id)
              .where(JobScore.scorer == SCORER, Job.is_active.is_(True)).subquery())
    by_rec = dict(session.execute(select(scored.c.recommendation, func.count()).group_by(scored.c.recommendation)).all())
    by_tier = dict(session.execute(select(scored.c.tier, func.count())
                                   .where(scored.c.recommendation.in_(("apply", "maybe"))).group_by(scored.c.tier)).all())
    by_status = dict(session.execute(select(JobStatus.status, func.count()).group_by(JobStatus.status)).all())
    week_ago = datetime.now(UTC) - timedelta(days=7)
    new_this_week = session.scalar(
        select(func.count()).select_from(JobScore).join(Job, Job.id == JobScore.job_id)
        .where(JobScore.scorer == SCORER, Job.is_active.is_(True), JobScore.recommendation == "apply",
               Job.first_seen_at >= week_ago))
    applied_this_week = session.scalar(select(func.count()).select_from(JobStatus).where(JobStatus.applied_at >= week_ago))
    recent = session.execute(
        select(JobStatus, Job, Company).join(Job, Job.id == JobStatus.job_id).join(Company, Company.id == Job.company_id)
        .order_by(JobStatus.updated_at.desc()).limit(10)).all()
    last_run = (select(IngestionRun.source_kind, func.max(IngestionRun.finished_at).label("finished_at"),
                       func.count().label("runs"))
                .where(IngestionRun.status == "succeeded").group_by(IngestionRun.source_kind))
    failures = session.scalar(select(func.count()).select_from(IngestionRun).where(
        IngestionRun.status == "failed", IngestionRun.started_at >= week_ago))
    return {
        "active_jobs": session.scalar(select(func.count()).select_from(Job).where(Job.is_active.is_(True))),
        "by_recommendation": {r: by_rec.get(r, 0) for r in RECOMMENDATIONS},
        "by_tier": {t: by_tier.get(t, 0) for t in TIERS},
        "by_status": {s: by_status.get(s, 0) for s in JOB_STATUSES if s != "new"},
        "new_apply_this_week": new_this_week,
        "applied_this_week": applied_this_week,
        "recent_activity": [{"job_id": j.id, "title": j.title, "company": c.name, "status": st.status,
                             "updated_at": st.updated_at.isoformat()} for st, j, c in recent],
        "sources": [{"kind": k, "last_success": f.isoformat() if f else None, "runs": n}
                    for k, f, n in session.execute(last_run).all()],
        "failed_runs_this_week": failures,
        "scored_at": (lambda t: t.isoformat() if t else None)(
            session.scalar(select(func.max(JobScore.computed_at)).where(JobScore.scorer == SCORER))),
    }


# ---------- CSV export ----------

def _cell(v: Any) -> str:
    """Text for a CSV cell. Values starting with = + - @ are prefixed so Excel/Sheets don't run them as formulas."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _pay_text(pay: dict[str, Any] | None) -> tuple[str, str]:
    if not pay:
        return "", ""
    unit = "/hr" if pay["period"] == "hour" else "/yr"
    rng = f"${pay['min']:,.0f}" + (f"–${pay['max']:,.0f}" if pay.get("max") and pay["max"] != pay["min"] else "") + unit
    return (rng, "") if pay["kind"] == "posted" else ("", f"{rng} ({pay['confidence']} confidence)")


@router.get("/export.csv")
def export_csv(filters: JobFilters = Depends(), session: Session = Depends(get_session)) -> Response:
    rows = session.execute(filters.stmt.order_by(*filters.order).limit(20_000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Score", "Recommendation", "Tier", "Title", "Company", "Location", "Same role, other locations",
                "Posted pay", "Pay estimate",
                "Flags", "Rotational", "Status", "Applied", "Posted", "Summary", "Apply link"])
    for r in rows:
        j = _job_row(*r)
        posted, est = _pay_text(j["pay"])
        flags = "; ".join(f"{f['label']} ({f['status'].replace('_', ' ')})" for f in j["flags"])
        w.writerow([_cell(x) for x in (
            f"{j['score']:g}", j["recommendation"], j["tier"].replace("_", " "), j["title"], j["company"], j["location"],
            j["other_locations"] or "", posted, est, flags, "yes" if j["rotational"] else "", j["status"], (j["applied_at"] or "")[:10],
            (j["posted_at"] or "")[:10], j["summary"], j["apply_url"])])
    name = f"jobs-{date.today().isoformat()}.csv"
    # BOM so Excel opens it as UTF-8 (en dashes, accents).
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- encouragement ----------

def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [str(line).strip() for line in data.get("lines") or [] if str(line).strip()]


@router.get("/encouragement")
def encouragement() -> list[str]:
    """Lines for the banner: config/encouragement.yaml plus my private ones in data/private/encouragement.yaml."""
    settings = get_settings()
    return list(dict.fromkeys(_lines(settings.config_dir / "encouragement.yaml")
                              + _lines(settings.private_dir / "encouragement.yaml")))


# ---------- apply hub ----------

@router.get("/hub")
def hub() -> list[dict[str, Any]]:
    return [vars(link) for link in load_hub(get_settings().config_dir / "apply_hub.yaml", get_settings().private_dir / "apply_hub.yaml")]
