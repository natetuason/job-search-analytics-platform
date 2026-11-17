"""Ashby public Job Posting API (no auth; published postings only).

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"
_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
_INTERVALS = {"1 YEAR": "year", "1 HOUR": "hour"}
_EMPLOYMENT = {"FullTime": "full-time", "PartTime": "part-time", "Intern": "internship",
               "Contract": "contract", "Temporary": "contract"}
_WORKPLACE = {"Hybrid": "hybrid", "Remote": "remote", "OnSite": "onsite"}


class AshbySource(Source):
    kind = "ashby"
    authoritative = True
    policy = AccessPolicy(
        name="ashby",
        min_interval_s=1.0,
        terms="Public Job Posting API for published postings; GET only, no auth.",
    )

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient(self.policy)

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        if not _TOKEN.match(ref):
            raise SourceError(f"ashby: invalid job board name {ref!r}")
        data = self.client.get_json(f"{BASE_URL}/{ref}", params={"includeCompensation": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("ashby: unexpected response shape")
        return [parse_job(j, ref, company_name) for j in data["jobs"] if j.get("isListed", True)]


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _salary(job: dict[str, Any]) -> dict[str, Any] | None:
    """The salary component Ashby summarizes for the posting, when it has a usable range."""
    comp = job.get("compensation") or {}
    for c in comp.get("summaryComponents") or []:
        if (c.get("compensationType") == "Salary" and c.get("interval") in _INTERVALS
                and c.get("minValue") is not None):
            return c
    return None


def parse_job(j: dict[str, Any], token: str, company_name: str | None) -> RawJob:
    locations = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
    country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
    salary = _salary(j)
    published = j.get("publishedAt")
    return RawJob(
        source_kind="ashby",
        external_id=f"{token}:{j['id']}",
        company_name=company_name or token,
        title=j["title"].strip(),
        apply_url=j.get("applyUrl") or j["jobUrl"],
        source_url=j.get("jobUrl"),
        location_raw="; ".join(x for x in locations if x) or None,
        description_html=None if j.get("descriptionPlain") else j.get("descriptionHtml"),
        description_text=j.get("descriptionPlain"),
        posted_at=datetime.fromisoformat(published) if published else None,
        work_arrangement_hint=_WORKPLACE.get(j.get("workplaceType") or ""),
        employment_type_hint=_EMPLOYMENT.get(j.get("employmentType") or ""),
        country_hint=country,
        base_min=_money(salary["minValue"]) if salary else None,
        base_max=_money(salary.get("maxValue")) if salary else None,
        pay_period=_INTERVALS[salary["interval"]] if salary else None,
        currency=salary.get("currencyCode") if salary else None,
        comp_parse_source="ashby_compensation" if salary else None,
        raw=j,
    )
