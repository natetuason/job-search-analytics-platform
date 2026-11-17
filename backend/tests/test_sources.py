import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.sources.ashby import AshbySource
from app.sources.greenhouse import GreenhouseSource
from app.sources.http import HttpClient, SourceError
from app.sources.lever import LeverSource

FIXTURES = Path(__file__).parent / "fixtures"


def client_returning(payload, status: int = 200, seen: list | None = None) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return HttpClient(GreenhouseSource.policy, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_greenhouse_parses_jobs() -> None:
    seen: list[httpx.Request] = []
    jobs = GreenhouseSource(client_returning(load("greenhouse_jobs.json"), seen=seen)).fetch("acme")
    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    assert len(jobs) == 3
    bi = jobs[0]
    assert bi.external_id == "acme:7001"
    assert bi.company_name == "Acme Inc."
    assert bi.location_raw == "New York, NY"
    assert "<strong>BI Analyst</strong>" in bi.description_html
    assert bi.posted_at.isoformat() == "2026-10-20T09:00:00-04:00"
    assert bi.deadline.isoformat() == "2026-12-01"
    # Falls back to updated_at when first_published is missing.
    assert jobs[1].posted_at.isoformat() == "2026-10-25T12:00:00+00:00"
    assert jobs[1].deadline is None
    assert bi.base_min is None  # Greenhouse list endpoint has no structured pay


def test_greenhouse_prefers_configured_company_name() -> None:
    jobs = GreenhouseSource(client_returning(load("greenhouse_jobs.json"))).fetch("acme", "Acme")
    assert jobs[0].company_name == "Acme"


def test_lever_parses_postings_and_salary() -> None:
    jobs = LeverSource(client_returning(load("lever_postings.json"))).fetch("acme", "Acme")
    so = jobs[0]
    assert so.company_name == "Acme"
    assert so.apply_url.endswith("/apply?lever-source=LinkedIn")
    assert so.work_arrangement_hint == "onsite"
    assert so.employment_type_hint == "Full-time"
    assert (so.base_min, so.base_max, so.pay_period, so.currency) == (Decimal("105000"), Decimal("130000"), "year", "USD")
    assert so.comp_parse_source == "lever_salary_range"
    assert "- 2+ years of experience" in so.description_text
    # Monthly salary isn't representable; it stays out of the confirmed fields.
    assert jobs[1].base_min is None and jobs[1].pay_period is None


@pytest.mark.parametrize("source_cls", [GreenhouseSource, LeverSource])
def test_rejects_suspicious_tokens(source_cls) -> None:
    with pytest.raises(SourceError, match="invalid"):
        source_cls(client_returning({})).fetch("../admin")


def test_unexpected_shape_is_source_error() -> None:
    with pytest.raises(SourceError, match="unexpected"):
        GreenhouseSource(client_returning([])).fetch("acme")
    with pytest.raises(SourceError, match="unexpected"):
        LeverSource(client_returning({"ok": False})).fetch("acme")


def test_missing_board_is_source_error() -> None:
    with pytest.raises(SourceError, match="404"):
        GreenhouseSource(client_returning({"status": 404}, status=404)).fetch("nope")


@pytest.mark.parametrize("ranges,expected", [
    ([{"min_cents": 16500000, "max_cents": 19000000, "currency_type": "USD", "title": "Annual Base Salary Range:"}],
     (Decimal("165000"), Decimal("190000"), "year")),
    ([{"min_cents": 3500, "max_cents": 4500, "currency_type": "USD", "title": "Hourly Pay"}],
     (Decimal("35"), Decimal("45"), "hour")),
    ([{"min_cents": 2000000, "max_cents": 3000000, "currency_type": "USD", "title": "Annual Bonus Target"},
      {"min_cents": 11000000, "max_cents": 13000000, "currency_type": "USD", "title": "Base Salary"}],
     (Decimal("110000"), Decimal("130000"), "year")),
    ([{"min_cents": 11000000, "max_cents": 13000000, "currency_type": "CAD", "title": "Base Salary"}],
     (None, None, None)),
    ([{"min_cents": 15000000, "max_cents": 20000000, "currency_type": "USD", "title": "OTE Range"}],
     (None, None, None)),
])
def test_greenhouse_pay_range_parsing(ranges, expected) -> None:
    job = {"id": 1, "title": "Analyst", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
           "pay_input_ranges": ranges}
    raw = GreenhouseSource(client_returning(job)).fetch_one("acme", "1")
    assert (raw.base_min, raw.base_max, raw.pay_period) == expected


ASHBY_BOARD = {"jobs": [
    {"id": "5d8a-1", "title": "Strategy & Operations Analyst ", "employmentType": "FullTime", "location": "New York, NY",
     "secondaryLocations": [{"location": "Jersey City, NJ"}], "publishedAt": "2026-11-01T18:24:47.526+00:00",
     "isListed": True, "workplaceType": "Hybrid", "address": {"postalAddress": {"addressCountry": "United States"}},
     "jobUrl": "https://jobs.ashbyhq.com/acme/5d8a-1", "applyUrl": "https://jobs.ashbyhq.com/acme/5d8a-1/application",
     "descriptionPlain": "Analyze ops with SQL.",
     "compensation": {"summaryComponents": [
         {"compensationType": "EquityCashValue", "interval": "1 YEAR", "minValue": None, "maxValue": None},
         {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 95000, "maxValue": 115000}]}},
    {"id": "5d8a-2", "title": "Unlisted", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/acme/5d8a-2"},
    {"id": "5d8a-3", "title": "Ops Intern", "employmentType": "Intern", "location": "Remote", "isListed": True,
     "workplaceType": "Remote", "jobUrl": "https://jobs.ashbyhq.com/acme/5d8a-3", "descriptionHtml": "<p>Hi</p>"},
]}


def test_ashby_parses_jobs_and_salary() -> None:
    jobs = AshbySource(client_returning(ASHBY_BOARD)).fetch("acme", "Acme")
    assert [j.external_id for j in jobs] == ["acme:5d8a-1", "acme:5d8a-3"]
    first, intern = jobs
    assert (first.title, first.location_raw, first.work_arrangement_hint) == (
        "Strategy & Operations Analyst", "New York, NY; Jersey City, NJ", "hybrid")
    assert (first.base_min, first.base_max, first.pay_period, first.comp_parse_source) == (
        Decimal("95000"), Decimal("115000"), "year", "ashby_compensation")
    assert first.apply_url.endswith("/application") and first.posted_at.year == 2026
    assert intern.base_min is None and intern.employment_type_hint == "internship"
    assert intern.description_html == "<p>Hi</p>" and intern.apply_url == "https://jobs.ashbyhq.com/acme/5d8a-3"


def test_ashby_rejects_bad_token() -> None:
    with pytest.raises(SourceError):
        AshbySource(client_returning({})).fetch("../etc")
