"""Greenhouse public Job Board API (no auth; read-only published postings).

Docs: https://developers.greenhouse.io/job-board.html
"""

import html
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")


class GreenhouseSource(Source):
    kind = "greenhouse"
    authoritative = True
    policy = AccessPolicy(
        name="greenhouse",
        min_interval_s=1.0,
        terms="Public Job Board API intended for displaying published postings; GET only, no auth.",
    )

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient(self.policy)

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        if not _TOKEN.match(ref):
            raise SourceError(f"greenhouse: invalid board token {ref!r}")
        data = self.client.get_json(f"{BASE_URL}/{ref}/jobs", params={"content": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("greenhouse: unexpected response shape")
        return [parse_job(j, ref, company_name) for j in data["jobs"]]

    def fetch_one(self, board: str, job_id: str, company_name: str | None = None) -> RawJob:
        """One posting, including structured pay ranges when the employer publishes them."""
        if not _TOKEN.match(board) or not job_id.isdigit():
            raise SourceError("greenhouse: invalid board or job id")
        data = self.client.get_json(f"{BASE_URL}/{board}/jobs/{job_id}", params={"pay_transparency": "true"})
        if not isinstance(data, dict) or "id" not in data:
            raise SourceError("greenhouse: unexpected response shape")
        raw = parse_job(data, board, company_name)
        _apply_pay_range(raw, data.get("pay_input_ranges") or [])
        return raw


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_job(j: dict[str, Any], board: str, company_name: str | None) -> RawJob:
    deadline = _dt(j.get("application_deadline"))
    return RawJob(
        source_kind="greenhouse",
        external_id=f"{board}:{j['id']}",
        # Prefer the name I configured, so "Chime" doesn't become "Chime Financial, Inc".
        company_name=company_name or j.get("company_name") or board,
        title=j["title"].strip(),
        apply_url=j["absolute_url"],
        source_url=f"{BASE_URL}/{board}/jobs/{j['id']}",
        location_raw=(j.get("location") or {}).get("name"),
        # Greenhouse returns HTML-escaped HTML in `content`.
        description_html=html.unescape(j.get("content") or ""),
        posted_at=_dt(j.get("first_published")) or _dt(j.get("updated_at")),
        deadline=deadline.date() if isinstance(deadline, datetime) else None,
        raw=j,
    )



def _apply_pay_range(raw: RawJob, ranges: list[dict[str, Any]]) -> None:
    """Use the first USD range whose label reads as base pay (salary/hourly), not bonus or OTE."""
    for r in ranges:
        label = (r.get("title") or "").lower()
        if r.get("currency_type") != "USD" or re.search(r"bonus|commission|ote|on-target|equity", label):
            continue
        lo, hi = r.get("min_cents"), r.get("max_cents")
        if not lo or not hi:
            continue
        lo_d, hi_d = Decimal(lo) / 100, Decimal(hi) / 100
        if "hour" in label or hi_d < 500:
            period = "hour"
        elif re.search(r"annual|salary|base|year", label) or hi_d >= 20000:
            period = "year"
        else:
            continue
        raw.base_min, raw.base_max = min(lo_d, hi_d), max(lo_d, hi_d)
        raw.pay_period, raw.currency = period, "USD"
        raw.comp_parse_source = "greenhouse_pay_range"
        return
