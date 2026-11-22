"""Errors that services and routes raise for a request that cannot be served.

Each carries the HTTP status it maps to. :mod:`routes.errors` turns them
into the standard JSON error body, so no route builds error responses itself.
Rule violations from :mod:`models.tracking` (``TrackingError``) are mapped
there as well.
"""

from __future__ import annotations

from typing import Optional


class ApiError(Exception):
    """Base class: a message for the user plus an HTTP status."""

    status = 400

    def __init__(self, message: str, *, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidInputError(ApiError):
    """Missing field, wrong type or value out of range (400)."""

    status = 400


class NotFoundError(ApiError):
    """The resource in the URL does not exist (404)."""

    status = 404


class ConflictError(ApiError):
    """The request clashes with existing data, e.g. a duplicate SKU (409)."""

    status = 409
