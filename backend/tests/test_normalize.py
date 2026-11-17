import pytest

from app.ingestion.normalize import (
    dedup_key,
    detect_employment_type,
    detect_work_arrangement,
    html_to_text,
    location_key,
    normalize_company,
    normalize_title,
    parse_locations,
)
from app.ingestion.urls import canonicalize_url


@pytest.mark.parametrize("raw,expected", [
    ("Acme Inc.", "acme"),
    ("Acme, Inc", "acme"),
    ("ACME Corporation", "acme"),
    ("The Home Depot", "home depot"),
    ("Procter & Gamble Co.", "procter and gamble"),
    ("Nestlé S.A.", "nestle"),
    ("Coinbase", "coinbase"),
])
def test_normalize_company(raw: str, expected: str) -> None:
    assert normalize_company(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Sr. Data Analyst", "senior data analyst"),
    ("Strategy & Ops Analyst", "strategy and operations analyst"),
    ("  Business   Analyst, II ", "business analyst ii"),
])
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_html_to_text_handles_escaped_html_and_lists() -> None:
    text = html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;")
    assert text == "Hello & welcome\n\n- SQL\n\n- Python"


def test_html_to_text_drops_scripts() -> None:
    assert html_to_text("<p>Hi</p><script>alert(1)</script>") == "Hi"
    assert html_to_text(None) == ""


@pytest.mark.parametrize("raw,city,state,country,remote", [
    ("New York, NY", "New York", "NY", "US", False),
    ("Chicago, Illinois, United States", "Chicago", "IL", "US", False),
    ("Remote - US", None, None, "US", True),
    ("Remote", None, None, None, True),
    ("United States", None, None, "US", False),
    ("London, United Kingdom", "London", None, "United Kingdom", False),
])
def test_parse_locations(raw: str, city, state, country, remote) -> None:
    loc = parse_locations(raw)[0]
    assert (loc["city"], loc["state"], loc["country"], loc["remote"]) == (city, state, country, remote)


def test_parse_multiple_locations_and_country_hint() -> None:
    locs = parse_locations("New York, NY; San Francisco, CA")
    assert [loc["state"] for loc in locs] == ["NY", "CA"]
    assert parse_locations("London", country_hint="GB")[0]["country"] == "GB"
    assert parse_locations("") == []


def test_location_key_equates_spellings() -> None:
    a = location_key(parse_locations("New York, NY"), "New York, NY")
    b = location_key(parse_locations("New York, New York, United States"), "x")
    assert a == b == "new york ny us"


@pytest.mark.parametrize("hint,loc,title,desc,expected", [
    ("hybrid", None, "Analyst", "", "hybrid"),
    ("unspecified", "Remote - US", "Analyst", "", "remote"),
    (None, "New York, NY", "Analyst (Hybrid)", "", "hybrid"),
    (None, "New York, NY", "Analyst", "This role is hybrid, 3 days in office.", "hybrid"),
    (None, "New York, NY", "Analyst", "Our remote-first culture", "remote"),
    (None, "New York, NY", "Analyst", "Some remote work possible", "unknown"),
])
def test_detect_work_arrangement(hint, loc, title, desc, expected) -> None:
    assert detect_work_arrangement(hint, loc, title, desc) == expected


@pytest.mark.parametrize("hint,title,expected", [
    ("Full-time", "Data Analyst", "full_time"),
    ("Internship", "Business Analyst", "internship"),
    (None, "Supply Chain Analyst Intern", "internship"),
    (None, "Data Analyst (Contract)", "contract"),
    (None, "Data Analyst", "unknown"),
    (None, "International Business Analyst", "unknown"),
])
def test_detect_employment_type(hint, title, expected) -> None:
    assert detect_employment_type(hint, title) == expected


def test_dedup_key_ignores_formatting_differences() -> None:
    loc = "new york ny us"
    assert dedup_key("Acme Inc.", "Sr. Data Analyst", loc) == dedup_key("ACME", "Senior Data Analyst", loc)
    assert dedup_key("Acme", "Data Analyst", loc) != dedup_key("Acme", "Data Analyst II", loc)
    assert dedup_key("Acme", "Data Analyst", loc) != dedup_key("Acme", "Data Analyst", "chicago il us")


@pytest.mark.parametrize("raw,expected", [
    ("https://boards.greenhouse.io/acme/jobs/1?gh_src=abc", "https://boards.greenhouse.io/acme/jobs/1"),
    ("https://job-boards.greenhouse.io/acme/jobs/1/", "https://boards.greenhouse.io/acme/jobs/1"),
    ("https://boards.greenhouse.io/embed/job_app?for=Acme&token=1", "https://boards.greenhouse.io/acme/jobs/1"),
    ("https://jobs.lever.co/acme/abc/apply?lever-source=LinkedIn", "https://jobs.lever.co/acme/abc"),
    ("HTTP://WWW.Careers.Acme.com/pos/7?utm_source=x&gh_jid=7#top", "https://careers.acme.com/pos/7?gh_jid=7"),
    ("https://x.com/a?b=2&a=1", "https://x.com/a?a=1&b=2"),
])
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected
