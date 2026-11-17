"""Skip feedback: why I skipped jobs (the skip popup), and the Settings changes those reasons suggest.

Nothing here changes Settings by itself. Each suggestion carries the exact Settings change; I apply it from the
Dashboard (which then rescores) or ignore it.
"""

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalize import normalize_title
from app.models import Company, Job, JobStatus, StatusEvent, UserSettings
from app.models.enums import SKIP_REASONS
from app.scoring.extract import annual
from app.scoring.rules import is_off_target

REASON_LABEL = {
    "experience": "Asks for more experience than I have",
    "pay": "Pay is too low",
    "location": "Location or commute",
    "arrangement": "On-site / hybrid / remote setup",
    "role": "Not the type of role I want",
    "company": "Company or culture fit",
    "other": "Something else",
}
assert set(REASON_LABEL) == set(SKIP_REASONS)

# How many skips with the same reason before a suggestion shows up. A company or a city is a specific choice,
# so one or two is enough; broad changes (pay floor, experience, work setup) wait for a pattern.
MIN_SKIPS = {"experience": 3, "pay": 3, "arrangement": 3, "location": 2, "role": 2, "company": 1}
# Title words that say nothing about the type of role.
_GENERIC = {
    "and", "of", "the", "for", "to", "in", "with", "at", "on", "new", "grad", "graduate", "entry", "level", "early",
    "career", "program", "remote", "hybrid", "onsite", "full", "time", "team", "associate", "junior", "senior",
    "lead", "staff", "principal", "intern", "ii", "iii", "iv", "us", "usa", "nyc", "2025", "2026", "2027",
}


@dataclass
class Skip:
    job: Job
    company: str
    reasons: list[str]
    note: str | None


def latest_skips(session: Session) -> list[Skip]:
    """Jobs still marked Skipped, with the reasons from their most recent skip."""
    rows = session.execute(
        select(StatusEvent, Job, Company.name)
        .join(Job, Job.id == StatusEvent.job_id).join(Company, Company.id == Job.company_id)
        .join(JobStatus, JobStatus.job_id == Job.id)
        .where(StatusEvent.to_status == "skipped", JobStatus.status == "skipped")
        .order_by(StatusEvent.at, StatusEvent.id)
    ).all()
    latest: dict[int, Skip] = {}
    for event, job, company in rows:
        latest[job.id] = Skip(job, company, list(event.reasons or []), event.note)
    return list(latest.values())


def _place(loc: dict) -> str | None:
    city, state = (loc.get("city") or "").strip(), loc.get("state")
    return f"{city}, {state}" if city and state else None


def _top_pay(job: Job) -> float | None:
    if job.base_min is not None:
        return annual(job.base_max or job.base_min, job.pay_period)
    if job.est_base_min is not None:
        return annual(job.est_base_max or job.est_base_min, job.est_pay_period)
    return None


def _money(v: float) -> str:
    return f"${v / 1000:,.0f}k"


def suggestions(skips: list[Skip], s: UserSettings) -> list[dict[str, Any]]:
    by_reason: dict[str, list[Skip]] = defaultdict(list)
    for sk in skips:
        for r in sk.reasons:
            by_reason[r].append(sk)
    out: list[dict[str, Any]] = []

    def add(key: str, reason: str, text: str, evidence: list[str], change: dict[str, Any]) -> None:
        out.append({"id": key, "reason": reason, "text": text, "evidence": evidence[:5], "change": change})

    # Company or culture fit: hide that company.
    hidden = {c.lower() for c in s.excluded_companies or []}
    by_company: dict[str, list[Skip]] = defaultdict(list)
    for sk in by_reason["company"]:
        by_company[sk.company].append(sk)
    for company, group in sorted(by_company.items(), key=lambda kv: -len(kv[1])):
        if company.lower() not in hidden and len(group) >= MIN_SKIPS["company"]:
            add(f"company:{company.lower()}", "company", f"Hide all jobs from {company}",
                [sk.job.title for sk in group], {"excluded_companies": [*(s.excluded_companies or []), company]})

    # Not the type of role I want: hide a title word those jobs share (never one from my target titles).
    targets = {w for t in [*(s.target_titles_primary or []), *(s.target_titles_secondary or [])]
               for w in normalize_title(t).split()}
    already = {w.lower() for w in s.excluded_title_words or []}
    words: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for sk in by_reason["role"]:
        for w in set(normalize_title(sk.job.title).split()):
            if (len(w) >= 3 and not w.isdigit() and w not in _GENERIC and w not in targets and w not in already
                    and not is_off_target(w)):
                words[w] += 1
                examples[w].append(sk.job.title)
    for w, n in words.most_common(5):
        if n >= MIN_SKIPS["role"]:
            add(f"word:{w}", "role", f"Skip titles containing “{w}”", examples[w],
                {"excluded_title_words": [*(s.excluded_title_words or []), w]})

    # Location: exclude a city those jobs were in (unless it's one of my preferred places).
    preferred = {p.lower() for p in s.preferred_locations or []}
    excluded = {p.lower() for p in s.excluded_locations or []}
    places: Counter[str] = Counter()
    for sk in by_reason["location"]:
        for p in {pl for pl in map(_place, sk.job.locations or []) if pl}:
            places[p] += 1
    for p, n in places.most_common(5):
        state = p.rsplit(", ", 1)[1].lower()
        if n >= MIN_SKIPS["location"] and p.lower() not in excluded | preferred and state not in preferred:
            add(f"location:{p.lower()}", "location", f"Stop showing jobs in {p}", [f"skipped {n} jobs there"],
                {"excluded_locations": [*(s.excluded_locations or []), p]})

    # Work setup: drop an arrangement I keep skipping (keeping at least one).
    allowed = list(s.work_arrangements or [])
    setups = Counter(sk.job.work_arrangement for sk in by_reason["arrangement"])
    for setup, n in setups.most_common():
        if setup in allowed and len(allowed) > 1 and n >= MIN_SKIPS["arrangement"]:
            label = "on-site" if setup == "onsite" else setup
            add(f"arrangement:{setup}", "arrangement", f"Stop showing {label} jobs",
                [f"skipped {n} {label} jobs for their setup"],
                {"work_arrangements": [a for a in allowed if a != setup]})

    # Pay: raise the minimum to the typical top of the pay I skipped, rounded up to $5k.
    pays = [p for p in (_top_pay(sk.job) for sk in by_reason["pay"]) if p]
    if len(pays) >= MIN_SKIPS["pay"]:
        new_min = math.ceil(statistics.median(pays) / 5000) * 5000
        if new_min > float(s.salary_min):
            add(f"pay:{new_min}", "pay", f"Raise minimum base pay from {_money(float(s.salary_min))} to "
                f"{_money(new_min)}", [f"half of the {len(pays)} jobs you skipped for pay topped out at "
                                       f"{_money(statistics.median(pays))} or less"], {"salary_min": new_min})

    # Experience: lower the most experience a posting may ask for.
    asked = [sk for sk in by_reason["experience"]]
    current = float(s.experience_max_years)
    if len(asked) >= MIN_SKIPS["experience"] and current > 1:
        years = [float(sk.job.exp_min_years) for sk in asked if sk.job.exp_min_years is not None]
        evidence = [f"{len(asked)} jobs skipped for experience"] + (
            [f"they asked for {min(years):g}–{max(years):g} years"] if years else [])
        add(f"experience:{current - 1:g}", "experience",
            f"Only show jobs asking for up to {current - 1:g} years (now {current:g})", evidence,
            {"experience_max_years": current - 1})
    return out


def skip_feedback(session: Session) -> dict[str, Any]:
    skips = latest_skips(session)
    counts = Counter(r for sk in skips for r in sk.reasons)
    return {
        "skipped": len(skips),
        "with_reasons": sum(1 for sk in skips if sk.reasons),
        "reasons": [{"reason": r, "label": REASON_LABEL[r], "count": counts.get(r, 0)} for r in SKIP_REASONS],
        "notes": [{"job_id": sk.job.id, "title": sk.job.title, "company": sk.company, "note": sk.note}
                  for sk in reversed(skips) if sk.note][:10],
        "suggestions": suggestions(skips, session.get(UserSettings, 1)),
    }
