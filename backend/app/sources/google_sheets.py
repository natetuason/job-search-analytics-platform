"""Public (or "anyone with the link") Google Sheets, read through the CSV export URL.

No credentials are used. A private sheet returns Google's sign-in page, which we
detect and report instead of trying to get around it.
"""

import csv
import hashlib
import io
import re
from datetime import UTC, date, datetime
from typing import Any

from app.ingestion.urls import canonicalize_url
from app.sources.base import RawJob, Source
from app.sources.http import AccessPolicy, HttpClient, SourceError

_SHEET_URL = re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_GID = re.compile(r"[#&?]gid=(\d+)")
_URL_IN_TEXT = re.compile(r"https?://[^\s,;\"'<>]+")
_SAME_AS_ABOVE = {"", "↳", "^", '"', "same", "ditto"}

# Header names commonly used for each field (compared after lowercasing/stripping).
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "company": ("company", "company name", "employer", "organization", "firm"),
    "title": ("title", "job title", "role", "position", "job", "role title", "position title"),
    "location": ("location", "locations", "city", "office", "office location"),
    "link": ("link", "url", "apply", "apply link", "application link", "application", "job link",
             "posting", "posting link", "link to apply", "careers link", "apply url", "application url",
             "job url"),
    "posted": ("date posted", "posted", "date", "posting date", "date added", "added"),
    "salary": ("salary", "pay", "compensation", "comp", "base", "base salary", "salary range"),
    "deadline": ("deadline", "closing date", "apply by", "application deadline"),
    "notes": ("notes", "description", "details", "comments"),
    "type": ("type", "job type", "employment type"),
}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%d-%b-%Y")


def parse_sheet_url(url: str) -> tuple[str, str]:
    m = _SHEET_URL.search(url)
    if not m:
        raise SourceError("google_sheet: not a Google Sheets URL")
    gid = _GID.search(url)
    return m.group(1), gid.group(1) if gid else "0"


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z ]", "", h.lower()).strip()


def detect_columns(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, int]:
    """Map field -> column index, using explicit overrides first, then synonyms."""
    normalized = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for field, header in (overrides or {}).items():
        target = _norm_header(header)
        if target not in normalized:
            raise SourceError(f"google_sheet: column {header!r} not found in sheet headers")
        mapping[field] = normalized.index(target)
    for field, names in HEADER_SYNONYMS.items():
        if field in mapping:
            continue
        for i, h in enumerate(normalized):
            if h in names and i not in mapping.values():
                mapping[field] = i
                break
    missing = {"company", "title"} - set(mapping)
    if missing:
        raise SourceError(f"google_sheet: could not find column(s) {sorted(missing)}; add a column mapping")
    return mapping


def parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class GoogleSheetSource(Source):
    kind = "google_sheet"
    authoritative = False  # sheets go stale; never expire jobs because a row disappeared
    policy = AccessPolicy(
        name="google_sheet",
        min_interval_s=2.0,
        terms="Only sheets that are public or shared with me; read via CSV export, no auth.",
    )

    def __init__(self, client: HttpClient | None = None, columns: dict[str, str] | None = None):
        self.client = client or HttpClient(self.policy)
        self.columns = columns
        self.skipped_rows: list[int] = []

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        sheet_id, gid = parse_sheet_url(ref)
        body, content_type = self.client.get_text(
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export", params={"format": "csv", "gid": gid}
        )
        if "text/html" in content_type or body.lstrip().lower().startswith(("<!doctype html", "<html")):
            raise SourceError("google_sheet: sheet is not public (got a sign-in page); share it as 'anyone with the link'")
        self.skipped_rows = []
        return parse_csv(body, sheet_id, gid, self.columns, self.skipped_rows)


def parse_csv(
    body: str, sheet_id: str, gid: str, columns: dict[str, str] | None = None, skipped: list[int] | None = None,
    source_kind: str = "google_sheet", source_url: str | None = None,
) -> list[RawJob]:
    """Rows with a company and title but no URL are reported in `skipped` (by sheet row number).

    Also used for CSV files I downloaded myself (source_kind="csv_file"); then
    `sheet_id` is the file's label and `source_url` is None.
    """
    rows = list(csv.reader(io.StringIO(body)))
    # Skip leading blank/title rows: the header is the first row that maps company + title.
    for start, headers in enumerate(rows[:10]):
        try:
            mapping = detect_columns(headers, columns)
            break
        except SourceError:
            continue
    else:
        mapping = detect_columns(rows[0] if rows else [], columns)  # raises with a clear message

    jobs: list[RawJob] = []
    last_company = ""
    for row_number, row in enumerate(rows[start + 1:], start=start + 2):
        cell = lambda f: row[mapping[f]].strip() if f in mapping and mapping[f] < len(row) else ""  # noqa: E731
        company = cell("company")
        if company in _SAME_AS_ABOVE:
            company = last_company
        else:
            last_company = company
        title = cell("title")
        if not company or not title:
            continue
        link = _first_url(cell("link")) or next((u for c in row if (u := _first_url(c))), None)
        if not link:
            # CSV export drops hyperlinks hidden behind text, so these rows can't be imported.
            if skipped is not None:
                skipped.append(row_number)
            continue
        posted = parse_date(cell("posted"))
        notes = [f"Compensation (from sheet): {cell('salary')}" if cell("salary") else "", cell("notes")]
        raw_row: dict[str, Any] = {"row": row_number, "cells": row}
        jobs.append(RawJob(
            source_kind=source_kind,
            external_id=f"{sheet_id}:{gid}:{hashlib.sha1(canonicalize_url(link).encode()).hexdigest()[:16]}",
            company_name=company,
            title=title,
            apply_url=link,
            source_url=(f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"
                        if source_kind == "google_sheet" else source_url),
            location_raw=cell("location") or None,
            description_text="\n\n".join(n for n in notes if n) or None,
            posted_at=datetime(posted.year, posted.month, posted.day, tzinfo=UTC) if posted else None,
            deadline=parse_date(cell("deadline")),
            employment_type_hint=cell("type") or None,
            raw=raw_row,
        ))
    return jobs


def _first_url(text: str) -> str | None:
    m = _URL_IN_TEXT.search(text)
    return m.group(0).rstrip(").]") if m else None
