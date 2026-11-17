"""Enrich active jobs from their text, estimate missing pay, score, and store results."""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Company, Job, JobScore, UserSettings
from app.scoring import extract
from app.scoring.estimate import Sample, build_index, estimate, family
from app.scoring.location import all_excluded, classify
from app.scoring.profile import load_profile, load_vocabulary
from app.scoring.rules import DEFAULT_OFF_TARGET_WORDS, SCORER, JobFacts, Preferences, is_off_target, score_job, tuned_off_target

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    scored: int
    pay_parsed: int
    estimated: int
    by_recommendation: dict[str, int]


def load_preferences(session: Session, tuning_path: Path) -> tuple[Preferences, UserSettings]:
    s = session.get(UserSettings, 1)
    tuning = yaml.safe_load(tuning_path.read_text()) or {}
    if s.excluded_title_words:
        # Words I've hidden in Settings work like config/scoring.yaml's off-target words.
        words = tuning.get("off_target_title_words")
        tuning["off_target_title_words"] = [*(DEFAULT_OFF_TARGET_WORDS if words is None else words),
                                            *(rf"\b{re.escape(w)}\b" for w in s.excluded_title_words)]
    prefs = Preferences(
        weights={k: float(v) for k, v in (s.scoring_weights or {}).items()},
        salary_min=float(s.salary_min),
        target_titles_primary=list(s.target_titles_primary or []),
        target_titles_secondary=list(s.target_titles_secondary or []),
        experience_max_years=float(s.experience_max_years),
        secondary_location_min_score=float(s.secondary_location_min_score or 0),
        needs_sponsorship=s.needs_sponsorship,
        work_authorization=s.work_authorization,
        tuning=tuning,
        work_arrangements=tuple(s.work_arrangements or ("remote", "hybrid", "onsite")),
        excluded_companies=frozenset(c.lower() for c in s.excluded_companies or []),
    )
    return prefs, s


def enrich(job: Job) -> bool:
    """Fill fields derived from the posting text. Returns True if pay was parsed from the description."""
    text = job.description_text
    parsed_pay = False
    if job.base_min is None or job.comp_parse_source == "description_text":
        pay = extract.parse_pay(text)
        if pay:
            job.base_min, job.base_max, job.pay_period = pay.base_min, pay.base_max, pay.pay_period
            job.currency, job.comp_parse_source = "USD", "description_text"
            parsed_pay = True
        elif job.comp_parse_source == "description_text":
            job.base_min = job.base_max = job.pay_period = job.currency = job.comp_parse_source = None
    exp = extract.parse_experience(text)
    job.exp_min_years = exp.min_years if exp else None
    job.exp_max_years = exp.max_years if exp else None
    job.exp_text = exp.snippet if exp else None
    job.seniority_signal = extract.seniority(job.title)
    job.is_early_career_program = extract.early_career(job.title, text)
    return parsed_pay


def _location_label(job: Job) -> str | None:
    return job.location_raw[:120] if job.location_raw else None


def score_all(session: Session, config_dir: Path, profile_path: Path) -> RunSummary:
    vocab = load_vocabulary(config_dir / "skills.yaml")
    profile = load_profile(profile_path, vocab)
    prefs, settings = load_preferences(session, config_dir / "scoring.yaml")
    est_cfg = prefs.tuning.get("estimate", {})
    off_target = tuned_off_target(prefs.tuning)

    jobs = list(session.scalars(select(Job).where(Job.is_active.is_(True))))
    pay_parsed = sum(enrich(job) for job in jobs)
    loc_class = {j.id: classify(j.locations or [], settings.preferred_locations or [],
                                settings.secondary_locations or []) for j in jobs}
    company_names = dict(session.execute(select(Company.id, Company.name)).all())

    # Estimate from postings like the ones I'd apply to: not senior, not an off-target role type,
    # and early-career or asking for at most the experience I allow.
    samples = [
        Sample(j.id, family(j.title), loc_class[j.id] == "preferred",
               (extract.annual(j.base_min, j.pay_period) + extract.annual(j.base_max or j.base_min, j.pay_period)) / 2)
        for j in jobs
        if j.base_min is not None and j.seniority_signal is None and loc_class[j.id] != "foreign"
        and not is_off_target(j.title, off_target) and family(j.title) != "other"
        and (j.is_early_career_program or (j.exp_min_years is not None and j.exp_min_years <= prefs.experience_max_years))
    ]
    index = build_index(samples)

    estimated = 0
    now = datetime.now(UTC)
    rows = []
    counts: dict[str, int] = {}
    for job in jobs:
        if job.base_min is None and family(job.title) != "other" and not is_off_target(job.title, off_target):
            est = estimate(job.title, loc_class[job.id] == "preferred", index,
                           est_cfg.get("min_samples", 10), est_cfg.get("medium_confidence_at", 30))
            if est:
                job.est_base_min, job.est_base_max, job.est_pay_period = est.low, est.high, "year"
                job.est_confidence, job.est_evidence = est.confidence, est.evidence
                estimated += 1
            else:
                job.est_base_min = job.est_base_max = job.est_pay_period = job.est_confidence = None
                job.est_evidence = None
        else:
            job.est_base_min = job.est_base_max = job.est_pay_period = job.est_confidence = None
            job.est_evidence = None

        facts = JobFacts(
            title=job.title, description=job.description_text, employment_type=job.employment_type,
            location_class=loc_class[job.id], location_label=_location_label(job),
            base_min=job.base_min, base_max=job.base_max, pay_period=job.pay_period,
            est_base_min=job.est_base_min, est_base_max=job.est_base_max, est_pay_period=job.est_pay_period,
            est_confidence=job.est_confidence, exp_min_years=job.exp_min_years, exp_text=job.exp_text,
            seniority=job.seniority_signal, early_career=job.is_early_career_program,
            rotational=extract.rotational(job.title, job.description_text),
            flags=extract.hard_flags(job.title, job.description_text), work_arrangement=job.work_arrangement,
            company=company_names.get(job.company_id, ""),
            excluded_location=all_excluded(job.locations or [], settings.excluded_locations or []),
            posted_days=(now - job.posted_at).total_seconds() / 86400 if job.posted_at else None,
        )
        result = score_job(facts, profile, vocab, prefs)
        counts[result.recommendation] = counts.get(result.recommendation, 0) + 1
        rows.append({
            "job_id": job.id, "scorer": SCORER, "settings_version": settings.version, "score": result.score,
            "breakdown": result.breakdown, "tier": result.tier, "recommendation": result.recommendation,
            "explanation": result.explanation, "hard_flags": result.hard_flags, "computed_at": now,
        })

    session.flush()
    for start in range(0, len(rows), 1000):
        stmt = insert(JobScore).values(rows[start:start + 1000])
        session.execute(stmt.on_conflict_do_update(
            constraint="uq_job_scores_job_id_scorer",
            set_={c: stmt.excluded[c] for c in ("settings_version", "score", "breakdown", "tier", "recommendation",
                                                "explanation", "hard_flags", "computed_at")},
        ))
    # Scores for jobs that are no longer active are left in place; the UI filters on is_active.
    session.commit()
    summary = RunSummary(len(rows), pay_parsed, estimated, counts)
    logger.info("scoring finished", extra={"fields": vars(summary)})
    return summary
