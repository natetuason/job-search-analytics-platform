"""Application backlog: one structured entry per job I've applied to, plus a rollup across all of them.

Writes backlog/applications.md, backlog/applications.csv and backlog/rollup.md (git-ignored: they hold my fit and
gaps). Regenerated from the database whenever a job is marked Applied, and by `python -m app.cli backlog`.

Everything is read from the stored posting text: requirement lines are quoted exactly, and anything the posting
doesn't say is "not stated". Patterns live in config/backlog.yaml; my facts in data/private/backlog_me.yaml.
"""

import csv
import html
import io
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, Job, JobStatus
from app.scoring.extract import annual
from app.scoring.profile import load_profile, load_vocabulary

APPLIED = ("applied", "interviewing", "offer", "rejected")
NS = "not stated"
MAX_QUOTE = 220

_BULLET = re.compile(r"^\s*(?:[-•*·▪●◦]|\d+[.)])\s+")
_SENTENCE = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<!etc\.)(?<!vs\.)(?<=[.!?;])\s+(?=[A-Z(])")
_REQ_HDR = re.compile(r"qualif|requirement|looking for|you (?:have|bring|need)|you.ll (?:need|bring|have)|about you|"
                      r"who you are|must|skills|stats|ideal candidate|you might be|what we.re looking|basic", re.I)
_PREF_HDR = re.compile(r"prefer|nice|bonus|plus|desired|stand out|extra credit|even better", re.I)
_PREF = re.compile(r"\b(?:preferred|a plus|nice[- ]to[- ]have|bonus|an advantage|advantageous|desired|desirable|ideally|helpful|"
                   r"preferably|is a plus)\b", re.I)
_MUST = re.compile(r"\b(?:required|requires?|must|minimum|at least|proficien\w*|\d+\+? years?)\b", re.I)
_DEGREE = re.compile(r"\b(?:bachelor'?s?|b\.[sa]\.|undergraduate degree|master'?s|mba|ph\.?d|degree)\b", re.I)
_ADVANCED = re.compile(r"\b(?:master'?s|mba|ph\.?d)\b", re.I)
_GRAD = re.compile(r"(?:graduat\w*|class of|expected|completion)[^.]{0,80}\b20\d\d\b|\b20\d\d\b[^.]{0,40}(?:graduat\w*|start)",
                   re.I)
_MOVE = re.compile(r"\brelocat\w*|\btravel(?:ing|ling)?\b(?! reimburse)", re.I)
_AUTH = re.compile(r"sponsor\w*|authori[sz]ed to work|work authori[sz]ation|u\.?s\.? citizen\w*|\bvisa\b|clearance", re.I)
_EEO = re.compile(r"citizenship status|equal (?:employment )?opportunit|regardless of|protected (?:by|veteran)", re.I)
_CAP = re.compile(r"\bup to\b|\bmaximum\b|\bat most\b|\bno more than\b|\bor less\b|\bless than\b", re.I)
_NYC = re.compile(r"\bnew york\b|\bnyc\b|manhattan|brooklyn", re.I)
_COMP = re.compile(r"\b(?:bonus\w*|equity|rsus?|stock(?! exchange)|long[- ]term incentive|commission\w*)\b", re.I)
_MAJORS = {
    "computer science": r"computer science", "statistics": r"statistic", "mathematics": r"\bmath", "economics": r"econom",
    "finance": r"\bfinanc", "engineering": r"engineering", "business": r"business", "data science": r"data science",
    "supply chain": r"supply chain", "quantitative/technical field": r"quantitative|numerate|technical (?:field|discipline)|stem",
    "accounting": r"accounting", "information systems": r"information systems",
}


@dataclass
class Line:
    text: str
    kind: str  # must | preferred | other
    label: bool = False  # a short "Label:" bullet with its content on the next line


@dataclass
class Entry:
    job_id: int
    company: str
    title: str
    location: str
    arrangement: str
    applied: str
    status: str
    url: str
    family: str
    pay: str
    pay_band: tuple[float, float] | None
    bonus_equity: list[str]
    years: str
    years_min: float | None
    degree: list[str]
    advanced_degree_required: bool
    majors: list[str]
    grad_year: list[str]
    relocation_travel: list[str]
    work_auth: list[str]
    must: dict[str, str] = field(default_factory=dict)
    preferred: dict[str, str] = field(default_factory=dict)
    soft: dict[str, str] = field(default_factory=dict)
    exp_types: dict[str, str] = field(default_factory=dict)
    app_reqs: dict[str, str] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)
    certs: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    gaps_must: list[str] = field(default_factory=list)
    gaps_pref: list[str] = field(default_factory=list)
    fit: int = 3
    fit_reason: str = ""
    has_text: bool = True


def _compile(patterns: dict[str, list[str]]) -> dict[str, re.Pattern[str]]:
    return {k: re.compile("|".join(rf"\b(?:{p})" for p in v), re.I) for k, v in patterns.items()}


class Reader:
    def __init__(self, config_dir: Path, private_dir: Path):
        cfg = yaml.safe_load((config_dir / "backlog.yaml").read_text()) or {}
        self.vocab = load_vocabulary(config_dir / "skills.yaml")
        self.families = _compile(cfg.get("role_families") or {})
        self.tools = set(cfg.get("tools") or [])
        self.soft = _compile(cfg.get("soft_skills") or {})
        self.exp_types = _compile(cfg.get("experience_types") or {})
        self.app_reqs = _compile(cfg.get("application_requirements") or {})
        self.limits = re.compile("|".join(cfg.get("application_limits") or [r"(?!x)x"]), re.I)
        self.certs = re.compile(r"\b(?:" + "|".join(cfg.get("certifications") or [r"(?!x)x"]) + r")\b", re.I)
        self.gap_fixes: dict[str, str] = cfg.get("gap_fixes") or {}
        me_path = private_dir / "backlog_me.yaml"
        self.me: dict[str, Any] = (yaml.safe_load(me_path.read_text()) or {}) if me_path.exists() else {}
        profile_path = private_dir / "profile.yaml"
        profile = load_profile(profile_path, self.vocab) if profile_path.exists() else None
        self.my_skills = set(profile.skills if profile else []) | set(self.me.get("extra_skills") or [])
        self.gap_fixes |= self.me.get("gap_fixes") or {}

    def lines(self, text: str) -> list[Line]:
        """Sentences of the posting, each tagged must / preferred / other by its section header and wording."""
        out: list[Line] = []
        section = "other"
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            bullet = bool(_BULLET.match(line))
            line = _BULLET.sub("", line).strip()
            if not bullet and len(line) <= 70 and not line.endswith((".", ":", ";")) and len(line.split()) <= 9 \
                    or (not bullet and line.endswith(":") and len(line) <= 90):
                section = "preferred" if _PREF_HDR.search(line) else "must" if _REQ_HDR.search(line) else "other"
                continue
            if line.endswith(":") and len(line) <= 60:
                out.append(Line(line, section, label=True))
                continue
            for sentence in _SENTENCE.split(line):
                kind = ("preferred" if section == "preferred" or _PREF.search(sentence)
                        else "must" if section == "must" or _MUST.search(sentence) else "other")
                out.append(Line(sentence.strip(), kind))
        return out

    def family(self, title: str) -> str:
        return next((name for name, p in self.families.items() if p.search(title)), "other")

    def read(self, job: Job, company: str, status: JobStatus) -> Entry:
        text = html.unescape(job.description_text or "")
        everything = self.lines(text)
        lines = [ln for ln in everything if not ln.label]
        reqs = [ln for ln in lines if ln.kind != "other"]

        def quotes(pattern: re.Pattern[str], pool: list[Line] = lines, n: int = 2, skip_eeo: bool = True) -> list[str]:
            found = [q(ln.text) for ln in pool if pattern.search(ln.text) and not (skip_eeo and _EEO.search(ln.text))]
            return list(dict.fromkeys(found))[:n]

        e = Entry(
            job_id=job.id, company=company, title=job.title, location=job.location_raw or NS,
            arrangement=NS if job.work_arrangement == "unknown" else job.work_arrangement.replace("onsite", "on-site"),
            applied=status.applied_at.date().isoformat() if status.applied_at else NS, status=status.status,
            url=job.apply_url, family=self.family(job.title), pay=NS, pay_band=None,
            bonus_equity=quotes(_COMP), years=NS, years_min=None,
            degree=quotes(_DEGREE, reqs or lines), advanced_degree_required=False, majors=[],
            grad_year=quotes(_GRAD), relocation_travel=quotes(_MOVE), work_auth=quotes(_AUTH),
            limits=quotes(self.limits, n=3), has_text=len(text) > 200,
        )
        e.advanced_degree_required = any(_ADVANCED.search(ln.text) and ln.kind == "must" and _DEGREE.search(ln.text)
                                         for ln in lines)
        degree_text = " ".join(ln.text for ln in lines if _DEGREE.search(ln.text))
        e.majors = [m for m, p in _MAJORS.items() if re.search(p, degree_text, re.I)]
        if job.base_min is not None:
            lo, hi = annual(job.base_min, job.pay_period), annual(job.base_max or job.base_min, job.pay_period)
            unit = "/hr" if job.pay_period == "hour" else "/yr"
            e.pay = f"${job.base_min:,.0f}" + (f"–${job.base_max:,.0f}" if job.base_max and job.base_max != job.base_min
                                               else "") + unit
            e.pay_band = (lo, hi)
        quote = f" (“{q(html.unescape(job.exp_text))}”)" if job.exp_text else ""
        if job.exp_min_years is not None and job.exp_text and _CAP.search(job.exp_text):
            # "up to 2 years maximum" is a ceiling, not a requirement
            e.years_min = 0.0
            e.years = f"up to {float(job.exp_max_years or job.exp_min_years):g} years (a ceiling){quote}"
        elif job.exp_min_years is not None:
            e.years_min = float(job.exp_min_years)
            e.years = f"{e.years_min:g}+ years{quote}"
        elif job.is_early_career_program:
            e.years = "not stated (posting is early-career / new grad)"

        for ln in reqs:
            # A degree line names majors ("Economics, Statistics"), not skills.
            for skill in ([] if _DEGREE.search(ln.text) else self.vocab.find(ln.text)):
                if ln.kind == "must" and skill not in e.must:
                    e.must[skill] = q(ln.text)
                    e.preferred.pop(skill, None)  # must-have wins over preferred
                elif ln.kind == "preferred" and skill not in e.must:
                    e.preferred.setdefault(skill, q(ln.text))
            for name, p in self.exp_types.items():
                if p.search(ln.text):
                    e.exp_types.setdefault(name, q(ln.text))
        # Soft skills the posting screens for: requirement lines only (not the pitch or benefits).
        for ln in reqs or lines:
            for name, p in self.soft.items():
                if p.search(ln.text) and not _EEO.search(ln.text):
                    e.soft.setdefault(name, q(ln.text))
        for ln in everything:
            for name, p in self.app_reqs.items():
                if p.search(ln.text):
                    e.app_reqs.setdefault(name, q(ln.text))
        e.certs = sorted({m.group(0).upper() for m in self.certs.finditer(text)})
        self.score(e)
        return e

    def score(self, e: Entry) -> None:
        """1-5 fit from must-have skill coverage, minus stretch on years asked and advanced degrees. Not a probability."""
        e.matched = sorted(s for s in {*e.must, *e.preferred} if s in self.my_skills)
        e.gaps_must = sorted(s for s in e.must if s not in self.my_skills)
        e.gaps_pref = sorted(s for s in e.preferred if s not in self.my_skills)
        if not e.has_text:
            e.fit, e.fit_reason = 3, "Posting text wasn't available; can't judge (3 = unknown)"
            return
        reasons = []
        if e.must:
            have = len(e.must) - len(e.gaps_must)
            fit = 1 + round(4 * have / len(e.must))
            reasons.append(f"has {have}/{len(e.must)} must-have skills"
                           + (f" (missing {', '.join(e.gaps_must[:3])})" if e.gaps_must else ""))
        else:
            fit = 4
            reasons.append("no must-have skills named")
        mine = float(self.me.get("full_time_years") or 0)
        if e.years_min is not None and e.years_min - mine >= 3:
            fit -= 2
            reasons.append(f"asks {e.years_min:g}+ years")
        elif e.years_min is not None and e.years_min - mine >= 2:
            fit -= 1
            reasons.append(f"asks {e.years_min:g}+ years")
        elif e.years_min:
            reasons.append(f"asks {e.years_min:g}+ year(s); internships may count")
        if e.advanced_degree_required:
            fit -= 1
            reasons.append("requires an advanced degree")
        e.fit = max(1, min(5, fit))
        e.fit_reason = "; ".join(reasons)


def q(text: str | None) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= MAX_QUOTE else t[:MAX_QUOTE - 1].rsplit(" ", 1)[0] + "…"


def load_entries(session: Session, reader: Reader) -> list[Entry]:
    rows = session.execute(
        select(Job, Company.name, JobStatus).join(Company, Company.id == Job.company_id)
        .join(JobStatus, JobStatus.job_id == Job.id).where(JobStatus.status.in_(APPLIED))
        .order_by(JobStatus.applied_at.nulls_last(), Job.id)
    ).all()
    return [reader.read(job, company, status) for job, company, status in rows]


# ---------- applications.md / .csv ----------

def _quoted(d: dict[str, str]) -> list[str]:
    return [f"**{k}**: “{v}”" for k, v in d.items()]


def _list(items: list[str]) -> str:
    return "".join(f"\n  - {i}" for i in items) if items else f" {NS}"


def entry_md(e: Entry) -> str:
    def field_(label: str, items: list[str]) -> str:
        return f"- {label}:{_list(items) if len(items) != 1 else ' ' + items[0]}"

    return "\n".join([
        f'### <a id="j{e.job_id}"></a>{e.company}: {e.title}',
        f"- Status: {e.status} · Applied: {e.applied} · Fit: **{e.fit}/5**: {e.fit_reason}",
        f"- Location: {e.location} ({e.arrangement})",
        f"- Posting: {e.url}",
        f"- Role family: {e.family}",
        f"- Pay: {e.pay}",
        field_("Bonus / equity", [f"“{x}”" for x in e.bonus_equity]),
        f"- Years of experience asked: {e.years}",
        field_("Degree / major", [f"“{x}”" for x in e.degree]),
        field_("Graduation-year constraints", [f"“{x}”" for x in e.grad_year]),
        field_("Relocation / travel", [f"“{x}”" for x in e.relocation_travel]),
        field_("Work authorization", [f"“{x}”" for x in e.work_auth]),
        field_("Must-have skills and tools", _quoted(e.must)),
        field_("Preferred / nice-to-have", _quoted(e.preferred)),
        field_("Soft skills screened for", _quoted(e.soft)),
        field_("Experience types that count", _quoted(e.exp_types)),
        field_("Application requirements", _quoted(e.app_reqs)),
        field_("Application limits", [f"“{x}”" for x in e.limits]),
        field_("Certifications named", e.certs),
        f"- My matches: {', '.join(e.matched) or 'none named'}",
        f"- My gaps (must-have): {', '.join(e.gaps_must) or 'none'}",
        f"- My gaps (preferred): {', '.join(e.gaps_pref) or 'none'}",
        "" if e.has_text else "- ⚠ The posting text wasn't stored (list-only source): most fields are not stated.",
    ]).rstrip() + "\n"


def applications_md(entries: list[Entry], now: datetime) -> str:
    head = [
        "# Applications backlog",
        "",
        f"{len(entries)} applications · generated {now:%Y-%m-%d %H:%M} UTC from the job board. Regenerated on every new "
        "application; don't edit by hand (add notes on the job's page instead).",
        "Quotes are the posting's exact words. “Not stated” means the posting doesn't say. "
        "Fit is a 1–5 match label, not a chance of an interview.",
        "",
    ]
    return "\n".join(head) + "\n" + "\n".join(entry_md(e) for e in entries)


def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


CSV_COLUMNS = ["job_id", "company", "title", "location", "arrangement", "date_applied", "status", "posting_url",
               "role_family", "pay", "bonus_equity", "years_asked", "degree", "majors", "grad_year_constraints",
               "relocation_travel", "work_authorization", "must_have_skills", "preferred_skills", "soft_skills",
               "experience_types", "application_requirements", "application_limits", "certifications", "my_matches",
               "my_gaps_must", "my_gaps_preferred", "fit_score", "fit_reason"]


def applications_csv(entries: list[Entry]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    j = "; ".join
    for e in entries:
        w.writerow([_cell(v) for v in [
            e.job_id, e.company, e.title, e.location, e.arrangement, e.applied, e.status, e.url, e.family, e.pay,
            j(e.bonus_equity) or NS, e.years, j(e.degree) or NS, j(e.majors), j(e.grad_year) or NS,
            j(e.relocation_travel) or NS, j(e.work_auth) or NS, j(e.must), j(e.preferred), j(e.soft), j(e.exp_types),
            j(e.app_reqs) or NS, j(e.limits) or NS, j(e.certs), j(e.matched), j(e.gaps_must), j(e.gaps_pref), e.fit,
            e.fit_reason]])
    return "﻿" + buf.getvalue()


# ---------- rollup.md ----------

def _refs(entries: list[Entry], limit: int = 6) -> str:
    links = [f"[#{e.job_id}](applications.md#j{e.job_id})" for e in entries[:limit]]
    return " ".join(links) + (f" +{len(entries) - limit}" if len(entries) > limit else "")


def _flag(n: int) -> str:
    return " ⚠ fewer than 3 postings" if n < 3 else ""


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["_None yet._"]
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _counts(entries: list[Entry], get) -> dict[str, list[Entry]]:
    by: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        for k in get(e):
            by[k].append(e)
    return dict(sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def rollup_md(entries: list[Entry], reader: Reader, now: datetime) -> str:
    n = len(entries)
    read = [e for e in entries if e.has_text]
    out = ["# Application rollup", "",
           f"{n} applications ({len(read)} with posting text) · generated {now:%Y-%m-%d %H:%M} UTC. "
           "Links go to entries in applications.md. ⚠ marks anything resting on fewer than 3 postings.", ""]
    if not entries:
        return "\n".join(out + ["_No applications yet._"]) + "\n"

    mentioned = _counts(read, lambda e: {*e.must, *e.preferred})
    must = _counts(read, lambda e: e.must)
    pref = _counts(read, lambda e: e.preferred)

    def skill_rows(tools: bool, limit: int = 12) -> list[list[Any]]:
        return [[s, f"{len(es)}/{len(read)}", len(must.get(s, [])), len(pref.get(s, [])),
                 "yes" if s in reader.my_skills else "**no**", _refs(es, 4) + ("" if len(es) >= 3 else " ⚠")]
                for s, es in mentioned.items() if (s in reader.tools) == tools][:limit]

    out += ["## 1. Frequency across applications", "", "**Technical skills**", ""]
    out += _table(["Skill", "Postings", "Must", "Preferred", "I have", "Sources"], skill_rows(False))
    out += ["", "**Tools**", ""]
    out += _table(["Tool", "Postings", "Must", "Preferred", "I have", "Sources"], skill_rows(True))
    soft = _counts(read, lambda e: e.soft)
    out += ["", "**Soft skills**", ""]
    out += _table(["Soft skill", "Postings", "Sources"],
                  [[k, len(v), _refs(v, 4) + ("" if len(v) >= 3 else " ⚠")] for k, v in list(soft.items())[:10]])

    def years_bucket(e: Entry) -> str:
        if e.years_min is None:
            return NS
        return "0" if e.years_min == 0 else "1" if e.years_min < 2 else "2" if e.years_min < 3 else "3+"
    years = _counts(entries, lambda e: [years_bucket(e)])
    out += ["", "**Years of experience asked**", ""]
    out += _table(["Years", "Postings", "Sources"], [[k, len(v), _refs(v, 5)] for k, v in years.items()])

    def degree_bucket(e: Entry) -> str:
        if not e.degree:
            return NS
        return "advanced degree required" if e.advanced_degree_required else "bachelor's (or unspecified degree)"
    degrees = _counts(entries, lambda e: [degree_bucket(e)])
    majors = _counts(read, lambda e: e.majors)
    out += ["", "**Degree requirements**", ""]
    out += _table(["Degree", "Postings", "Sources"], [[k, len(v), _refs(v, 5)] for k, v in degrees.items()])
    if majors:
        out += ["", "Majors named: " + ", ".join(f"{k} ({len(v)})" for k, v in majors.items())]

    out += ["", "**Posted pay by role family** (base, annualized; estimates excluded)", ""]
    fam = _counts(entries, lambda e: [e.family])
    pay_rows = []
    for f, es in fam.items():
        bands = [e.pay_band for e in es if e.pay_band]
        if bands:
            mids = [(lo + hi) / 2 for lo, hi in bands]
            pay_rows.append([f, f"{len(bands)}/{len(es)}", f"${min(lo for lo, _ in bands):,.0f}",
                             f"${statistics.median(mids):,.0f}", f"${max(hi for _, hi in bands):,.0f}",
                             _flag(len(bands)).strip() or "ok"])
        else:
            pay_rows.append([f, f"0/{len(es)}", NS, NS, NS, "no posted pay"])
    out += _table(["Role family", "With pay", "Lowest", "Median midpoint", "Highest", "Note"], pay_rows)

    out += ["", "## 2. Must-have vs preferred, ranked", ""]
    ranked = sorted(mentioned, key=lambda s: (-len(must.get(s, [])), -len(pref.get(s, [])), s))
    out += _table(["Skill / tool", "Must-have", "Preferred", "I have"],
                  [[s, len(must.get(s, [])), len(pref.get(s, [])), "yes" if s in reader.my_skills else "**no**"]
                   for s in ranked[:20]])

    out += ["", "## 3. Strong candidate profile (what these postings imply by senior year)", ""]
    third = max(1, len(read) // 3)
    core = [s for s, es in must.items() if len(es) >= third]
    exp = _counts(read, lambda e: e.exp_types)
    certs = _counts(read, lambda e: e.certs)
    lead = [e for e in read if "Leadership" in e.soft or "leadership" in e.exp_types]
    stated = [e.years_min for e in entries if e.years_min is not None]
    bullets = [
        f"Must-have skills in at least a third of postings: {', '.join(f'{s} ({len(must[s])})' for s in core) or 'none yet'}"
        + _flag(len(read)),
        f"Experience types postings say count: {', '.join(f'{k} ({len(v)})' for k, v in exp.items()) or NS}",
        f"Years asked: {len(stated)} of {n} postings state a number; median {statistics.median(stated):g}"
        if stated else f"Years asked: no posting states a number{_flag(0)}",
        f"Degree: {', '.join(f'{k} ({len(v)})' for k, v in degrees.items())}; majors named: "
        f"{', '.join(majors) or NS}",
        f"Certifications named: {', '.join(f'{k} ({len(v)})' for k, v in certs.items()) or 'none in any posting'}",
        f"Leadership evidence asked for in {len(lead)} postings{_flag(len(lead))}"
        + (f" ({_refs(lead, 5)})" if lead else ""),
        f"Top soft skills: {', '.join(f'{k} ({len(v)})' for k, v in list(soft.items())[:5]) or NS}",
    ]
    out += [f"- {b}" for b in bullets]
    out += ["", "Read as: by senior year a strong applicant shows the core skills above in real work (internships or "
            "projects with numbers), plus the experience types and soft skills listed, on the résumé's first half."]

    out += ["", "## 4. My gaps, ranked by postings blocked", ""]
    gaps = sorted({s for e in read for s in e.gaps_must + e.gaps_pref},
                  key=lambda s: (-len([e for e in read if s in e.gaps_must]),
                                 -len([e for e in read if s in e.gaps_pref]), s))
    gap_rows = []
    for s in gaps[:12]:
        blocks = [e for e in read if s in e.gaps_must]
        prefs = [e for e in read if s in e.gaps_pref]
        gap_rows.append([s, len(blocks), len(prefs), reader.gap_fixes.get(s, f"One small project that uses {s}, "
                         "written up with a result"), _refs(blocks + prefs, 4) + _flag(len(blocks) + len(prefs))])
    out += _table(["Gap", "Must-have in", "Preferred in", "Cheapest fix", "Sources"], gap_rows)
    mine = float(reader.me.get("full_time_years") or 0)
    stretch_years = [e for e in entries if e.years_min is not None and e.years_min - mine >= 2]
    adv = [e for e in entries if e.advanced_degree_required]
    other = []
    if stretch_years:
        other.append(f"Asks 2+ years more than my {mine:g} full-time: {len(stretch_years)} postings "
                     f"({_refs(stretch_years)}){_flag(len(stretch_years))}. Cheapest fix: lead the résumé with "
                     "internship outcomes and count internship months where the posting allows it.")
    if adv:
        other.append(f"Advanced degree required: {len(adv)} postings ({_refs(adv)}){_flag(len(adv))}.")
    out += [""] + ([f"- {o}" for o in other] or ["- No years or degree gaps across these postings."])

    out += ["", "## 5. Patterns worth acting on", ""]
    fam_rows = []
    for f, es in fam.items():
        yrs = [e.years_min for e in es if e.years_min is not None]
        fam_rows.append([f, len(es), f"{statistics.mean(e.fit for e in es):.1f}",
                         f"{statistics.median(yrs):g}" if yrs else NS, _flag(len(es)).strip() or "ok"])
    out += _table(["Role family", "Applications", "Avg fit", "Median years asked", "Note"], fam_rows)
    low = [e for e in entries if e.fit <= 2]
    places = _counts(entries, lambda e: [] if e.location == NS else
                     ["New York City" if _NYC.search(e.location) else e.location.split(",")[0].split(";")[0].strip()])
    repeat = {k: v for k, v in places.items() if len(v) >= 2}
    setups = Counter(e.arrangement for e in entries)
    reqs = _counts(read, lambda e: e.app_reqs)
    limits = [e for e in entries if e.limits]
    grad = [e for e in entries if e.grad_year]
    move = [e for e in entries if e.relocation_travel]
    auth = [e for e in entries if e.work_auth]
    patterns = [
        f"Stretch applications (fit ≤ 2): {len(low)}" + (f": {_refs(low)}" if low else "") + _flag(len(low)),
        "Repeat locations: " + (", ".join(f"{k} ({len(v)})" for k, v in repeat.items()) or "none"),
        "Work setup: " + ", ".join(f"{k} ({v})" for k, v in setups.most_common()),
        f"Graduation-year windows stated: {len(grad)}" + (f" ({_refs(grad)})" if grad else "") + _flag(len(grad)),
        f"Relocation or travel mentioned: {len(move)}" + (f" ({_refs(move)})" if move else "") + _flag(len(move)),
        f"Work-authorization language: {len(auth)}" + (f" ({_refs(auth)})" if auth else ""),
        "Application requirements: " + (", ".join(f"{k} ({len(v)})" for k, v in reqs.items()) or "none stated"),
        f"Application limits: {len(limits)}" + (": " + "; ".join(f"{e.company}: “{e.limits[0]}”"
                                                                   for e in limits) if limits else ""),
    ]
    out += [""] + [f"- {p}" for p in patterns]
    return "\n".join(out) + "\n"


def write_backlog(session: Session, config_dir: Path, private_dir: Path, out_dir: Path,
                  now: datetime | None = None) -> int:
    """Regenerate all three files. Returns the number of applications."""
    now = now or datetime.now(UTC)
    reader = Reader(config_dir, private_dir)
    entries = load_entries(session, reader)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (("applications.md", applications_md(entries, now)), ("applications.csv", applications_csv(entries)),
                       ("rollup.md", rollup_md(entries, reader, now))):
        tmp = out_dir / f".{name}.tmp"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(out_dir / name)
    return len(entries)
