"""Allowed values for enum-like VARCHAR columns (enforced by CHECK constraints)."""

WORK_ARRANGEMENTS = ("remote", "hybrid", "onsite", "unknown")
EMPLOYMENT_TYPES = ("full_time", "part_time", "internship", "contract", "unknown")
PAY_PERIODS = ("year", "hour")
CONFIDENCE_LEVELS = ("low", "medium", "high")
ATS_TYPES = ("greenhouse", "lever", "ashby", "other")
SOURCE_KINDS = ("greenhouse", "lever", "google_sheet", "reddit", "manual", "github_list", "csv_file", "ashby", "adzuna")
TIERS = ("strong_fit", "competitive", "reach")
RECOMMENDATIONS = ("apply", "maybe", "skip")
JOB_STATUSES = (
    "new",
    "shortlisted",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "skipped",
    "expired",
)
# Why I skipped a job (the skip popup).
SKIP_REASONS = ("experience", "pay", "location", "arrangement", "role", "company", "other")
RUN_STATUSES = ("running", "succeeded", "failed")
