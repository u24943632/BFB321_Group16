"""Central error handling for the whole app.

Every failure ends here, so no route builds an error response itself:

* API requests (``/api/...``) get ``{"ok": false, "error": "...", "status": n}``.
* Page requests get a friendly HTML page (templates/error.html).
* 4xx errors are logged as warnings; 5xx errors are logged with their stack
  trace (server side only - the user never sees it).
* Any open database transaction is rolled back before responding.

Status codes:
    400 bad input / rule-breaking request     403 role not allowed
    404 unknown box, incident, product, URL    405 wrong HTTP method
    409 conflict (illegal stage move, duplicate SKU or category)
    503 database unreachable                   500 anything unexpected
"""

from __future__ import annotations

import logging

import pymysql
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from database.db import rollback_db
from models.products import UnknownProductError
from models.tracking import (
    InvalidTransitionError, PermissionDeniedError, TrackingError, UnknownBoxError,
)
from services.errors import ApiError

log = logging.getLogger(__name__)


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def _respond(status: int, message: str, details: dict | None = None):
    """Log, roll back, and return the JSON or HTML error response."""
    rollback_db()
    if status >= 500:
        log.error("%s %s -> %s: %s", request.method, request.path, status, message)
    else:
        log.warning("%s %s -> %s: %s", request.method, request.path, status, message)
    if _wants_json():
        body = {"ok": False, "error": message, "status": status}
        if details:
            body["details"] = details
        return jsonify(body), status
    return render_template("error.html", status=status, message=message), status


def tracking_error_status(error: TrackingError) -> int:
    """HTTP status for a process-rule violation from models.tracking."""
    if isinstance(error, UnknownBoxError):
        return 404
    if isinstance(error, PermissionDeniedError):
        return 403
    if isinstance(error, InvalidTransitionError):
        return 409
    return 400  # validation, malformed box ID, unknown user or location


def register_error_handlers(app: Flask) -> None:
    """Attach the handlers to the app (called from app.create_app)."""

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return _respond(error.status, error.message, error.details)

    @app.errorhandler(TrackingError)
    def handle_rule_error(error: TrackingError):
        message = error.args[0] if error.args else str(error)
        return _respond(tracking_error_status(error), message)

    @app.errorhandler(UnknownProductError)
    def handle_unknown_product(error: UnknownProductError):
        return _respond(400, error.args[0] if error.args else "Unknown product.")

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        messages = {
            404: "That page or resource does not exist.",
            405: f"{request.method} is not allowed on {request.path}.",
        }
        return _respond(error.code or 500, messages.get(error.code, error.description))

    @app.errorhandler(pymysql.err.IntegrityError)
    def handle_integrity_error(error: pymysql.err.IntegrityError):
        log.warning("Integrity error on %s %s: %s", request.method, request.path, error)
        return _respond(409, "The change conflicts with existing data "
                             "(for example a duplicate code or a missing reference).")

    @app.errorhandler(pymysql.err.OperationalError)
    @app.errorhandler(pymysql.err.InterfaceError)
    def handle_database_down(error: pymysql.MySQLError):
        log.error("Database unavailable on %s %s: %s", request.method, request.path, error)
        return _respond(503, "The database is not reachable. Check that MySQL is running "
                             "and that the settings in .env are correct.")

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return _respond(500, "Something went wrong on the server. The error has been logged.")
