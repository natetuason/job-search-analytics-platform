"""Test fixtures. Uses a separate database (TEST_DATABASE_URL), never the dev database."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_url() -> str:
    url = get_settings().test_database_url
    if not url:
        pytest.exit("TEST_DATABASE_URL is not set in .env", returncode=2)
    if url == get_settings().database_url:
        pytest.exit("TEST_DATABASE_URL must differ from DATABASE_URL", returncode=2)
    return url


def _recreate_database(url: str) -> None:
    u = make_url(url)
    with psycopg.connect(
        host=u.host, port=u.port, user=u.username, password=u.password, dbname="postgres", autocommit=True
    ) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(u.database)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(u.database)))


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = _test_url()
    _recreate_database(url)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def engine(test_db_url: str) -> Iterator[Engine]:
    eng = create_engine(test_db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back afterwards."""
    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def _no_real_backlog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never write the real backlog/ (my applications) or read the dev database for it."""
    import app.api as api

    monkeypatch.setattr(get_settings(), "backlog_dir", tmp_path / "backlog")
    monkeypatch.setattr(api, "_refresh_backlog", lambda factory: None)
