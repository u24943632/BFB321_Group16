"""Application settings, read from environment variables (and a local .env file).

Nothing secret is hard-coded: copy ``.env.example`` to ``.env`` and fill in
your own MySQL credentials. Real environment variables override ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    """Flask and database settings for the running application."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "simply_garlic")

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
