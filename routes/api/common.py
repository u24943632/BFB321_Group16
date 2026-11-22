"""Request parsing, validation and pagination shared by the API blueprints.

Every helper raises :class:`services.errors.InvalidInputError` with a message
that names the field and says what is wrong; :mod:`routes.errors` returns it
as ``400 {"ok": false, "error": ...}``.

Response shapes used by every endpoint::

    one resource:  {"ok": true, "<resource>": {...}}
    a list:        {"ok": true, "items": [...], "pagination": {...}, "filters": {...}}
    an error:      {"ok": false, "error": "...", "status": 400}
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from flask import current_app, request

from services.errors import InvalidInputError

MAX_TEXT_LENGTH = 500  # matches the VARCHAR(500) note / resolution columns


# ---------------------------------------------------------------------------
# JSON body
# ---------------------------------------------------------------------------

def json_body(allowed: Optional[Iterable[str]] = None) -> dict:
    """The request's JSON object. With ``allowed``, unknown keys are refused."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise InvalidInputError("Send a JSON object in the request body "
                                "with the header Content-Type: application/json.")
    if allowed is not None:
        unknown = sorted(set(data) - set(allowed) - {"username"})
        if unknown:
            raise InvalidInputError(f"Unknown field(s): {', '.join(unknown)}. "
                                    f"Allowed: {', '.join(sorted(allowed))}.")
    return data


def text_field(data: dict, key: str, *, required: bool = True,
               max_length: int = MAX_TEXT_LENGTH, min_length: int = 0) -> str:
    """A trimmed string field ('' if optional and missing)."""
    value = data.get(key, "")
    if value is None and not required:
        value = ""
    if not isinstance(value, str):
        raise InvalidInputError(f"'{key}' must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"'{key}' is required.")
    if value and len(value) < min_length:
        raise InvalidInputError(f"'{key}' must be at least {min_length} characters.")
    if len(value) > max_length:
        raise InvalidInputError(f"'{key}' may be at most {max_length} characters.")
    return value


def int_field(data: dict, key: str, *, minimum: Optional[int] = None,
              maximum: Optional[int] = None) -> int:
    """A required whole-number field within [minimum, maximum]."""
    value = data.get(key)
    if value is None:
        raise InvalidInputError(f"'{key}' is required.")
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"'{key}' must be a whole number.")
    _check_range(key, value, minimum, maximum)
    return value


def number_field(data: dict, key: str, *, minimum: Optional[float] = None,
                 maximum: Optional[float] = None, exclusive_minimum: bool = False,
                 exclusive_maximum: bool = False, places: int = 2) -> float:
    """A required number field, rounded to ``places`` decimals (as stored in DECIMAL)."""
    value = data.get(key)
    if value is None:
        raise InvalidInputError(f"'{key}' is required.")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInputError(f"'{key}' must be a number.")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise InvalidInputError(f"'{key}' must be a number.") from None
    if not number.is_finite():
        raise InvalidInputError(f"'{key}' must be a finite number.")
    number = float(round(number, places))
    if minimum is not None and (number <= minimum if exclusive_minimum else number < minimum):
        raise InvalidInputError(
            f"'{key}' must be {'greater than' if exclusive_minimum else 'at least'} {minimum:g}.")
    if maximum is not None and (number >= maximum if exclusive_maximum else number > maximum):
        raise InvalidInputError(
            f"'{key}' must be {'less than' if exclusive_maximum else 'at most'} {maximum:g}.")
    return number


def bool_field(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise InvalidInputError(f"'{key}' must be true or false.")
    return value


def _check_range(key: str, value: int, minimum: Optional[int], maximum: Optional[int]) -> None:
    if minimum is not None and value < minimum:
        raise InvalidInputError(f"'{key}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise InvalidInputError(f"'{key}' must be at most {maximum}.")


# ---------------------------------------------------------------------------
# Query string: filters and pagination
# ---------------------------------------------------------------------------

def query_text(name: str, *, max_length: int = 60) -> Optional[str]:
    """A trimmed query-string value, or None when absent/blank."""
    value = (request.args.get(name) or "").strip()
    if len(value) > max_length:
        raise InvalidInputError(f"Query parameter '{name}' may be at most {max_length} characters.")
    return value or None


def query_choice(name: str, choices: Iterable[str], *,
                 default: Optional[str] = None) -> Optional[str]:
    """A query-string value that must be one of ``choices``."""
    value = query_text(name)
    if value is None:
        return default
    choices = list(choices)
    if value not in choices:
        raise InvalidInputError(f"Query parameter '{name}' must be one of: {', '.join(choices)}.")
    return value


def query_bool(name: str) -> Optional[bool]:
    """``true``/``false`` (also 1/0, yes/no) from the query string, or None."""
    value = query_text(name)
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise InvalidInputError(f"Query parameter '{name}' must be true or false.")


def _query_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise InvalidInputError(f"Query parameter '{name}' must be a whole number.") from None
    if not minimum <= value <= maximum:
        raise InvalidInputError(
            f"Query parameter '{name}' must be between {minimum} and {maximum}.")
    return value


def pagination() -> tuple[int, int]:
    """``(page, per_page)`` from the query string, validated against the config limits."""
    max_size = current_app.config["API_MAX_PAGE_SIZE"]
    per_page = _query_int("per_page", current_app.config["API_DEFAULT_PAGE_SIZE"], 1, max_size)
    page = _query_int("page", 1, 1, 1_000_000)
    return page, per_page


def paged_response(items: list, total: int, page: int, per_page: int,
                   filters: Optional[dict] = None) -> dict:
    """The standard list envelope."""
    pages = max(1, math.ceil(total / per_page))
    return {
        "ok": True,
        "items": items,
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages,
                       "has_next": page < pages, "has_prev": page > 1},
        "filters": {k: v for k, v in (filters or {}).items() if v is not None},
    }
