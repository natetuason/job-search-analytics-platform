"""Read structured facts out of job titles and description text.

Everything here is pattern matching on what the posting says. Nothing is guessed:
a function returns None when the text doesn't say.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

# ---------- Pay ----------

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_RANGE = re.compile(rf"\$\s?{_NUM}\s*(?:USD|usd)?\s*(?:-|–|—|to)\s*\$?\s?{_NUM}")
_HOURLY = re.compile(r"^\s*(?:USD\s*)?(?:/\s*(?:hr|hour)|per\s+hour|an\s+hour|hourly)", re.I)
_PAY_CONTEXT = re.compile(r"salary|pay|compensation|base|wage|rate|range|annual|per year|/year|usd", re.I)
_NOT_BASE = re.compile(r"bonus|commission|\bote\b|on-target|equity|stipend|tuition|reimburse|allowance|per quarter|per month",
                       re.I)
_NYC = re.compile(r"new york|\bnyc\b|\bny\b|new jersey|\bnj\b|tier 1|zone 1|region 1", re.I)


@dataclass
class ParsedPay:
    base_min: Decimal
    base_max: Decimal
    pay_period: str  # "year" | "hour"
    snippet: str


def _amount(num: str, k: str | None) -> Decimal:
    value = Decimal(num.replace(",", ""))
    return value * 1000 if k else value


def parse_pay(text: str | None) -> ParsedPay | None:
    """The posted base pay range, if the description states one.

    Ranges described as bonus/commission/equity/stipends are ignored. When several
    ranges are listed (by region), the one mentioning New York / NJ / zone 1 wins,
    otherwise the first.
    """
    if not text:
        return None
    candidates: list[tuple[bool, ParsedPay]] = []
    for m in _RANGE.finditer(text):
        before = text[max(0, m.start() - 160):m.start()]
        line_start = text.rfind("\n", 0, m.start()) + 1
        line = text[line_start:m.start()]
        if _NOT_BASE.search(line) or (_NOT_BASE.search(before[-60:]) and not _PAY_CONTEXT.search(line)):
            continue
        standalone = not line.strip(" -•*\t")
        if not (standalone or _PAY_CONTEXT.search(before) or _PAY_CONTEXT.search(text[m.end():m.end() + 30])):
            continue
        lo, hi = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
        if m.group(2) and not m.group(4) and hi < 1000:  # "$95k - 115"
            hi *= 1000
        if lo > hi:
            continue
        hourly = bool(_HOURLY.match(text[m.end():m.end() + 20])) or hi < 300
        if hourly and not (12 <= lo <= 300):
            continue
        if not hourly and not (20_000 <= lo and hi <= 1_000_000):
            continue
        snippet = text[max(0, m.start() - 60):m.end() + 20].replace("\n", " ").strip()
        candidates.append((bool(_NYC.search(before[-80:])), ParsedPay(lo, hi, "hour" if hourly else "year", snippet)))
    if not candidates:
        return None
    return next((p for nyc, p in candidates if nyc), candidates[0][1])


def annual(amount: Decimal | float | None, period: str | None) -> float | None:
    if amount is None:
        return None
    return float(amount) * (2080 if period == "hour" else 1)


# ---------- Experience ----------

_YEARS = re.compile(
    r"(?:(?:at\s+least|minimum(?:\s+of)?|min\.?)\s+)?(\d{1,2}(?:\.\d)?)\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?"
    r"(?:years?|yrs?)\b(?:\s+of)?([^.\n;]{0,80})", re.I)
_EXP_WORDS = re.compile(r"experience|^\s*(?:of\s+)?(?:in|as|with|working|work|professional|relevant|related|hands|"
                        r"progressive|full[- ]time|prior|direct|industry|post|building|leading|managing)\b", re.I)
_NOT_EXP = re.compile(r"\bold\b|holiday|anniversary|history|founded|in business|investing|track record|warranty|"
                      r"degree|bachelor|master|phd|college|university|school|tenure|\bage\b|service with", re.I)
_PREFERRED = re.compile(r"prefer|nice to have|a plus|bonus|ideally", re.I)


@dataclass
class ParsedExperience:
    min_years: Decimal
    max_years: Decimal | None
    snippet: str


def parse_experience(text: str | None) -> ParsedExperience | None:
    """Largest required minimum years of experience stated (preferred-only mentions ignored)."""
    if not text:
        return None
    best: ParsedExperience | None = None
    for m in _YEARS.finditer(text):
        tail = m.group(3) or ""
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line = text[line_start:line_end if line_end != -1 else len(text)]
        if not _EXP_WORDS.search(tail) or _NOT_EXP.search(tail[:40]) or _PREFERRED.search(line):
            continue
        lo = Decimal(m.group(1))
        if lo > 15:
            continue
        hi = Decimal(m.group(2)) if m.group(2) else None
        if best is None or lo > best.min_years:
            best = ParsedExperience(lo, hi, line.strip(" -•*\t")[:200])
    return best


# ---------- Title signals ----------

_SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|director|head of|vice president|vp|chief|partner|"
                     r"managing|executive)\b", re.I)
# "Manager" is senior unless it's a product/project/program manager or an associate-level title.
# The words must be adjacent: "Product Marketing Manager" is a marketing manager, not a product manager.
_MANAGER = re.compile(r"\bmanager\b", re.I)
_MANAGER_OK = re.compile(r"\b(product|project|program|associate|assistant)\s+manager\b|"
                         r"\bmanager\b[^,|-]*\b(program|rotational)\b", re.I)
_LEVEL = re.compile(r"\b(?:analyst|associate|consultant|specialist|engineer|scientist|manager)\s*(i{1,3}|iv|[1-4])\b", re.I)
_EARLY = re.compile(
    r"new grad|new graduate|recent grad|university grad|college grad|graduate program|graduate scheme|entry[- ]level|"
    r"early[- ]career|early talent|campus|class of 202[67]|202[67] (?:start|graduates?)|junior|\bjr\.?\b|"
    r"analyst program|associate program|development program|rotational|rotation program|leadership program|"
    r"0\s*(?:-|–|to)\s*[12]\s*years|no experience required|fresh graduates?", re.I)
_ROTATIONAL = re.compile(r"rotational|rotation program|rotations? (?:across|through)|leadership development program|"
                         r"\bldp\b|development program", re.I)


def seniority(title: str) -> str | None:
    """'senior' for senior/lead/manager-type titles, 'level_ii' / 'level_iii' for numbered levels, else None."""
    if _SENIOR.search(title):
        return "senior"
    if _MANAGER.search(title) and not _MANAGER_OK.search(title):
        return "senior"
    m = _LEVEL.search(title)
    if m:
        level = {"i": 1, "1": 1, "ii": 2, "2": 2, "iii": 3, "3": 3, "iv": 4, "4": 4}[m.group(1).lower()]
        if level >= 3:
            return "level_iii"
        if level == 2:
            return "level_ii"
    return None


def early_career(title: str, description: str | None) -> bool:
    if _EARLY.search(title):
        return True
    return bool(description and _EARLY.search(description[:3000]))


def rotational(title: str, description: str | None) -> bool:
    return bool(_ROTATIONAL.search(title) or (description and _ROTATIONAL.search(description[:3000])))


# ---------- Hard requirements ----------

_FLAGS: list[tuple[str, str, re.Pattern[str]]] = [
    ("no_sponsorship", "Says it won't sponsor work visas",
     re.compile(r"(?:unable|not able|will not|won't|cannot|can't|does not|do not|is not able)\s+(?:to\s+)?"
                r"(?:provide\s+|offer\s+)?(?:visa\s+|employment\s+)?sponsor|without\s+(?:the\s+need\s+for\s+|requiring\s+)?"
                r"(?:current\s+or\s+future\s+)?(?:visa\s+|employment\s+)?sponsorship|no\s+(?:visa\s+)?sponsorship|"
                r"Sponsorship \(from list\): Does Not Offer Sponsorship", re.I)),
    ("citizenship", "Requires U.S. citizenship",
     re.compile(r"(?:must\s+be|requires?|required\s+to\s+be)\s+(?:a\s+)?u\.?s\.?\s+citizen|u\.?s\.?\s+citizenship\s+"
                r"(?:is\s+)?required|us citizens only|U\.S\. Citizenship is Required", re.I)),
    ("clearance", "Requires a security clearance",
     re.compile(r"(?:active|current|ability to obtain|eligible for|must (?:have|hold|obtain))[^.\n]{0,40}"
                r"(?:security\s+clearance|ts/sci|top\s+secret|secret\s+clearance|public\s+trust)|"
                r"(?:security\s+clearance|ts/sci|top\s+secret clearance)\s+(?:is\s+)?required", re.I)),
    ("certification", "Requires a certification",
     re.compile(r"(?:\bcpa\b|\bcfa\b|\bpmp\b|series\s+\d{1,2}|\bfrm\b|six\s+sigma)[^.\n]{0,40}(?:required|must)|"
                r"(?:required|must\s+(?:have|hold))[^.\n]{0,40}(?:\bcpa\b|\bcfa\b|\bpmp\b|series\s+\d{1,2}|\bfrm\b)", re.I)),
    ("advanced_degree", "Requires an advanced degree",
     re.compile(r"(?:master'?s|mba|ph\.?d\.?)\s+(?:degree\s+)?(?:is\s+)?required|requires?\s+(?:a\s+)?(?:master'?s|mba|ph\.?d)|"
                r"Degrees \(from list\): (?:Master's|PhD)(?:, (?:Master's|PhD))*$", re.I | re.M)),
]


# "... security clearance (preferred but not required)": the qualifier right after the match.
_PREFERRED_FLAG = re.compile(r"[\s,(\-–]*(?:is\s+)?(?:preferred|not required|a plus|nice to have|desired)", re.I)

# Cheap pre-check: a flag's regex only runs if one of these words appears.
_FLAG_WORDS = {
    "no_sponsorship": ("sponsor",), "citizenship": ("citizen",), "clearance": ("clearance", "secret", "ts/sci", "public trust"),
    "certification": ("cpa", "cfa", "pmp", "series", "frm", "sigma"), "advanced_degree": ("master", "mba", "ph"),
}


def hard_flags(title: str, description: str | None) -> list[dict[str, str]]:
    text = f"{title}\n{description or ''}"
    lowered = text.lower()
    flags = []
    for kind, label, pattern in _FLAGS:
        if not any(w in lowered for w in _FLAG_WORDS[kind]):
            continue
        m = next((m for m in pattern.finditer(text) if not _PREFERRED_FLAG.match(text, m.end())), None)
        if m:
            snippet = text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ").strip()
            flags.append({"type": kind, "label": label, "evidence": snippet})
    return flags
