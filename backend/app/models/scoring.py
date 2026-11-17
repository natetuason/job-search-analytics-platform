from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_check
from app.models.enums import RECOMMENDATIONS, TIERS


class JobScore(Base):
    """Output of one scorer for one job. The tier is a qualitative label, not a probability."""

    __tablename__ = "job_scores"
    __table_args__ = (
        UniqueConstraint("job_id", "scorer", name="uq_job_scores_job_id_scorer"),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint(in_check("tier", TIERS), name="tier"),
        CheckConstraint(in_check("recommendation", RECOMMENDATIONS), name="recommendation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    scorer: Mapped[str] = mapped_column(String(50))  # e.g. "rules-v1", later "claude-v1"
    settings_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tier: Mapped[str] = mapped_column(String(20))
    recommendation: Mapped[str] = mapped_column(String(10))
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hard_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
