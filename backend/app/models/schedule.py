from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_check
from app.models.enums import RUN_STATUSES

TRIGGERS = ("schedule", "manual")


class ScheduledRun(Base):
    """One scheduled update: fetch everything, close gone listings, rescore. Also the digest of what's new."""

    __tablename__ = "scheduled_runs"
    __table_args__ = (
        CheckConstraint(in_check("status", RUN_STATUSES), name="status"),
        CheckConstraint(in_check("trigger", TRIGGERS), name="trigger"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sources_ok: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sources_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_jobs: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    closed_jobs: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Jobs first found in this run that scored Apply or Maybe: the digest.
    new_recommended_ids: Mapped[list[int]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Which of my tracked jobs (shortlisted/applied/...) had their listing close in this run.
    closed_tracked_ids: Mapped[list[int]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(String(1000))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
