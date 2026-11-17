from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.manual import LinkImport, LinkImportError, extract_job_links, import_link
from app.ingestion.pipeline import ingest
from app.models import Job, JobSource
from app.sources.greenhouse import GreenhouseSource
from app.sources.http import HttpClient
from app.sources.lever import LeverSource

GH_JOB = {
    "id": 5001,
    "title": "Demand Planning Analyst",
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/5001",
    "company_name": "Acme Inc.",
    "location": {"name": "Jersey City, NJ"},
    "first_published": "2026-10-30T09:00:00-04:00",
    "content": "&lt;p&gt;Forecasting with SQL.&lt;/p&gt;",
    "pay_input_ranges": [
        {"min_cents": 9500000, "max_cents": 11500000, "currency_type": "USD", "title": "Annual Base Salary Range:"}
    ],
}
LEVER_POSTING = {
    "id": "aaaa1111-2222-3333-4444-555566667777",
    "text": "Inventory Analyst",
    "hostedUrl": "https://jobs.lever.co/acme/aaaa1111-2222-3333-4444-555566667777",
    "applyUrl": "https://jobs.lever.co/acme/aaaa1111-2222-3333-4444-555566667777/apply",
    "categories": {"commitment": "Full-time", "location": "Newark, NJ"},
    "workplaceType": "hybrid",
    "descriptionPlain": "Own inventory reporting.",
}


def mock_client(routes: dict[str, dict], calls: list[str]) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        for prefix, payload in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    return HttpClient(GreenhouseSource.policy, transport=httpx.MockTransport(handler), sleep=lambda s: None)


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def sources(calls):
    routes = {
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/5001": GH_JOB,
        "https://api.lever.co/v0/postings/acme/aaaa1111": LEVER_POSTING,
    }
    client = mock_client(routes, calls)
    return {"greenhouse": GreenhouseSource(client), "lever": LeverSource(client)}


def test_extract_job_links_ranks_ats_first() -> None:
    post = """Hiring! Apply on LinkedIn https://www.linkedin.com/jobs/view/123 or directly:
    https://boards.greenhouse.io/acme/jobs/5001?gh_src=reddit. More at https://acme.com/careers.
    pic: https://i.imgur.com/x.png  thread https://www.reddit.com/r/supplychain/comments/abc123/x/
    dup https://boards.greenhouse.io/acme/jobs/5001"""
    assert extract_job_links(post) == [
        "https://boards.greenhouse.io/acme/jobs/5001?gh_src=reddit",
        "https://acme.com/careers",
        "https://www.linkedin.com/jobs/view/123",
    ]


def test_greenhouse_link_fetches_official_api_with_pay(session: Session, sources, calls) -> None:
    result = import_link(session, LinkImport(url="https://job-boards.greenhouse.io/acme/jobs/5001?gh_src=x"), **sources)
    assert result.created and result.fetched_from == "greenhouse"
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/5001?pay_transparency=true"]
    job = session.get(Job, result.job_id)
    assert (job.title, job.base_min, job.base_max, job.pay_period) == (
        "Demand Planning Analyst", Decimal("95000.00"), Decimal("115000.00"), "year")
    assert job.comp_parse_source == "greenhouse_pay_range"
    kinds = sorted(s.source_kind for s in job.sources)
    assert kinds == ["greenhouse", "manual"]


def test_lever_link_fetches_official_api(session: Session, sources) -> None:
    url = "https://jobs.lever.co/acme/aaaa1111-2222-3333-4444-555566667777/apply?lever-source=Reddit"
    result = import_link(session, LinkImport(url=url, company="Acme"), **sources)
    job = session.get(Job, result.job_id)
    assert (job.title, job.work_arrangement, job.employment_type) == ("Inventory Analyst", "hybrid", "full_time")


def test_reddit_post_is_recorded_without_contacting_reddit(session: Session, sources, calls) -> None:
    via = "https://www.reddit.com/r/supplychain/comments/abc123/hiring_demand_planner/"
    result = import_link(session, LinkImport(url="https://boards.greenhouse.io/acme/jobs/5001", found_via=via), **sources)
    assert not any("reddit" in c for c in calls)
    reddit_src = session.scalar(select(JobSource).where(JobSource.job_id == result.job_id, JobSource.source_kind == "reddit"))
    assert reddit_src.external_id.startswith("abc123:")
    assert reddit_src.source_url == via


def test_other_sites_are_never_fetched(session: Session, sources, calls) -> None:
    url = "https://www.linkedin.com/jobs/view/4012345678/"
    with pytest.raises(LinkImportError, match="add the company and job title"):
        import_link(session, LinkImport(url=url), **sources)
    result = import_link(session, LinkImport(url=url, company="Pfizer", title="Supply Chain Analyst",
                                             location="New York, NY", description="Pasted text"), **sources)
    assert calls == []
    job = session.get(Job, result.job_id)
    assert (job.title, job.apply_url, job.description_text) == ("Supply Chain Analyst", url, "Pasted text")
    assert job.sources[0].source_kind == "manual"


def test_pasting_same_link_twice_does_not_duplicate(session: Session, sources) -> None:
    req = LinkImport(url="https://acme.wd5.myworkdayjobs.com/en-US/External/job/NY/Analyst_R1", company="Acme", title="Analyst")
    first = import_link(session, req, **sources)
    second = import_link(session, req, **sources)
    assert first.created and not second.created
    assert first.job_id == second.job_id
    assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_pasted_link_fills_pay_on_job_already_found_by_board(session: Session, sources, calls) -> None:
    board_job = dict(GH_JOB)
    board_job.pop("pay_input_ranges")
    board_client = mock_client({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [board_job]}}, calls)
    ingest(session, GreenhouseSource(board_client), "acme", "Acme")
    assert session.scalar(select(Job.base_min)) is None

    import_link(session, LinkImport(url="https://boards.greenhouse.io/acme/jobs/5001"), **sources)
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    assert session.scalar(select(Job.base_min)) == Decimal("95000.00")


def test_invalid_url_rejected(session: Session, sources) -> None:
    with pytest.raises(LinkImportError, match="full http"):
        import_link(session, LinkImport(url="javascript:alert(1)", company="x", title="y"), **sources)


def test_missing_ats_posting_is_clear_error(session: Session, sources) -> None:
    with pytest.raises(LinkImportError, match="couldn't load"):
        import_link(session, LinkImport(url="https://boards.greenhouse.io/acme/jobs/999"), **sources)
