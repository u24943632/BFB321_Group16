"""Application settings per environment, read from environment variables / .env.

Nothing secret is hard-coded: copy ``.env.example`` to ``.env`` and fill in
your own MySQL credentials. Real environment variables override ``.env``.

Choose the environment with ``APP_ENV`` (default ``development``)::

    development   debug logging, local MySQL database from .env
    testing       separate ``<DB_NAME>_test`` database, used by the test suite
    production    no debug, INFO logging, refuses to start without a real SECRET_KEY
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_SECRET = "dev-only-change-me"


class Config:
    """Settings shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET)
    DEBUG = False
    TESTING = False

    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "simply_garlic")

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # List endpoints: ?page=&per_page= (per_page is capped at API_MAX_PAGE_SIZE).
    API_DEFAULT_PAGE_SIZE = 20
    API_MAX_PAGE_SIZE = 100


class DevelopmentConfig(Config):
    DEBUG = True
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")


class TestingConfig(Config):
    TESTING = True
    DB_NAME = f"{Config.DB_NAME}_test"  # never touch the real data
    LOG_LEVEL = "ERROR"  # keep test output readable; tests that check logging capture it


class ProductionConfig(Config):
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> None:
        if cls.SECRET_KEY == DEFAULT_SECRET:
            raise RuntimeError("Set a real SECRET_KEY in the environment for production.")


CONFIGS: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    """The config class for ``name`` (or the APP_ENV variable, default development)."""
    key = (name or os.environ.get("APP_ENV") or "development").strip().lower()
    try:
        return CONFIGS[key]
    except KeyError:
        raise RuntimeError(f"Unknown APP_ENV '{key}'. Use one of: {', '.join(CONFIGS)}.") from None
