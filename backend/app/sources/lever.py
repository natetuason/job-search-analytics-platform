"""Lever public Postings API (no auth; read-only published postings).

Docs: https://github.com/lever/postings-api
"""

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

BASE_URL = "https://api.lever.co/v0/postings"
_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
# Lever salary intervals we can express; others (monthly, one-time) are left in the raw payload.
_INTERVALS = {"per-year-salary": "year", "per-hour-wage": "hour"}


class LeverSource(Source):
    kind = "lever"
    authoritative = True
    policy = AccessPolicy(
        name="lever",
        min_interval_s=1.0,
        terms="Public Postings API for published postings; GET only, no auth.",
    )

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient(self.policy)

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        if not _TOKEN.match(ref):
            raise SourceError(f"lever: invalid company token {ref!r}")
        data = self.client.get_json(f"{BASE_URL}/{ref}", params={"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("lever: unexpected response shape")
        return [parse_posting(p, ref, company_name) for p in data]

    def fetch_one(self, token: str, posting_id: str, company_name: str | None = None) -> RawJob:
        if not _TOKEN.match(token) or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", posting_id):
            raise SourceError("lever: invalid company token or posting id")
        data = self.client.get_json(f"{BASE_URL}/{token}/{posting_id}", params={"mode": "json"})
        if not isinstance(data, dict) or "id" not in data:
            raise SourceError("lever: unexpected response shape")
        return parse_posting(data, token, company_name)


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _description(p: dict[str, Any]) -> str:
    parts = [p.get("descriptionPlain") or ""]
    for section in p.get("lists") or []:
        items = re.sub(r"<[^>]+>", "\n", section.get("content") or "")
        lines = [ln.strip() for ln in items.splitlines() if ln.strip()]
        parts.append(section.get("text", "") + "\n" + "\n".join(f"- {ln}" for ln in lines))
    parts.append(p.get("additionalPlain") or "")
    return "\n\n".join(x.strip() for x in parts if x and x.strip())


def parse_posting(p: dict[str, Any], token: str, company_name: str | None) -> RawJob:
    cats = p.get("categories") or {}
    salary = p.get("salaryRange") or {}
    period = _INTERVALS.get(salary.get("interval", ""))
    created = p.get("createdAt")
    return RawJob(
        source_kind="lever",
        external_id=f"{token}:{p['id']}",
        company_name=company_name or token,
        title=p["text"].strip(),
        apply_url=p.get("applyUrl") or p["hostedUrl"],
        source_url=p.get("hostedUrl"),
        location_raw=cats.get("location"),
        description_text=_description(p),
        posted_at=datetime.fromtimestamp(int(created) / 1000, UTC) if created else None,
        work_arrangement_hint=p.get("workplaceType"),
        employment_type_hint=cats.get("commitment"),
        country_hint=p.get("country"),
        base_min=_money(salary.get("min")) if period else None,
        base_max=_money(salary.get("max")) if period else None,
        pay_period=period,
        currency=salary.get("currency") if period else None,
        comp_parse_source="lever_salary_range" if period else None,
        raw=p,
    )
