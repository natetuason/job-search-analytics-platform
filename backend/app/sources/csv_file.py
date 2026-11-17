"""A CSV file I downloaded myself, e.g. SWE Job Lists' "Download CSV" export.

Some sites offer an export but put it behind a CAPTCHA. The app never fetches
those; I download the file in my browser and point the importer at it.
"""

from pathlib import Path

from app.sources.base import RawJob, Source
from app.sources.google_sheets import parse_csv
from app.sources.http import AccessPolicy, SourceError


class CsvFileSource(Source):
    kind = "csv_file"
    authoritative = False
    policy = AccessPolicy(name="csv_file", min_interval_s=0.0, terms="Local files only; no network access.")

    def __init__(self, label: str, columns: dict[str, str] | None = None):
        self.label = label
        self.columns = columns
        self.skipped_rows: list[int] = []

    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        path = Path(ref).expanduser()
        try:
            body = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise SourceError(f"csv_file: can't read {path.name}: {exc}") from exc
        self.skipped_rows = []
        return parse_csv(body, self.label, "file", self.columns, self.skipped_rows, source_kind="csv_file")
