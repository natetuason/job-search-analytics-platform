from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_check
from app.models.enums import RUN_STATUSES, SOURCE_KINDS


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(in_check("source_kind", SOURCE_KINDS), name="source_kind"),
        CheckConstraint(in_check("status", RUN_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(20))
    # e.g. a board token or sheet name; never personal data.
    source_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Requests sent during the run, for sources with a paid call quota.
    api_calls: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
