from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root holds .env; backend/ is one level down.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None
    log_level: str = "INFO"
    config_dir: Path = REPO_ROOT / "config"
    private_dir: Path = REPO_ROOT / "data" / "private"
    # Application backlog (git-ignored): applications.md/.csv and rollup.md.
    backlog_dir: Path = REPO_ROOT / "backlog"
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
