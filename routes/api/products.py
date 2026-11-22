"""Product catalogue endpoints: products, categories and reorder policies.

    GET    /api/products                          list products (filters + pagination)
    POST   /api/products                          add a product (+ optional policy) -> 201
    GET    /api/products/<sku>                    one product with its reorder plan
    PATCH  /api/products/<sku>                    change name, category, cost, active flag
    GET    /api/products/<sku>/reorder-policy     reorder inputs + EOQ / ROP plan
    PUT    /api/products/<sku>/reorder-policy     create or replace the policy
    PATCH  /api/products/<sku>/reorder-policy     change some inputs (e.g. on_hand_qty)
    GET    /api/categories                        product categories
    POST   /api/categories                        add a category              -> 201

Writes need a supervisor ``username`` and are audited. Policy writes return
the plan recalculated by the EOQ model (:mod:`models.eoq`).
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from database.db import get_db
from models import repository
from models.products import SKU_PATTERN
from routes.api.common import (
    bool_field, int_field, json_body, number_field, paged_response, pagination, query_bool,
    query_text, text_field,
)
from services import catalogue_service
from services.app_data import product_to_dict, reorder_plan_to_dict
from services.errors import InvalidInputError, NotFoundError
from services.tracker_service import TrackerSession

products_bp = Blueprint("products_api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)

PRODUCT_FIELDS = {"sku", "name", "category", "unit_cost", "reorder_policy"}
PRODUCT_UPDATE_FIELDS = {"name", "category", "unit_cost", "active"}


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

def _product_fields(data: dict, keys) -> dict:
    """Parse the product fields present in ``keys``."""
    parsers = {
        "name": lambda: text_field(data, "name", max_length=80, min_length=3),
        "category": lambda: text_field(data, "category", max_length=40),
        "unit_cost": lambda: number_field(data, "unit_cost", minimum=0, maximum=99_999_999),
        "active": lambda: bool_field(data, "active"),
    }
    return {key: parsers[key]() for key in keys}


# column -> parser for a reorder policy value; limits match the schema CHECKs/types.
POLICY_PARSERS = {
    "annual_demand": lambda d: int_field(d, "annual_demand", minimum=1, maximum=10_000_000),
    "order_cost": lambda d: number_field(d, "order_cost", minimum=0, exclusive_minimum=True,
                                         maximum=99_999_999),
    "holding_cost": lambda d: number_field(d, "holding_cost", minimum=0, exclusive_minimum=True,
                                           maximum=99_999_999),
    "daily_demand_std": lambda d: number_field(d, "daily_demand_std", minimum=0,
                                               maximum=999_999),
    "lead_time_days": lambda d: int_field(d, "lead_time_days", minimum=1, maximum=365),
    "on_hand_qty": lambda d: int_field(d, "on_hand_qty", minimum=0, maximum=10_000_000),
    "service_level": lambda d: number_field(d, "service_level", minimum=0, maximum=1,
                                            exclusive_minimum=True, exclusive_maximum=True,
                                            places=3),
    "working_days": lambda d: int_field(d, "working_days", minimum=1, maximum=366),
}
POLICY_DEFAULTS = {"on_hand_qty": 0, "service_level": 0.95, "working_days": 250}


def _policy_fields(data: dict, *, partial: bool) -> dict:
    """Parse a reorder policy.

    ``partial`` (PATCH) parses only the fields sent. Otherwise every field is
    required except those in POLICY_DEFAULTS, which fall back to the default.
    """
    unknown = sorted(set(data) - set(POLICY_PARSERS) - {"username"})
    if unknown:
        raise InvalidInputError(f"Unknown reorder policy field(s): {', '.join(unknown)}.")
    values = {}
    for key, parse in POLICY_PARSERS.items():
        if key in data:
            values[key] = parse(data)
        elif not partial:
            values[key] = POLICY_DEFAULTS[key] if key in POLICY_DEFAULTS else parse(data)
    return values


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@products_bp.get("/products")
def list_products():
    page, per_page = pagination()
    filters = {
        "category": query_text("category", max_length=40),
        "active": query_bool("active"),
        "q": query_text("q", max_length=80),
    }
    rows, total = repository.query_products(
        get_db(), category=filters["category"], active=filters["active"], search=filters["q"],
        limit=per_page, offset=(page - 1) * per_page)
    return jsonify(paged_response([product_to_dict(r) for r in rows], total, page, per_page,
                                  filters))


@products_bp.post("/products")
def create_product():
    data = json_body(allowed=PRODUCT_FIELDS)
    sku = text_field(data, "sku", max_length=3).upper()
    if not SKU_PATTERN.match(sku):
        raise InvalidInputError("'sku' must be exactly three letters, e.g. GRW.")
    fields = _product_fields(data, ("name", "category", "unit_cost"))
    policy = None
    if data.get("reorder_policy") is not None:
        if not isinstance(data["reorder_policy"], dict):
            raise InvalidInputError("'reorder_policy' must be a JSON object.")
        policy = _policy_fields(data["reorder_policy"], partial=False)
    with TrackerSession(get_db()) as session:
        product = catalogue_service.create_product(
            session, text_field(data, "username"), sku=sku, name=fields["name"],
            category=fields["category"], unit_cost=fields["unit_cost"], reorder_policy=policy)
    log.info("Created product %s", sku)
    return jsonify({"ok": True, "product": product}), 201


@products_bp.get("/products/<sku>")
def get_product(sku: str):
    return jsonify({"ok": True, "product": catalogue_service.product_detail(get_db(),
                                                                            sku.upper())})


@products_bp.patch("/products/<sku>")
def update_product(sku: str):
    data = json_body(allowed=PRODUCT_UPDATE_FIELDS)
    changes = _product_fields(data, [k for k in PRODUCT_UPDATE_FIELDS if k in data])
    if not changes:
        allowed = ", ".join(sorted(PRODUCT_UPDATE_FIELDS))
        raise InvalidInputError(f"Send at least one of: {allowed}.")
    with TrackerSession(get_db()) as session:
        product = catalogue_service.update_product(session, text_field(data, "username"),
                                                   sku.upper(), changes)
    log.info("Updated product %s: %s", sku.upper(), ", ".join(changes))
    return jsonify({"ok": True, "product": product})


# ---------------------------------------------------------------------------
# Reorder policies (inputs to the EOQ / reorder point model)
# ---------------------------------------------------------------------------

@products_bp.get("/products/<sku>/reorder-policy")
def get_reorder_policy(sku: str):
    rows = repository.fetch_reorder_policies(get_db(), sku.upper())
    if not rows:
        raise NotFoundError(f"No reorder policy for SKU '{sku}'.")
    return jsonify({"ok": True, "plan": reorder_plan_to_dict(rows[0])})


def _save_policy(sku: str, *, partial: bool):
    data = json_body(allowed=POLICY_PARSERS.keys())
    values = _policy_fields(data, partial=partial)
    if partial and not values:
        raise InvalidInputError(f"Send at least one of: {', '.join(POLICY_PARSERS)}.")
    with TrackerSession(get_db()) as session:
        plan = catalogue_service.save_reorder_policy(session, text_field(data, "username"),
                                                     sku.upper(), values, partial=partial)
    log.info("Saved reorder policy for %s: %s", sku.upper(), ", ".join(values))
    return jsonify({"ok": True, "plan": plan})


@products_bp.put("/products/<sku>/reorder-policy")
def replace_reorder_policy(sku: str):
    return _save_policy(sku, partial=False)


@products_bp.patch("/products/<sku>/reorder-policy")
def update_reorder_policy(sku: str):
    return _save_policy(sku, partial=True)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@products_bp.get("/categories")
def list_categories():
    rows = repository.fetch_categories(get_db())
    return jsonify({"ok": True, "items": [
        {"id": r["category_id"], "name": r["category_name"],
         "perishable": bool(r["is_perishable"]), "products": r["products"]} for r in rows]})


@products_bp.post("/categories")
def create_category():
    data = json_body(allowed={"name", "perishable"})
    name = text_field(data, "name", max_length=40, min_length=3)
    perishable = bool_field(data, "perishable") if "perishable" in data else False
    with TrackerSession(get_db()) as session:
        category = catalogue_service.create_category(session, text_field(data, "username"),
                                                     name, perishable)
    log.info("Created category %s", name)
    return jsonify({"ok": True, "category": category}), 201
