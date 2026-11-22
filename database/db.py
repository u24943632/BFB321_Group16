"""MySQL connection handling for the Flask app.

* :func:`get_db` opens one connection per request (stored on ``flask.g``)
  and :func:`close_db` closes it when the request ends, so connections are
  never leaked.
* Settings come from :class:`config.Config`, i.e. from ``.env``; no host,
  user or password is written in code.
* Every connection uses UTC as its session time zone, matching how the
  application stores timestamps.

CLI commands (registered by :func:`init_app`)::

    flask --app app init-db            # create database + tables + seed data
    flask --app app init-db --no-seed  # tables only
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import click
import pymysql
from flask import Flask, current_app, g
from pymysql.cursors import DictCursor

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,64}$")
log = logging.getLogger(__name__)


def connect(settings: dict, *, with_database: bool = True) -> pymysql.connections.Connection:
    """Open a new MySQL connection from a settings mapping (Flask config or Config attrs).

    ``with_database=False`` connects to the server only, used to create the
    database itself before it exists.
    """
    return pymysql.connect(
        host=settings["DB_HOST"],
        port=int(settings["DB_PORT"]),
        user=settings["DB_USER"],
        password=settings["DB_PASSWORD"],
        database=settings["DB_NAME"] if with_database else None,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        init_command="SET time_zone = '+00:00'",
    )


def get_db() -> pymysql.connections.Connection:
    """Return this request's connection, opening it on first use."""
    if "db" not in g:
        g.db = connect(current_app.config)
    return g.db


def rollback_db() -> None:
    """Roll back this request's open transaction, if any (used by the error handlers)."""
    conn = g.get("db")
    if conn is None:
        return
    try:
        conn.rollback()
    except pymysql.MySQLError:
        log.warning("Rollback failed; the connection is closed at teardown.", exc_info=True)


def close_db(error: BaseException | None = None) -> None:
    """Close the request's connection (registered as an app-context teardown).

    If the request ended with an error, anything not yet committed is rolled
    back first, so a half-finished write is never left behind.
    """
    conn = g.pop("db", None)
    if conn is None:
        return
    try:
        if error is not None:
            conn.rollback()
    except pymysql.MySQLError:
        log.warning("Rollback at teardown failed.", exc_info=True)
    finally:
        conn.close()


def create_database(settings: dict) -> None:
    """Create the configured database if it does not exist yet.

    A database name cannot be passed as a query parameter, so it is checked
    against a strict pattern before being placed in the statement.
    """
    name = settings["DB_NAME"]
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(f"DB_NAME '{name}' may only contain letters, digits and underscores.")
    conn = connect(settings, with_database=False)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        conn.commit()
    finally:
        conn.close()


def split_sql_statements(sql: str) -> list[str]:
    """Split a .sql script into statements (drops ``--`` comment lines).

    Good enough for schema.sql, which has no semicolons inside strings.
    """
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def run_schema(conn: pymysql.connections.Connection, path: Path = SCHEMA_FILE) -> None:
    """Drop and recreate every table from schema.sql."""
    with conn.cursor() as cur:
        for statement in split_sql_statements(path.read_text(encoding="utf-8")):
            cur.execute(statement)
    conn.commit()


@click.command("init-db")
@click.option("--seed/--no-seed", default=True, help="Load realistic demo data (default: yes).")
def init_db_command(seed: bool) -> None:
    """Create the database and tables from schema.sql, then load the seed data."""
    from database.seed import seed_database  # imported here to avoid a circular import

    settings = current_app.config
    create_database(settings)
    conn = connect(settings)
    try:
        run_schema(conn)
        click.echo(f"Created tables in '{settings['DB_NAME']}' from {SCHEMA_FILE.name}.")
        if seed:
            summary = seed_database(conn)
            click.echo("Seeded: " + ", ".join(f"{n} {table}" for table, n in summary.items()) + ".")
    finally:
        conn.close()


def init_app(app: Flask) -> None:
    """Register the teardown hook and CLI commands on the Flask app."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
