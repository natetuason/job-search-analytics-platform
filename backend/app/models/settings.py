import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DEFAULT_WEIGHTS = {
    "skills": 30,
    "experience": 25,
    "responsibilities": 20,
    "compensation": 15,
    "location": 10,
}


class UserSettings(TimestampMixin, Base):
    """Single-row table (id is always 1).

    Facts about me that I haven't confirmed (graduation date, work authorization,
    sponsorship, relocation) are nullable and default to NULL — never guessed.
    """

    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("salary_min >= 0", name="salary_min_nonneg"),
        CheckConstraint(
            "secondary_location_min_score IS NULL OR secondary_location_min_score BETWEEN 0 AND 100",
            name="secondary_min_score_range",
        ),
        CheckConstraint(
            "salary_preferred_min IS NULL OR salary_preferred_max IS NULL "
            "OR salary_preferred_min <= salary_preferred_max",
            name="preferred_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    # Bumped on every change so job_scores can tell when they're stale.
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    salary_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("90000"))
    salary_preferred_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), server_default=text("110000"))
    salary_preferred_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), server_default=text("140000"))

    work_arrangements: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("""'["remote", "hybrid", "onsite"]'::jsonb""")
    )
    preferred_locations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    excluded_locations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Other places that are acceptable only when the job's match score clears a bar.
    secondary_locations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Hidden after skips: jobs at these companies, and titles containing these words, are Skip.
    excluded_companies: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    excluded_title_words: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    secondary_location_min_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    open_to_relocation: Mapped[bool | None] = mapped_column(Boolean)

    target_titles_primary: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    target_titles_secondary: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    experience_max_years: Mapped[Decimal] = mapped_column(Numeric(4, 1), server_default=text("3"))
    scoring_weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text(f"'{json.dumps(DEFAULT_WEIGHTS)}'::jsonb"),
    )

    search_frequency_hours: Mapped[int] = mapped_column(Integer, server_default=text("24"))
    notifications: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    graduation_date: Mapped[date | None] = mapped_column(Date)
    work_authorization: Mapped[str | None] = mapped_column(String(100))
    needs_sponsorship: Mapped[bool | None] = mapped_column(Boolean)


class CandidateProfile(TimestampMixin, Base):
    """Single-row table of my skills and experiences, used only for scoring.

    Seeded from data/private/profile.yaml (git-ignored); editable in Settings.
    """

    __tablename__ = "candidate_profile"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # [{"org": ..., "role": ..., "kind": "internship"|"full_time"|"project", "skills": [...], "highlights": [...]}]
    experiences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
