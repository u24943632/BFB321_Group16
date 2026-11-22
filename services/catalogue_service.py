"""Product catalogue changes: products, categories and reorder policies.

This is what makes the catalogue scalable (proposal objective 2): seasonal
products are added as rows through the API instead of being written into
the code, and the tracker picks them up on the next request.

Every function runs inside a :class:`services.tracker_service.TrackerSession`
so that the change and its audit entry are saved in one transaction.
Permission is checked first (only supervisors may change the catalogue);
a refused attempt is audited and nothing else is written.
"""

from __future__ import annotations

from typing import Optional

from models import repository
from models.products import SKU_PATTERN
from services.app_data import product_to_dict, reorder_plan_to_dict
from services.errors import ConflictError, InvalidInputError, NotFoundError
from services.tracker_service import TrackerSession


def _require_product(conn, sku: str) -> dict:
    product = repository.fetch_product(conn, sku)
    if product is None:
        raise NotFoundError(f"No product with SKU '{sku}'.")
    return product


def _require_category(conn, name: str) -> dict:
    category = repository.find_category(conn, name)
    if category is None:
        known = ", ".join(c["category_name"] for c in repository.fetch_categories(conn))
        raise InvalidInputError(f"Unknown category '{name}'. Choose one of: {known}, "
                                "or create it first with POST /api/categories.")
    return category


def _plan(conn, sku: str) -> Optional[dict]:
    rows = repository.fetch_reorder_policies(conn, sku)
    return reorder_plan_to_dict(rows[0]) if rows else None


def product_detail(conn, sku: str) -> dict:
    """Product JSON with its current reorder plan (None if it has no policy)."""
    return {**product_to_dict(_require_product(conn, sku)), "reorder": _plan(conn, sku)}


def create_category(session: TrackerSession, username: str, name: str,
                    perishable: bool) -> dict:
    user = session.user(username)
    if repository.find_category(session.conn, name):
        raise ConflictError(f"Category '{name}' already exists.")
    session.tracker.record_catalogue_change(
        user, "CATEGORY_CREATE", f"{name} ({'perishable' if perishable else 'shelf-stable'})")
    category_id = repository.insert_category(session.conn, name, perishable)
    return {"id": category_id, "name": name, "perishable": perishable, "products": 0}


def create_product(session: TrackerSession, username: str, *, sku: str, name: str,
                   category: str, unit_cost: float,
                   reorder_policy: Optional[dict] = None) -> dict:
    """Add a product (and optionally its reorder policy) to the catalogue."""
    user = session.user(username)
    conn = session.conn
    if not SKU_PATTERN.match(sku):
        raise InvalidInputError("'sku' must be exactly three capital letters, e.g. GRW.")
    if repository.fetch_product(conn, sku):
        raise ConflictError(f"A product with SKU '{sku}' already exists.")
    category_row = _require_category(conn, category)
    detail = f"{sku} {name} ({category_row['category_name']}), R {unit_cost:.2f}"
    session.tracker.record_catalogue_change(user, "PRODUCT_CREATE", detail)
    repository.insert_product(conn, sku=sku, name=name, category_id=category_row["category_id"],
                              unit_cost=unit_cost)
    if reorder_policy is not None:
        repository.save_reorder_policy(conn, sku, reorder_policy)
    return product_detail(conn, sku)


def update_product(session: TrackerSession, username: str, sku: str, changes: dict) -> dict:
    """Change name, category, unit cost and/or active flag of one product."""
    user = session.user(username)
    conn = session.conn
    current = _require_product(conn, sku)
    columns = {}
    if "name" in changes:
        columns["name"] = changes["name"]
    if "unit_cost" in changes:
        columns["unit_cost"] = changes["unit_cost"]
    if "active" in changes:
        columns["is_active"] = changes["active"]
    if "category" in changes:
        columns["category_id"] = _require_category(conn, changes["category"])["category_id"]
    summary = ", ".join(f"{key}={value}" for key, value in changes.items())
    session.tracker.record_catalogue_change(user, "PRODUCT_UPDATE",
                                            f"{sku} {current['product_name']}: {summary}")
    repository.update_product(conn, sku, columns)
    return product_detail(conn, sku)


def save_reorder_policy(session: TrackerSession, username: str, sku: str, values: dict, *,
                        partial: bool) -> dict:
    """Create/replace (PUT) or partly update (PATCH) a product's reorder policy.

    Returns the plan recalculated by the EOQ model with the new inputs.
    """
    user = session.user(username)
    conn = session.conn
    _require_product(conn, sku)
    existing = repository.fetch_reorder_policies(conn, sku)
    summary = ", ".join(f"{key}={value}" for key, value in values.items())  # what was sent
    if partial:
        if not existing:
            raise NotFoundError(f"'{sku}' has no reorder policy yet. Create it with PUT.")
        merged = {column: existing[0][column] for column in repository.REORDER_POLICY_COLUMNS}
        merged.update(values)
        values = merged
    session.tracker.record_catalogue_change(user, "POLICY_UPDATE", f"{sku}: {summary}")
    repository.save_reorder_policy(conn, sku, values)
    return _plan(conn, sku)
