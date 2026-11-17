from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from app.models import Base
from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "companies",
    "jobs",
    "job_sources",
    "job_scores",
    "job_status",
    "status_events",
    "resume_versions",
    "user_settings",
    "candidate_profile",
    "ingestion_runs",
}


def test_all_tables_created(engine: Engine) -> None:
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_models_match_migrations(engine: Engine) -> None:
    """Fails if a model was changed without generating a migration."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_round_trip(test_db_url: str, engine: Engine) -> None:
    cfg = alembic_config(test_db_url)
    command.downgrade(cfg, "base")
    assert not (EXPECTED_TABLES & set(inspect(engine).get_table_names()))
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
