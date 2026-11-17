"""Community job lists published as files in public GitHub repos.

Only raw files on GitHub are fetched (raw.githubusercontent.com), never the list
owners' own websites, several of which prohibit automated access in their terms.

Two formats:
  simplify_json   SimplifyJobs-style listings.json (structured; links go to the employer)
  markdown_table  a README with a Markdown table, e.g. jobright-ai's lists (links go to
                  the list owner's job page, not the employer)
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

from app.ingestion.urls import canonicalize_url
from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

FORMATS = ("simplify_json", "markdown_table")
_ALLOWED_HOSTS = {"raw.githubusercontent.com"}
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_SAME_AS_ABOVE = {"", "↳", "^"}
_WORK_MODELS = {"on site": "onsite", "onsite": "onsite", "hybrid": "hybrid", "remote": "remote"}
_MONTH_DAY = ("%b %d", "%B %d")


class GitHubListSource(Source):
    kind = "github_list"
    authoritative = False  # a list dropping a row says nothing reliable about the employer's posting
    policy = AccessPolicy(
        name="github_list",
        min_interval_s=2.0,
        terms="Public files in public GitHub repos, fetched from raw.githubusercontent.com only.",
    )

    def __init__(self, fmt: str, categories: list[str] | None = None, client: HttpClient | None = None):
        if fmt not in FORMATS:
            raise ValueError(f"unknown list format {fmt!r}; expected one of {FORMATS}")
        self.fmt = fmt
        self.categories = categories
        self.client = client or HttpClient(self.policy)

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        if urlparse(ref).hostname not in _ALLOWED_HOSTS:
            raise SourceError("github_list: url must be a raw.githubusercontent.com file")
        body, _ = self.client.get_text(ref)
        if self.fmt == "simplify_json":
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise SourceError(f"github_list: not valid JSON ({exc})") from exc
            return parse_simplify_json(data, ref, self.categories)
        return parse_markdown_table(body, ref)


def _repo(url: str) -> str:
    """'SimplifyJobs/New-Grad-Positions' from a raw.githubusercontent.com URL."""
    parts = urlparse(url).path.strip("/").split("/")
    return "/".join(parts[:2])


def _link_hash(url: str) -> str:
    return hashlib.sha1(canonicalize_url(url).encode()).hexdigest()[:16]


def parse_simplify_json(data: Any, url: str, categories: list[str] | None = None) -> list[RawJob]:
    if not isinstance(data, list):
        raise SourceError("github_list: expected a JSON list of listings")
    repo = _repo(url)
    wanted = {c.lower() for c in categories} if categories else None
    jobs: list[RawJob] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("active") or not item.get("is_visible", True):
            continue
        if wanted is not None and str(item.get("category", "")).lower() not in wanted:
            continue
        company, title, link = (str(item.get(k) or "").strip() for k in ("company_name", "title", "url"))
        if not (company and title and link.startswith("http")):
            continue
        notes = []
        if item.get("sponsorship") and item["sponsorship"] != "Other":
            notes.append(f"Sponsorship (from list): {item['sponsorship']}")
        if item.get("degrees"):
            notes.append(f"Degrees (from list): {', '.join(item['degrees'])}")
        posted = item.get("date_posted")
        jobs.append(RawJob(
            source_kind="github_list",
            external_id=f"{repo}:{item.get('id') or _link_hash(link)}",
            company_name=company,
            title=title,
            apply_url=link,
            source_url=f"https://github.com/{repo}",
            location_raw="; ".join(item.get("locations") or []) or None,
            description_text="\n".join(notes) or None,
            posted_at=datetime.fromtimestamp(posted, UTC) if isinstance(posted, int | float) else None,
            employment_type_hint="full-time",  # these are new-grad full-time lists
            raw=item,
        ))
    return jobs


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _link(cell: str) -> tuple[str, str | None]:
    """('text', url) from a '**[text](url)**' cell, or (plain text, None)."""
    m = _MD_LINK.search(cell)
    if m:
        return m.group(1).strip(), m.group(2)
    return cell.replace("*", "").strip(), None


def parse_month_day(value: str, today: date) -> date | None:
    """'Nov 05' -> the most recent such date on or before today (lists omit the year)."""
    for fmt in _MONTH_DAY:
        try:
            parsed = datetime.strptime(f"{value.strip()} {today.year}", f"{fmt} %Y").date()
        except ValueError:
            continue
        return parsed if parsed <= today else parsed.replace(year=today.year - 1)
    return None


def parse_markdown_table(text: str, url: str, today: date | None = None) -> list[RawJob]:
    today = today or datetime.now(UTC).date()
    repo = _repo(url)
    lines = text.splitlines()
    for start, line in enumerate(lines):
        headers = [h.lower() for h in _cells(line)] if line.lstrip().startswith("|") else []
        if "company" in headers and any("title" in h or h in ("role", "position") for h in headers):
            break
    else:
        raise SourceError("github_list: no Markdown table with Company and Job Title columns")
    col = {h: i for i, h in enumerate(headers)}
    title_col = next(i for i, h in enumerate(headers) if "title" in h or h in ("role", "position"))

    jobs: list[RawJob] = []
    last_company = ""
    for line in lines[start + 2:]:  # skip the |---| separator row
        if not line.lstrip().startswith("|"):
            break
        cells = _cells(line)
        get = lambda name: cells[col[name]] if name in col and col[name] < len(cells) else ""  # noqa: E731
        company, _ = _link(get("company"))
        if company in _SAME_AS_ABOVE:
            company = last_company
        else:
            last_company = company
        title, link = _link(cells[title_col] if title_col < len(cells) else "")
        if not (company and title and link):
            continue
        date_cell = get("date posted") or get("date")
        posted = parse_month_day(date_cell, today) if date_cell else None
        # jobright calls the work model "Work Model"; jobr.pro calls it "Type".
        work_model = (get("work model") or get("type")).lower()
        jobs.append(RawJob(
            source_kind="github_list",
            external_id=f"{repo}:{_link_hash(link)}",
            company_name=company,
            title=title,
            apply_url=link,
            source_url=f"https://github.com/{repo}",
            location_raw=get("location") or None,
            posted_at=datetime(posted.year, posted.month, posted.day, tzinfo=UTC) if posted else None,
            work_arrangement_hint=_WORK_MODELS.get(work_model),
            employment_type_hint="full-time",
            raw={"cells": cells},
        ))
    return jobs
