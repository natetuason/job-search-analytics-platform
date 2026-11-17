import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import csv_label
from app.ingestion.batch import load_lists
from app.ingestion.pipeline import ingest
from app.models import Job, JobSource
from app.sources.csv_file import CsvFileSource
from app.sources.github_lists import GitHubListSource, parse_markdown_table, parse_month_day, parse_simplify_json
from app.sources.http import HttpClient, SourceError

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
JOBRIGHT_URL = "https://raw.githubusercontent.com/jobright-ai/2026-Business-Analyst-New-Grad/master/README.md"


def listing(**over):
    base = {
        "source": "Simplify", "category": "AI/ML/Data", "company_name": "Acme", "id": "abc-1",
        "title": "Data Analyst", "active": True, "is_visible": True, "date_posted": 1793885069,
        "url": "https://acme.wd5.myworkdayjobs.com/External/job/NY/Data-Analyst_R1?utm_source=Simplify&ref=Simplify",
        "locations": ["New York, NY", "Jersey City, NJ"], "sponsorship": "Other", "degrees": ["Bachelor's"],
    }
    return base | over


def test_simplify_json_keeps_active_listings_in_wanted_categories() -> None:
    data = [
        listing(),
        listing(id="2", active=False),
        listing(id="3", is_visible=False),
        listing(id="4", category="Hardware"),
        listing(id="5", sponsorship="U.S. Citizenship is Required", degrees=[]),
        listing(id="6", url="not a url"),
    ]
    jobs = parse_simplify_json(data, SIMPLIFY_URL, ["AI/ML/Data"])
    assert [j.external_id for j in jobs] == ["SimplifyJobs/New-Grad-Positions:abc-1", "SimplifyJobs/New-Grad-Positions:5"]
    first, second = jobs
    assert first.location_raw == "New York, NY; Jersey City, NJ"
    assert first.description_text == "Degrees (from list): Bachelor's"
    assert first.posted_at.year == 2026 and first.employment_type_hint == "full-time"
    assert second.description_text == "Sponsorship (from list): U.S. Citizenship is Required"
    assert len(parse_simplify_json(data, SIMPLIFY_URL)) == 3  # no category filter


JOBRIGHT_MD = """## Daily Job List

| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- |  --------- | ---- | ------- |
| **[Hatch](https://www.hatch.com)** | **[Transit Business Analyst](https://jobright.ai/jobs/info/6aec?utm_source=1103)** | Denver, CO, United States | On Site | Nov 05 |
| ↳ | **[Junior Business Analyst](https://jobright.ai/jobs/info/7bcd)** | Remote | Remote | Dec 30 |
| **[NoLink Co](https://x.com)** | Business Analyst | NY | Hybrid | Nov 01 |

More text after the table.
"""


def test_markdown_table_rows() -> None:
    jobs = parse_markdown_table(JOBRIGHT_MD, JOBRIGHT_URL, today=date(2026, 11, 5))
    assert [(j.company_name, j.title) for j in jobs] == [("Hatch", "Transit Business Analyst"),
                                                         ("Hatch", "Junior Business Analyst")]
    assert jobs[0].apply_url == "https://jobright.ai/jobs/info/6aec?utm_source=1103"
    assert jobs[0].work_arrangement_hint == "onsite"
    assert jobs[0].posted_at.date() == date(2026, 11, 5)
    assert jobs[1].posted_at.date() == date(2025, 12, 30)  # month after today means last year
    with pytest.raises(SourceError, match="no Markdown table"):
        parse_markdown_table("# nothing here", JOBRIGHT_URL)


def test_markdown_table_jobr_pro_column_names() -> None:
    md = """| Company | Position | Location | Type | Date |
| ------- | -------- | -------- | ---- | ------ |
| **[Pivot Point](https://pp.com)** | **[Operations Data Analyst](https://jobr.pro/job/41519241/x?utm_source=github)** | Fairfield, CA | On Site | Jul 01 |
"""
    job = parse_markdown_table(md, "https://raw.githubusercontent.com/jobs-jobr-pro/Data-Analyst-Jobs/main/README.md",
                               today=date(2026, 11, 5))[0]
    assert (job.title, job.work_arrangement_hint, job.posted_at.date()) == ("Operations Data Analyst", "onsite",
                                                                           date(2026, 7, 1))
    assert job.external_id.startswith("jobs-jobr-pro/Data-Analyst-Jobs:")


def test_parse_month_day() -> None:
    assert parse_month_day("Nov 05", date(2026, 11, 5)) == date(2026, 11, 5)
    assert parse_month_day("garbage", date(2026, 11, 5)) is None


def test_only_raw_github_files_are_fetched() -> None:
    calls = []
    client = HttpClient(GitHubListSource.policy, transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)),
                        sleep=lambda s: None)
    with pytest.raises(SourceError, match="raw.githubusercontent.com"):
        GitHubListSource("markdown_table", client=client).fetch("https://jobright.ai/jobs")
    assert calls == []
    with pytest.raises(ValueError):
        GitHubListSource("html")


def test_list_ingest_is_idempotent_and_dedups_with_other_sources(session: Session) -> None:
    body = json.dumps([listing()])
    client = HttpClient(GitHubListSource.policy, sleep=lambda s: None,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    source = GitHubListSource("simplify_json", client=client)
    first = ingest(session, source, SIMPLIFY_URL)
    second = ingest(session, source, SIMPLIFY_URL)
    assert (first.new_count, second.new_count, second.updated_count) == (1, 0, 1)
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    job = session.scalar(select(Job))
    assert job.employment_type == "full_time"


def test_csv_file_import(session: Session, tmp_path: Path) -> None:
    f = tmp_path / "swejoblists-2026-11-05.csv"
    f.write_text("﻿Company,Title,Location,Type,Posted,Age,Job board,Apply URL\n"
                 "Acme,Data Analyst,\"New York, NY\",Full-time,2026-11-04,1d,Greenhouse,https://boards.greenhouse.io/acme/jobs/1\n"
                 "Beta,SWE Intern,Remote,Internship,2026-11-04,1d,Lever,https://jobs.lever.co/beta/1\n")
    source = CsvFileSource(csv_label(f))
    run = ingest(session, source, str(f))
    assert (run.status, run.new_count) == ("succeeded", 2)
    kinds = dict(session.execute(select(Job.title, Job.employment_type)).all())
    assert kinds == {"Data Analyst": "full_time", "SWE Intern": "internship"}
    src = session.scalar(select(JobSource).where(JobSource.source_kind == "csv_file").limit(1))
    assert src.external_id.startswith("swejoblists:file:") and src.source_url is None


def test_csv_file_missing_is_failed_run(session: Session, tmp_path: Path) -> None:
    run = ingest(session, CsvFileSource("x"), str(tmp_path / "nope.csv"))
    assert run.status == "failed" and "can't read" in run.errors[0]["error"]


def test_csv_label() -> None:
    assert csv_label(Path("swejoblists-2026-11-05.csv")) == "swejoblists"
    assert csv_label(Path("my list.csv")) == "my list"


def test_load_lists_validates(tmp_path: Path) -> None:
    p = tmp_path / "lists.yaml"
    p.write_text("lists:\n  - {name: a, format: simplify_json, url: u, categories: [Product]}\n")
    assert load_lists(p)[0].categories == ["Product"]
    p.write_text("lists:\n  - {format: xml, url: u}\n")
    with pytest.raises(ValueError, match="unknown format"):
        load_lists(p)


def test_repo_lists_config_loads() -> None:
    lists = load_lists(Path(__file__).resolve().parents[2] / "config" / "lists.yaml")
    assert {entry.format for entry in lists} == {"simplify_json", "markdown_table"}
