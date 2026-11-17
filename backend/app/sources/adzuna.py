"""Adzuna job search API (official; app_id/app_key from .env).

Adzuna's API terms permit personal job search. Every request counts against a paid
call quota, so a search stops paging as soon as its call budget is used, and failed
requests are not retried.

Adzuna aggregates many employers that have no public ATS API (Workday sites, etc.).
Its links go through an Adzuna redirect to the employer's posting. Descriptions are
short snippets. Salaries Adzuna *predicts* are never stored as posted pay.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ingestion.normalize import US_STATES
from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search"
_CONTRACT_TIME = {"full_time": "full-time", "part_time": "part-time"}
_TAG = re.compile(r"<[^>]+>")  # Adzuna wraps matched words in <strong>


@dataclass(frozen=True)
class AdzunaSearch:
    what: str
    where: str

    @property
    def ref(self) -> str:
        return f"{self.what} @ {self.where}"


class AdzunaSource(Source):
    kind = "adzuna"
    authoritative = False  # a search returns a slice of results, not a complete listing
    policy = AccessPolicy(
        name="adzuna",
        min_interval_s=1.0,
        max_retries=0,  # every attempt costs a quota call
        terms="Official API with my app key; personal job search use per Adzuna API terms.",
    )

    def __init__(
        self,
        app_id: str | None,
        app_key: str | None,
        searches: dict[str, AdzunaSearch] | None = None,
        call_budget: int = 0,
        results_per_page: int = 50,
        max_pages: int = 1,
        max_days_old: int = 7,
        client: HttpClient | None = None,
    ):
        self.app_id, self.app_key = app_id, app_key
        self.searches = searches or {}
        self.call_budget = call_budget
        self.results_per_page = results_per_page
        self.max_pages = max_pages
        self.max_days_old = max_days_old
        self.client = client or HttpClient(self.policy)

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        if not (self.app_id and self.app_key):
            raise SourceError("adzuna: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env")
        search = self.searches.get(ref)
        if search is None:
            raise SourceError(f"adzuna: unknown search {ref!r}")
        jobs: list[RawJob] = []
        for page in range(1, self.max_pages + 1):
            if self.call_budget <= 0:
                break
            self.call_budget -= 1
            data = self.client.get_json(f"{BASE_URL}/{page}", params={
                "app_id": self.app_id, "app_key": self.app_key, "what": search.what, "where": search.where,
                "results_per_page": self.results_per_page, "max_days_old": self.max_days_old,
                "sort_by": "date", "content-type": "application/json",
            })
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise SourceError("adzuna: unexpected response shape")
            jobs.extend(parse_result(r) for r in data["results"]
                        if r.get("id") and r.get("redirect_url") and r.get("title"))
            if len(data["results"]) < self.results_per_page or page * self.results_per_page >= data.get("count", 0):
                break
        return jobs


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _location(loc: dict[str, Any]) -> str | None:
    """'Jersey City, NJ' from area ['US', 'New Jersey', 'Hudson County', 'Jersey City'].

    Any NYC neighborhood becomes 'New York, NY'; a county with no town becomes just the state.
    """
    area = loc.get("area") or []
    state = US_STATES.get(area[1].lower()) if len(area) > 1 else None
    if not state:
        return loc.get("display_name")
    if "New York City" in area:
        return "New York, NY"
    if len(area) > 3:
        return f"{area[3]}, {state}"
    if len(area) == 3 and not area[2].endswith("County"):
        return f"{area[2]}, {state}"
    return state


def parse_result(r: dict[str, Any]) -> RawJob:
    company = ((r.get("company") or {}).get("display_name") or "").strip() or "Unknown company"
    posted = r.get("salary_is_predicted") in ("0", 0, False)
    lo, hi = _money(r.get("salary_min")), _money(r.get("salary_max"))
    period = ("hour" if (hi or lo) < 500 else "year") if posted and lo else None
    created = r.get("created")
    return RawJob(
        source_kind="adzuna",
        external_id=str(r["id"]),
        company_name=company,
        title=_TAG.sub("", r["title"]).strip(),
        apply_url=r["redirect_url"],
        source_url=r["redirect_url"],
        location_raw=_location(r.get("location") or {}),
        description_text=_TAG.sub("", r.get("description") or "") or None,
        posted_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        employment_type_hint=_CONTRACT_TIME.get(r.get("contract_time") or "")
        or ("contract" if r.get("contract_type") == "contract" else None),
        country_hint="US",
        base_min=lo if period else None,
        base_max=hi if period else None,
        pay_period=period,
        currency="USD" if period else None,
        comp_parse_source="adzuna_salary" if period else None,
        raw=r,
    )
