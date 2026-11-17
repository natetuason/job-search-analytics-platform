from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app


def _client_for(engine: Engine) -> TestClient:
    def override() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_health_ok(engine: Engine) -> None:
    try:
        resp = _client_for(engine).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_db() -> None:
    dead = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    try:
        resp = _client_for(dead).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["database"] == "unreachable"
