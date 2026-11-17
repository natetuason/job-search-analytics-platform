"""Rule-based scorer ("rules-v1").

Produces a 0-100 match score from five weighted categories, a qualitative tier, a
recommendation with reasons, and hard-requirement flags kept separate from the
score. The score measures fit with my stated preferences and resume; it is not a
probability of getting an interview or offer.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any

from app.ingestion.normalize import normalize_title
from app.scoring.extract import annual
from app.scoring.profile import Profile, SkillVocabulary

SCORER = "rules-v1"
CATEGORIES = ("skills", "experience", "responsibilities", "compensation", "location")
_ANALYTIC_TITLE = re.compile(
    r"analyst|analytics|insights|business intelligence|\bbi\b|data|operations|strategy|strategic|planning|planner|"
    r"supply chain|logistics|procurement|inventory|consult|product|project|program|risk|associate|rotational", re.I)
# Role types outside my targets even when the description uses my tools (Python, SQL, ...).
# Defaults; config/scoring.yaml (off_target_title_words, tool_skills) overrides them per user.
DEFAULT_OFF_TARGET_WORDS = (
    r"engineer", r"developer", r"designer", r"quant\b", r"quantitative", r"trader", r"researcher", r"scientist",
    r"technician", r"nurse", r"therapist", r"physician", r"pharmacist", r"recruiter", r"account executive",
    r"sales (?:representative|development)", r"attorney", r"counsel", r"paralegal", r"teacher", r"driver",
    r"machinist", r"mechanic", r"cashier", r"server", r"cook", r"housekeep", r"reporter", r"editor", r"writer",
    r"producer", r"journalist", r"photographer", r"operator", r"stylist", r"chef", r"architect", r"full[- ]?stack",
    r"front[- ]?end", r"back[- ]?end",
)
# A description naming several of these is "uses my skills", not just generic business words.
DEFAULT_TOOL_SKILLS = ("SQL", "Snowflake", "Python", "R", "Tableau", "QuickSight", "Power BI", "Excel", "SAP", "Jira", "Java")


@lru_cache(maxsize=8)
def off_target_pattern(words: tuple[str, ...] = DEFAULT_OFF_TARGET_WORDS) -> re.Pattern[str]:
    """Role types that are Skip even when the description uses my tools. An empty list disables the rule."""
    return re.compile("|".join(words) if words else r"(?!x)x", re.I)


_OFF_TARGET = off_target_pattern()
_INELIGIBLE_TYPES = {"internship": "an internship", "part_time": "part-time", "contract": "a contract role"}


@dataclass
class JobFacts:
    """Everything the scorer needs about one job, already extracted."""
    title: str
    description: str | None
    employment_type: str
    location_class: str  # see scoring.location
    location_label: str | None
    base_min: Decimal | None = None
    base_max: Decimal | None = None
    pay_period: str | None = None
    est_base_min: Decimal | None = None
    est_base_max: Decimal | None = None
    est_pay_period: str | None = None
    est_confidence: str | None = None
    exp_min_years: Decimal | None = None
    exp_text: str | None = None
    seniority: str | None = None
    early_career: bool = False
    rotational: bool = False
    flags: list[dict[str, str]] = field(default_factory=list)
    work_arrangement: str = "unknown"
    company: str = ""
    # Every location is one I've excluded in Settings.
    excluded_location: bool = False
    # Days since the posting went up (None when the source gives no date).
    posted_days: float | None = None


@dataclass
class Preferences:
    weights: dict[str, float]
    salary_min: float
    target_titles_primary: list[str]
    target_titles_secondary: list[str]
    experience_max_years: float
    secondary_location_min_score: float
    needs_sponsorship: bool | None
    work_authorization: str | None
    tuning: dict[str, Any]
    # Only a posting that states an arrangement I've excluded is skipped; "unknown" always passes.
    work_arrangements: tuple[str, ...] = ("remote", "hybrid", "onsite")
    # Lowercased company names I've hidden (usually from skip feedback).
    excluded_companies: frozenset[str] = frozenset()


@dataclass
class ScoreResult:
    score: float
    breakdown: dict[str, Any]
    tier: str
    recommendation: str
    explanation: dict[str, Any]
    hard_flags: list[dict[str, str]]


@lru_cache(maxsize=4096)
def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(normalize_title(title).split())


# Targets ending in these words must appear as an exact phrase: "Product Manager" doesn't match
# "Product Marketing Manager". Other targets match on words in any order ("Analyst, Supply Chain").
_PHRASE_TARGET = re.compile(r"\b(?:manager|coordinator)$")


@lru_cache(maxsize=4096)
def _target_in(target: str, title: str) -> bool:
    if _PHRASE_TARGET.search(normalize_title(target)):
        return f" {normalize_title(target)} " in f" {normalize_title(title)} "
    return _title_tokens(target) <= _title_tokens(title)


def title_match(title: str, primary: list[str], secondary: list[str],
                off_target: re.Pattern[str] = _OFF_TARGET) -> tuple[str, str | None]:
    """('primary'|'secondary'|'off_target'|'analytic'|'none', matched target title)."""
    off = bool(off_target.search(title))
    for group, targets in (("primary", primary), ("secondary", secondary)):
        for target in targets:
            # "Quantitative Analyst" isn't an "Analyst" target; "Supply Chain Data Scientist" is its own target.
            if _target_in(target, title) and (not off or off_target.search(target)):
                return group, target
    if off:
        return "off_target", None
    return ("analytic", None) if _ANALYTIC_TITLE.search(title) else ("none", None)


def is_off_target(title: str, off_target: re.Pattern[str] = _OFF_TARGET) -> bool:
    return bool(off_target.search(title))


def tuned_off_target(tuning: dict[str, Any]) -> re.Pattern[str]:
    words = tuning.get("off_target_title_words")
    return off_target_pattern(tuple(str(w) for w in words)) if words is not None else _OFF_TARGET


def _skills(job: JobFacts, profile: Profile, vocab: SkillVocabulary) -> tuple[float, dict[str, Any]]:
    found = vocab.find(f"{job.title}\n{job.description or ''}")
    if not found:
        return 50.0, {"matched": [], "partial": [], "missing": [], "note": "description too short to judge skills"}
    matched = [s for s in found if profile.weight(s) == 1.0]
    partial = [s for s in found if 0 < profile.weight(s) < 1.0]
    missing = [s for s in found if profile.weight(s) == 0]
    coverage = sum(profile.weight(s) for s in found) / len(found)
    # A long list of mentioned tools shouldn't sink a job that hits the core ones:
    # the first five matched skills count double.
    depth = min(len(matched) + 0.6 * len(partial), 5) / 5
    return round(100 * (0.6 * coverage + 0.4 * depth), 1), {"matched": matched, "partial": partial, "missing": missing}


def _experience(job: JobFacts, prefs: Preferences) -> tuple[float, str, str | None]:
    """(points, plain-English fit, blocking reason or None)."""
    if job.seniority == "senior":
        return 0.0, "Senior-level title", "senior-level title"
    if job.seniority == "level_iii":
        return 0.0, "Level III+ title (typically 4+ years)", "level III+ title"
    senior_pay = float(prefs.tuning.get("senior_pay_floor", 0) or 0)
    if senior_pay and job.base_min is not None and annual(job.base_min, job.pay_period) >= senior_pay:
        return 0.0, f"Posted pay starts at ${annual(job.base_min, job.pay_period):,.0f}; that's a senior-level band", \
            "pay band indicates a senior role"
    years = float(job.exp_min_years) if job.exp_min_years is not None else None
    if years is not None and years > prefs.experience_max_years:
        return 0.0, f"Asks for {years:g}+ years of experience", f"asks for {years:g}+ years"
    if job.early_career:
        points, fit = 100.0, "Explicitly early-career / new-grad"
    elif years is None:
        points, fit = 65.0, "Experience level not stated"
    elif years <= 1:
        points, fit = 95.0, f"Asks for {years:g} year(s); fits a new grad"
    elif years <= 2:
        points, fit = 80.0, f"Asks for {years:g} years; internships may count"
    else:
        points, fit = 55.0, f"Asks for {years:g} years; a stretch but allowed"
    if job.seniority == "level_ii":
        points -= prefs.tuning.get("level_ii_penalty", 15)
        fit += "; level II title"
    return max(points, 0.0), fit, None


def _responsibilities(job: JobFacts, prefs: Preferences, skills_points: float, tools: int) -> tuple[float, str]:
    group, target = title_match(job.title, prefs.target_titles_primary, prefs.target_titles_secondary,
                                tuned_off_target(prefs.tuning))
    if group == "primary":
        return 100.0, f"Title matches a primary target ({target})"
    if group == "secondary":
        return 80.0, f"Title matches a secondary target ({target})"
    if group == "off_target":
        return 0.0, "Engineering/design/trading/other non-target role type"
    if group == "analytic" and skills_points >= 60 and tools >= 1:
        return 65.0, "Analytics-type title, and the description uses many of my skills"
    if group == "analytic":
        return 50.0, "Analytics/operations-type title"
    if skills_points >= 60 and tools >= 2:  # title differs, but the work uses my tools
        return 50.0, "Title isn't a target, but the description names several of my tools"
    return 15.0, "Title is outside my target roles"


def _compensation(job: JobFacts, prefs: Preferences) -> tuple[float, str, str | None]:
    pay = prefs.tuning.get("pay", {})
    full_at, floor_points = float(pay.get("full_points_at", 110000)), float(pay.get("floor_points", 60))

    def points(lo: float, hi: float) -> float:
        mid = (lo + hi) / 2
        if hi < prefs.salary_min:
            return 0.0
        if mid >= full_at:
            return 100.0
        if mid <= prefs.salary_min:
            return floor_points
        return floor_points + (100 - floor_points) * (mid - prefs.salary_min) / (full_at - prefs.salary_min)

    if job.base_min is not None:
        lo = annual(job.base_min, job.pay_period)
        hi = annual(job.base_max if job.base_max is not None else job.base_min, job.pay_period)
        label = f"Posted base ${lo:,.0f}–${hi:,.0f}/yr" + (" (hourly, annualized)" if job.pay_period == "hour" else "")
        if hi < prefs.salary_min:
            return 0.0, label + f"; below your ${prefs.salary_min:,.0f} minimum", f"pay tops out below ${prefs.salary_min:,.0f}"
        return round(points(lo, hi), 1), label, None
    if job.est_base_min is not None:
        lo = annual(job.est_base_min, job.est_pay_period)
        hi = annual(job.est_base_max if job.est_base_max is not None else job.est_base_min, job.est_pay_period)
        trust = {"medium": 0.6, "low": 0.4}.get(job.est_confidence or "", 0.3)
        unknown = float(pay.get("unknown_points", 50))
        pts = unknown + trust * (points(lo, hi) - unknown)
        return round(pts, 1), f"ESTIMATE ${lo:,.0f}–${hi:,.0f}/yr ({job.est_confidence} confidence; not posted)", None
    return float(pay.get("unknown_points", 50)), "Pay not posted; no estimate", None


def _age_text(days: float) -> str:
    if days < 60:
        return f"{days:.0f} days ago"
    if days < 365:
        return f"{days / 30.4:.0f} months ago"
    years = days / 365
    return "over a year ago" if years < 2 else f"over {int(years)} years ago"


def _age_penalty(days: float | None, tuning: dict[str, Any]) -> tuple[float, str | None]:
    """(points off, note). Older postings rank lower; a job with no posted date isn't penalized."""
    cfg = tuning.get("posting_age") or {}
    max_pen = float(cfg.get("max_penalty", 0) or 0)
    if days is None or max_pen <= 0:
        return 0.0, None
    grace, full = float(cfg.get("grace_days", 30)), float(cfg.get("full_penalty_days", 180))
    if days <= grace:
        return 0.0, None
    share = 1.0 if days >= full or full <= grace else (days - grace) / (full - grace)
    penalty = round(max_pen * share, 1)
    return penalty, f"Posted {_age_text(days)}; ranked {penalty:g} points lower"


def score_job(job: JobFacts, profile: Profile, vocab: SkillVocabulary, prefs: Preferences) -> ScoreResult:
    tuning = prefs.tuning
    blockers: list[str] = []
    if job.employment_type in _INELIGIBLE_TYPES:
        blockers.append(f"it's {_INELIGIBLE_TYPES[job.employment_type]}")

    skills_pts, skills_detail = _skills(job, profile, vocab)
    exp_pts, exp_fit, exp_block = _experience(job, prefs)
    tool_skills = set(prefs.tuning.get("tool_skills") or DEFAULT_TOOL_SKILLS)
    tools = len(tool_skills & set(skills_detail["matched"] + skills_detail["partial"]))
    resp_pts, resp_note = _responsibilities(job, prefs, skills_pts, tools)
    comp_pts, comp_note, comp_block = _compensation(job, prefs)
    loc_pts = float((tuning.get("location_points") or {}).get(job.location_class, 0))
    for block in (exp_block, comp_block):
        if block:
            blockers.append(block)
    if resp_pts == 0:
        blockers.append("not one of your target role types")

    parts = {"skills": skills_pts, "experience": exp_pts, "responsibilities": resp_pts,
             "compensation": comp_pts, "location": loc_pts}
    total_weight = sum(prefs.weights.get(c, 0) for c in CATEGORIES) or 1
    score = sum(parts[c] * prefs.weights.get(c, 0) for c in CATEGORIES) / total_weight
    bonus = float(tuning.get("rotational_bonus", 0)) if job.rotational else 0.0
    age_penalty, age_note = _age_penalty(job.posted_days, tuning)
    score = round(max(min(score + bonus - age_penalty, 100.0), 0.0), 1)

    if job.location_class == "foreign":
        blockers.append("outside the U.S.")
    elif job.location_class == "other":
        blockers.append(f"location ({job.location_label or 'unknown'}) isn't NYC/NJ, remote, or one of your big cities")
    elif job.location_class == "secondary" and score < prefs.secondary_location_min_score:
        blockers.append(f"outside NYC/NJ and the match score is under {prefs.secondary_location_min_score:g}")
    if job.work_arrangement in ("remote", "hybrid", "onsite") and job.work_arrangement not in prefs.work_arrangements:
        blockers.append(f"it's {job.work_arrangement}, which isn't in your work arrangements")
    if job.excluded_location:
        blockers.append(f"{job.location_label or 'its location'} is in your excluded locations")
    if job.company.lower() in prefs.excluded_companies:
        blockers.append(f"you've hidden jobs from {job.company}")

    tiers = tuning.get("tiers", {})
    strong, competitive = tiers.get("strong_fit", {}), tiers.get("competitive", {})
    if (score >= strong.get("min_score", 70) and exp_pts >= strong.get("min_experience", 80)
            and skills_pts >= strong.get("min_skills", 55)):
        tier = "strong_fit"
    elif score >= competitive.get("min_score", 55):
        tier = "competitive"
    else:
        tier = "reach"

    flags = [dict(f, status=_flag_status(f["type"], prefs)) for f in job.flags]
    rec_cfg = tuning.get("recommendation", {})
    reasons: list[str] = []
    if blockers:
        recommendation = "skip"
        reasons = [f"Skip: {b}" for b in blockers]
    elif score < rec_cfg.get("skip_below_score", 40):
        recommendation, reasons = "skip", [f"Skip: match score {score:g} is low"]
    elif resp_pts < rec_cfg.get("maybe_min_responsibilities", 0) and not job.rotational:
        recommendation = "skip"
        reasons = ["Skip: not one of your target role types, and the posting doesn't show enough of your tools"]
    elif (score >= rec_cfg.get("apply_min_score", 60) and tier != "reach"
          and resp_pts >= rec_cfg.get("apply_min_responsibilities", 50)):
        recommendation, reasons = "apply", [f"Apply: {tier.replace('_', ' ')} at match score {score:g}"]
    else:
        recommendation, reasons = "maybe", [f"Maybe: match score {score:g} ({tier})"]
    if recommendation == "apply" and any(f["status"] == "blocks" for f in flags):
        recommendation = "maybe"
        reasons.append("Downgraded: a hard requirement conflicts with your Settings")
    if recommendation == "apply" and any(f["type"] in ("clearance", "certification", "advanced_degree") for f in flags):
        recommendation = "maybe"
        reasons.append("Downgraded: check the hard requirement flag first")
    for f in flags:
        if f["status"] == "needs_your_check":
            reasons.append(f"Needs your check: {f['label'].lower()}")

    best = sorted(((sum(1 for s in e.skills if s in skills_detail["matched"] + skills_detail["partial"]), e)
                   for e in profile.experiences), key=lambda x: -x[0])
    best_experiences = [{"name": e.name, "skills": [s for s in e.skills if s in skills_detail["matched"] + skills_detail["partial"]]}
                        for n, e in best[:2] if n > 0]

    explanation = {
        "summary": _summary(score, tier, recommendation, resp_note, exp_fit, job),
        "skills": skills_detail,
        "best_experiences": best_experiences,
        "experience_fit": exp_fit,
        "responsibilities": resp_note,
        "compensation": comp_note,
        "location": f"{job.location_label or 'Location unknown'} ({job.location_class})",
        "rotational": job.rotational,
        "posting_age": age_note,
        "reasons": reasons,
    }
    breakdown = {"categories": parts, "weights": {c: prefs.weights.get(c, 0) for c in CATEGORIES},
                 "rotational_bonus": bonus, "age_penalty": age_penalty, "location_class": job.location_class}
    return ScoreResult(score, breakdown, tier, recommendation, explanation, flags)


_CITIZEN = re.compile(r"^\s*(?:u\.?s\.?|united states)\s+citizen", re.I)


def _flag_status(kind: str, prefs: Preferences) -> str:
    if kind == "no_sponsorship":
        if prefs.needs_sponsorship is None:
            return "needs_your_check"
        return "blocks" if prefs.needs_sponsorship else "ok"
    if kind == "citizenship":
        if not prefs.work_authorization:
            return "needs_your_check"
        return "ok" if _CITIZEN.search(prefs.work_authorization) else "blocks"
    return "check"


def _summary(score: float, tier: str, rec: str, resp: str, exp_fit: str, job: JobFacts) -> str:
    bits = [f"{tier.replace('_', ' ').capitalize()} match ({score:g}/100).", resp + ".", exp_fit + "."]
    if job.rotational:
        bits.append("Rotational/development program.")
    return " ".join(bits)
