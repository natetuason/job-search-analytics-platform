"""Pay estimates for jobs that don't post pay, from similar postings that do.

"Similar" = same role family, not senior, in the same location group (NYC/NJ first,
then anywhere in the U.S.). The estimate is the 25th-75th percentile of those
postings' base midpoints. Confidence is never "high": it's an estimate.
"""

import re
from dataclasses import dataclass
from statistics import quantiles

FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("supply_chain", re.compile(r"supply chain|planning|planner|inventory|procurement|logistics|s&op|sourcing|buyer", re.I)),
    ("product", re.compile(r"product", re.I)),
    ("project_program", re.compile(r"project|program", re.I)),
    ("risk", re.compile(r"risk|compliance|fraud|credit", re.I)),
    ("finance", re.compile(r"financ|fp&a|accounting|treasury|investment", re.I)),
    ("strategy_ops", re.compile(r"strateg|operations|consult|transformation", re.I)),
    ("business_analyst", re.compile(r"business analyst|business systems|systems analyst", re.I)),
    ("data_analytics", re.compile(r"data|analytics|insights|intelligence|\bbi\b|analyst", re.I)),
]


def family(title: str) -> str:
    return next((name for name, pattern in FAMILIES if pattern.search(title)), "other")


@dataclass
class Sample:
    job_id: int
    family: str
    nynj: bool
    midpoint: float  # annual


@dataclass
class Estimate:
    low: float
    high: float
    confidence: str
    evidence: dict


def build_index(samples: list[Sample]) -> dict[tuple[str, bool | None], list[Sample]]:
    index: dict[tuple[str, bool | None], list[Sample]] = {}
    for s in samples:
        index.setdefault((s.family, True if s.nynj else False), []).append(s)
        index.setdefault((s.family, None), []).append(s)
    return index


def estimate(title: str, nynj: bool, index: dict, min_samples: int = 10, medium_at: int = 30) -> Estimate | None:
    fam = family(title)
    for key, scope in (((fam, True), "NYC/NJ postings"), ((fam, None), "U.S. postings")):
        if key[1] is True and not nynj:
            continue
        group = index.get(key) or []
        if len(group) >= min_samples:
            q = quantiles([s.midpoint for s in group], n=4)
            return Estimate(
                low=round(q[0], -3), high=round(q[2], -3),
                confidence="medium" if len(group) >= medium_at else "low",
                evidence={"method": "25th-75th percentile of posted base midpoints of similar postings",
                          "family": fam, "scope": scope, "n": len(group),
                          "example_job_ids": [s.job_id for s in group[:5]]},
            )
    return None
