import httpx
import pytest

from app.sources.google_sheets import GoogleSheetSource, detect_columns, parse_csv, parse_date, parse_sheet_url
from app.sources.http import HttpClient, SourceError

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

CSV = """New Grad Analyst Roles 2027,,,,,
,,,,,
Company,Role,Location,Application Link,Date Posted,Salary
Acme Inc.,Data Analyst,"New York, NY",https://boards.greenhouse.io/acme/jobs/1?gh_src=sheet,10/20/2026,$100k-$120k
↳,Business Analyst,"Jersey City, NJ",https://jobs.lever.co/acme/aaaa-1111-bbbb,2026-10-21,
Beta Corp,Supply Chain Analyst,Remote,Apply,Oct 22 2026,
Gamma,Strategy Analyst,"Boston, MA",see https://gamma.example.com/careers/123 (new),,
,,,,,
"""


def test_parse_sheet_url() -> None:
    assert parse_sheet_url(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=42") == (SHEET_ID, "42")
    assert parse_sheet_url(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit") == (SHEET_ID, "0")
    with pytest.raises(SourceError):
        parse_sheet_url("https://example.com/sheet")


def test_detect_columns_synonyms_and_overrides() -> None:
    headers = ["Employer", "Position", "Where", "URL"]
    assert detect_columns(headers) == {"company": 0, "title": 1, "link": 3}
    assert detect_columns(headers, {"location": "Where"})["location"] == 2
    with pytest.raises(SourceError, match="not found"):
        detect_columns(headers, {"location": "Nope"})
    with pytest.raises(SourceError, match="could not find"):
        detect_columns(["Foo", "Bar"])


def test_parse_date_formats() -> None:
    assert parse_date("10/20/2026").isoformat() == "2026-10-20"
    assert parse_date("Oct 22 2026").isoformat() == "2026-10-22"
    assert parse_date("soon") is None


def test_parse_csv_rows() -> None:
    skipped: list[int] = []
    jobs = parse_csv(CSV, SHEET_ID, "0", skipped=skipped)
    assert [j.title for j in jobs] == ["Data Analyst", "Business Analyst", "Strategy Analyst"]
    first, second, third = jobs
    assert first.company_name == "Acme Inc."
    assert second.company_name == "Acme Inc."  # "↳" means same company as above
    assert first.location_raw == "New York, NY"
    assert first.posted_at.date().isoformat() == "2026-10-20"
    assert first.description_text == "Compensation (from sheet): $100k-$120k"
    assert first.external_id.startswith(f"{SHEET_ID}:0:")
    assert third.apply_url == "https://gamma.example.com/careers/123"  # URL pulled out of surrounding text
    assert skipped == [6]  # "Apply" text with no URL (hyperlink lost in CSV export)


def test_external_id_stable_across_tracking_params() -> None:
    a = parse_csv("Company,Role,Link\nAcme,DA,https://x.com/j/1?utm_source=a\n", SHEET_ID, "0")[0]
    b = parse_csv("Company,Role,Link\nAcme,DA,https://x.com/j/1?utm_source=b\n", SHEET_ID, "0")[0]
    assert a.external_id == b.external_id


def _client(body: str, content_type: str, seen: list | None = None) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return HttpClient(GoogleSheetSource.policy, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_uses_csv_export() -> None:
    seen: list[httpx.Request] = []
    source = GoogleSheetSource(_client(CSV, "text/csv; charset=utf-8", seen))
    jobs = source.fetch(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=7")
    assert str(seen[0].url) == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=7"
    assert len(jobs) == 3
    assert source.skipped_rows == [6]


def test_private_sheet_is_reported_not_bypassed() -> None:
    source = GoogleSheetSource(_client("<!DOCTYPE html><html>Sign in</html>", "text/html"))
    with pytest.raises(SourceError, match="not public"):
        source.fetch(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
