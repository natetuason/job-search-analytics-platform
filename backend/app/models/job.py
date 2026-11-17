from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, in_check
from app.models.enums import (
    ATS_TYPES,
    CONFIDENCE_LEVELS,
    EMPLOYMENT_TYPES,
    PAY_PERIODS,
    SOURCE_KINDS,
    WORK_ARRANGEMENTS,
)

Money = Numeric(12, 2)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(in_check("ats_type", ATS_TYPES), name="ats_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True)
    ats_type: Mapped[str | None] = mapped_column(String(20))
    ats_token: Mapped[str | None] = mapped_column(String(255))
    careers_url: Mapped[str | None] = mapped_column(Text)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(in_check("work_arrangement", WORK_ARRANGEMENTS), name="work_arrangement"),
        CheckConstraint(in_check("employment_type", EMPLOYMENT_TYPES), name="employment_type"),
        CheckConstraint(in_check("pay_period", PAY_PERIODS), name="pay_period"),
        CheckConstraint(in_check("est_pay_period", PAY_PERIODS), name="est_pay_period"),
        CheckConstraint(in_check("est_confidence", CONFIDENCE_LEVELS), name="est_confidence"),
        CheckConstraint("base_min IS NULL OR base_max IS NULL OR base_min <= base_max", name="base_range"),
        CheckConstraint(
            "est_base_min IS NULL OR est_base_max IS NULL OR est_base_min <= est_base_max",
            name="est_base_range",
        ),
        # An estimate must always say how confident it is.
        CheckConstraint(
            "(est_base_min IS NULL AND est_base_max IS NULL) OR est_confidence IS NOT NULL",
            name="estimate_has_confidence",
        ),
        CheckConstraint("exp_min_years IS NULL OR exp_min_years >= 0", name="exp_min_nonneg"),
        Index("ix_jobs_active_posted", "is_active", "posted_at"),
        Index("ix_jobs_canonical_apply_url", "canonical_apply_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)

    title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str] = mapped_column(String(500))
    location_raw: Mapped[str | None] = mapped_column(Text)
    # [{"city": ..., "state": ..., "country": ...}, ...]
    locations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    work_arrangement: Mapped[str] = mapped_column(String(10), server_default="unknown")
    employment_type: Mapped[str] = mapped_column(String(20), server_default="unknown")

    description_text: Mapped[str | None] = mapped_column(Text)
    required_quals: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    preferred_quals: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    exp_min_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    exp_max_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    exp_text: Mapped[str | None] = mapped_column(Text)
    seniority_signal: Mapped[str | None] = mapped_column(String(50))
    is_early_career_program: Mapped[bool] = mapped_column(Boolean, server_default="false")

    # Compensation as stated in the posting. Never filled from an estimate.
    base_min: Mapped[Decimal | None] = mapped_column(Money)
    base_max: Mapped[Decimal | None] = mapped_column(Money)
    pay_period: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str | None] = mapped_column(String(3))
    signing_bonus: Mapped[Decimal | None] = mapped_column(Money)
    bonus_text: Mapped[str | None] = mapped_column(Text)
    equity_text: Mapped[str | None] = mapped_column(Text)
    total_comp_min: Mapped[Decimal | None] = mapped_column(Money)
    total_comp_max: Mapped[Decimal | None] = mapped_column(Money)
    comp_parse_source: Mapped[str | None] = mapped_column(String(50))

    # Estimated base pay, kept in separate columns so it can't be mistaken for confirmed pay.
    est_base_min: Mapped[Decimal | None] = mapped_column(Money)
    est_base_max: Mapped[Decimal | None] = mapped_column(Money)
    est_pay_period: Mapped[str | None] = mapped_column(String(10))
    est_confidence: Mapped[str | None] = mapped_column(String(10))
    est_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    apply_url: Mapped[str] = mapped_column(Text)
    canonical_apply_url: Mapped[str] = mapped_column(Text)
    # sha256 of normalized company + title + location; see ingestion/dedup (Phase 2).
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)

    company: Mapped[Company] = relationship(back_populates="jobs")
    sources: Mapped[list["JobSource"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobSource(Base):
    """Where a job was seen. One job can be found by several sources."""

    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("source_kind", "external_id", name="uq_job_sources_kind_external_id"),
        CheckConstraint(in_check("source_kind", SOURCE_KINDS), name="source_kind"),
        Index("ix_job_sources_kind_ref", "source_kind", "source_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_kind: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(255))
    # The board token / sheet / feed this was fetched from.
    source_ref: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[Job] = relationship(back_populates="sources")
