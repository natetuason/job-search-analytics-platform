"""Fetch every configured source: the loaders for config/*.yaml and the batch runners.

Used by the CLI (`ingest ...`) and the scheduler. Runners report one line per source through `out`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import func, select

from app.config import get_settings
from app.ingestion.pipeline import ingest
from app.models import IngestionRun
from app.sources.adzuna import AdzunaSearch, AdzunaSource
from app.sources.ashby import AshbySource
from app.sources.base import Source
from app.sources.github_lists import FORMATS, GitHubListSource
from app.sources.google_sheets import GoogleSheetSource
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import LeverSource

BOARD_SOURCES: dict[str, type[Source]] = {"greenhouse": GreenhouseSource, "lever": LeverSource, "ashby": AshbySource}


@dataclass
class BoardConfig:
    name: str
    ats: str
    token: str


@dataclass
class SheetConfig:
    name: str
    url: str
    columns: dict[str, str] = field(default_factory=dict)


@dataclass
class ListConfig:
    name: str
    format: str
    url: str
    categories: list[str] | None = None


def _load_yaml_list(path: Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    entries = (yaml.safe_load(path.read_text()) or {}).get(key) or []
    if not isinstance(entries, list):
        raise ValueError(f"{path.name}: '{key}' must be a list")
    return entries


def load_companies(path: Path) -> list[BoardConfig]:
    boards = []
    for i, entry in enumerate(_load_yaml_list(path, "companies")):
        missing = {"name", "ats", "token"} - set(entry or {})
        if missing:
            raise ValueError(f"{path.name} entry {i}: missing {sorted(missing)}")
        if entry["ats"] not in BOARD_SOURCES:
            raise ValueError(f"{path.name} entry {i}: unknown ats {entry['ats']!r}")
        boards.append(BoardConfig(str(entry["name"]), entry["ats"], str(entry["token"])))
    return boards


def load_sheets(path: Path) -> list[SheetConfig]:
    sheets = []
    for i, entry in enumerate(_load_yaml_list(path, "sheets")):
        if not entry or "url" not in entry:
            raise ValueError(f"{path.name} entry {i}: missing 'url'")
        sheets.append(SheetConfig(str(entry.get("name") or entry["url"]), entry["url"], entry.get("columns") or {}))
    return sheets


def load_lists(path: Path) -> list[ListConfig]:
    lists = []
    for i, entry in enumerate(_load_yaml_list(path, "lists")):
        missing = {"format", "url"} - set(entry or {})
        if missing:
            raise ValueError(f"{path.name} entry {i}: missing {sorted(missing)}")
        if entry["format"] not in FORMATS:
            raise ValueError(f"{path.name} entry {i}: unknown format {entry['format']!r}")
        lists.append(ListConfig(str(entry.get("name") or entry["url"]), entry["format"], entry["url"],
                                entry.get("categories")))
    return lists


@dataclass
class AdzunaConfig:
    searches: list[AdzunaSearch]
    daily_call_cap: int
    total_call_cap: int | None = None
    results_per_page: int = 50
    max_pages: int = 1
    max_days_old: int = 14


def load_adzuna(path: Path) -> AdzunaConfig:
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    if not data:
        return AdzunaConfig([], 0)
    searches = []
    for i, entry in enumerate(data.get("searches") or []):
        if not entry or not entry.get("what") or not entry.get("where"):
            raise ValueError(f"{path.name} searches[{i}]: needs what and where")
        searches.append(AdzunaSearch(str(entry["what"]), str(entry["where"])))
    if "daily_call_cap" not in data:
        raise ValueError(f"{path.name}: daily_call_cap is required")
    return AdzunaConfig(searches, int(data["daily_call_cap"]), data.get("total_call_cap"),
                        int(data.get("results_per_page", 50)), int(data.get("max_pages", 1)),
                        int(data.get("max_days_old", 14)))


def adzuna_calls_remaining(session, cfg: AdzunaConfig, max_calls: int | None, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    used = lambda *where: session.scalar(  # noqa: E731
        select(func.coalesce(func.sum(IngestionRun.api_calls), 0)).where(IngestionRun.source_kind == "adzuna", *where))
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = cfg.daily_call_cap - used(IngestionRun.started_at >= start_of_day)
    if cfg.total_call_cap is not None:
        remaining = min(remaining, int(cfg.total_call_cap) - used())
    if max_calls is not None:
        remaining = min(remaining, max_calls)
    return max(remaining, 0)


def summary_line(run: IngestionRun, label: str | None = None) -> str:
    where = label or f"{run.source_kind}:{run.source_ref}"
    if run.status == "failed":
        return f"FAILED  {where}  {run.errors[0]['error'] if run.errors else ''}"
    errs = f"  errors={len(run.errors)}" if run.errors else ""
    return f"ok      {where}  fetched={run.fetched_count} new={run.new_count} updated={run.updated_count}{errs}"


def run_boards(session, boards: list[BoardConfig], out: Callable[[str], None] = print) -> int:
    clients = {kind: cls() for kind, cls in BOARD_SOURCES.items()}  # shared per source, so rate limits apply across boards
    failures = 0
    for board in boards:
        run = ingest(session, clients[board.ats], board.token, board.name)
        failures += run.status == "failed"
        out(summary_line(run))
    return failures


def run_sheets(session, sheets: list[SheetConfig], out: Callable[[str], None] = print) -> int:
    failures = 0
    for sheet in sheets:
        source = GoogleSheetSource(columns=sheet.columns or None)
        run = ingest(session, source, sheet.url)
        failures += run.status == "failed"
        line = summary_line(run, f"google_sheet:{sheet.name}")
        if source.skipped_rows:
            line += f"  skipped_no_link={len(source.skipped_rows)} (rows {source.skipped_rows[:5]}{'...' if len(source.skipped_rows) > 5 else ''})"
        out(line)
    return failures


def run_lists(session, lists: list[ListConfig], out: Callable[[str], None] = print) -> int:
    failures = 0
    for entry in lists:
        run = ingest(session, GitHubListSource(entry.format, entry.categories), entry.url)
        failures += run.status == "failed"
        out(summary_line(run, f"github_list:{entry.name}"))
    return failures


def run_adzuna(session, cfg: AdzunaConfig, max_calls: int | None, out: Callable[[str], None] = print) -> int:
    settings = get_settings()
    budget = adzuna_calls_remaining(session, cfg, max_calls)
    out(f"adzuna: {budget} call(s) available this run")
    source = AdzunaSource(settings.adzuna_app_id, settings.adzuna_app_key, {s.ref: s for s in cfg.searches},
                          budget, cfg.results_per_page, cfg.max_pages, cfg.max_days_old)
    failures = 0
    for search in cfg.searches:
        if source.call_budget <= 0:
            out(f"skipped  adzuna:{search.ref}  (call cap reached)")
            continue
        run = ingest(session, source, search.ref)
        failures += run.status == "failed"
        out(summary_line(run) + f"  calls={run.api_calls}")
    return failures
