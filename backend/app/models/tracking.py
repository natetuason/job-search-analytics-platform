from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, in_check
from app.models.enums import JOB_STATUSES


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), unique=True)
    # File name inside data/private/resumes/ — the file itself never goes in the DB.
    filename: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobStatus(TimestampMixin, Base):
    """My tracking state for a job. job_id is the primary key: one record per posting."""

    __tablename__ = "job_status"
    __table_args__ = (
        CheckConstraint(in_check("status", JOB_STATUSES), name="status"),
        CheckConstraint("status <> 'applied' OR applied_at IS NOT NULL", name="applied_has_date"),
    )

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), server_default="new", index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class StatusEvent(Base):
    __tablename__ = "status_events"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR {in_check('from_status', JOB_STATUSES)}", name="from_status"
        ),
        CheckConstraint(in_check("to_status", JOB_STATUSES), name="to_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)
    # Why I skipped (SKIP_REASONS), chosen in the skip popup; feeds the suggestions on the Dashboard.
    reasons: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
