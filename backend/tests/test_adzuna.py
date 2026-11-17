from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.ingestion.batch import AdzunaConfig, adzuna_calls_remaining, load_adzuna
from app.ingestion.pipeline import ingest
from app.log import redact
from app.models import IngestionRun, Job
from app.sources.adzuna import AdzunaSearch, AdzunaSource, parse_result
from app.sources.http import HttpClient

SEARCH = AdzunaSearch("business analyst", "New Jersey")


def result(i: int, **over):
    base = {
        "id": str(5000 + i), "title": "Junior <strong>Business Analyst</strong>",
        "company": {"display_name": "Prudential Financial"},
        "location": {"display_name": "Newark, Essex County", "area": ["US", "New Jersey", "Essex County", "Newark"]},
        "description": "Support <strong>analyst</strong> work with SQL...", "created": "2026-11-03T12:00:00Z",
        "redirect_url": f"https://www.adzuna.com/land/ad/{5000 + i}?se=x", "contract_time": "full_time",
        "salary_min": 85000, "salary_max": 95000, "salary_is_predicted": "1",
    }
    return base | over


def make_source(pages: list[list[dict]], count: int, budget: int, calls: list, app_key="k"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = int(request.url.path.rsplit("/", 1)[1])
        if page > len(pages):
            return httpx.Response(500)
        return httpx.Response(200, json={"count": count, "results": pages[page - 1]})

    client = HttpClient(AdzunaSource.policy, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    return AdzunaSource("id", app_key, {SEARCH.ref: SEARCH}, call_budget=budget, results_per_page=2,
                        max_pages=3, client=client)


def test_parse_result_cleans_title_and_never_stores_predicted_pay() -> None:
    job = parse_result(result(1))
    assert (job.title, job.company_name, job.location_raw) == ("Junior Business Analyst", "Prudential Financial", "Newark, NJ")
    assert job.description_text == "Support analyst work with SQL..."
    assert job.base_min is None and job.raw["salary_min"] == 85000  # prediction kept only in the raw payload
    assert job.employment_type_hint == "full-time" and job.posted_at.tzinfo is not None


@pytest.mark.parametrize(("area", "expected"), [
    (["US", "New York", "New York City", "Manhattan", "Grand Central"], "New York, NY"),
    (["US", "New York", "New York City"], "New York, NY"),
    (["US", "New Jersey", "Hudson County", "Jersey City"], "Jersey City, NJ"),
    (["US", "New Jersey", "Somerset County"], "NJ"),
    (["US", "New Jersey"], "NJ"),
])
def test_location_from_area(area, expected) -> None:
    assert parse_result(result(1, location={"display_name": "x", "area": area})).location_raw == expected


def test_parse_result_stores_posted_pay() -> None:
    job = parse_result(result(1, salary_is_predicted="0", salary_min=40, salary_max=45))
    assert (job.base_min, job.base_max, job.pay_period, job.comp_parse_source) == (
        Decimal("40"), Decimal("45"), "hour", "adzuna_salary")
    assert parse_result(result(2, salary_is_predicted="0")).pay_period == "year"


def test_paging_stops_at_budget_and_at_last_page() -> None:
    calls: list = []
    source = make_source([[result(1), result(2)], [result(3), result(4)], [result(5)]], count=5, budget=2, calls=calls)
    assert len(source.fetch(SEARCH.ref)) == 4 and len(calls) == 2 and source.call_budget == 0

    calls.clear()
    source = make_source([[result(1)]], count=1, budget=5, calls=calls)
    assert len(source.fetch(SEARCH.ref)) == 1 and len(calls) == 1  # short page means no more results


def test_failed_call_is_not_retried_and_is_counted(session: Session) -> None:
    calls: list = []
    source = make_source([], count=0, budget=3, calls=calls)  # every page returns HTTP 500
    run = ingest(session, source, SEARCH.ref)
    assert run.status == "failed" and len(calls) == 1 and run.api_calls == 1


def test_missing_keys_make_no_calls(session: Session) -> None:
    calls: list = []
    run = ingest(session, make_source([[result(1)]], 1, 5, calls, app_key=None), SEARCH.ref)
    assert run.status == "failed" and "ADZUNA_APP_ID" in run.errors[0]["error"] and calls == []


def test_ingest_records_calls_and_jobs(session: Session) -> None:
    calls: list = []
    run = ingest(session, make_source([[result(1), result(2)], [result(3)]], 3, 5, calls), SEARCH.ref)
    assert (run.status, run.fetched_count, run.api_calls) == ("succeeded", 3, 2)
    assert session.query(Job).count() == 1  # same company/title/location collapses to one job


def test_calls_remaining_respects_daily_total_and_flag(session: Session) -> None:
    now = datetime(2026, 11, 5, 15, tzinfo=UTC)
    session.add_all([
        IngestionRun(source_kind="adzuna", source_ref="a", status="succeeded", started_at=now, api_calls=3),
        IngestionRun(source_kind="adzuna", source_ref="a", status="succeeded",
                     started_at=datetime(2026, 11, 4, 15, tzinfo=UTC), api_calls=10),
    ])
    session.flush()
    assert adzuna_calls_remaining(session, AdzunaConfig([], daily_call_cap=10), None, now) == 7
    assert adzuna_calls_remaining(session, AdzunaConfig([], 10, total_call_cap=15), None, now) == 2
    assert adzuna_calls_remaining(session, AdzunaConfig([], 10), 4, now) == 4
    assert adzuna_calls_remaining(session, AdzunaConfig([], 10, total_call_cap=5), None, now) == 0


def test_repo_adzuna_config_loads() -> None:
    cfg = load_adzuna(Path(__file__).resolve().parents[2] / "config" / "adzuna.yaml")
    assert cfg.searches and cfg.daily_call_cap > 0


def test_keys_are_redacted_from_logs() -> None:
    assert redact("GET /search/1?app_id=3a81&app_key=af63xyz&what=x") == "GET /search/1?app_id=***&app_key=***&what=x"


def test_load_adzuna_requires_cap(tmp_path: Path) -> None:
    p = tmp_path / "a.yaml"
    p.write_text("searches: [{what: a, where: b}]\n")
    with pytest.raises(ValueError, match="daily_call_cap"):
        load_adzuna(p)
