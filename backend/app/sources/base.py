"""The interface every job source implements.

A source turns one reference (a board token, a sheet URL, ...) into RawJob records.
It does no database work; the ingestion pipeline handles normalization and storage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from app.sources.http import AccessPolicy


@dataclass
class RawJob:
    source_kind: str
    external_id: str
    company_name: str
    title: str
    apply_url: str
    source_url: str | None = None
    location_raw: str | None = None
    description_html: str | None = None
    description_text: str | None = None
    posted_at: datetime | None = None
    deadline: date | None = None
    # Structured hints straight from the source, when it provides them.
    work_arrangement_hint: str | None = None
    employment_type_hint: str | None = None
    country_hint: str | None = None
    # Compensation only when the source gives it as structured data.
    base_min: Decimal | None = None
    base_max: Decimal | None = None
    pay_period: str | None = None
    currency: str | None = None
    comp_parse_source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    kind: ClassVar[str]
    policy: ClassVar[AccessPolicy]
    # True when a fetch returns the complete listing for the ref, so postings
    # missing from it can be treated as taken down.
    authoritative: ClassVar[bool] = False

    @abstractmethod
    def fetch(self, ref: str, company_name: str | None = None) -> list[RawJob]:
        """Return all postings for `ref`. Raise SourceError on failure."""
