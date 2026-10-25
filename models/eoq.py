"""Reorder logic: EOQ, safety stock and reorder point.

Standalone - no Flask, no database. The API layer calls these functions per
SKU and uses :func:`stock_status` to drive the exception badges in the UI.

Model assumptions (classic Wilson EOQ):

* demand is steady and known over the year;
* the whole order arrives at once after a fixed lead time;
* ordering cost per order and holding cost per unit per year are constant;
* no quantity discounts; stock-outs are prevented by safety stock.

Units must be consistent: if ``annual_demand`` is in boxes, holding cost is
Rand per box per year and the EOQ is in boxes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist


def _require_positive(**values: float) -> None:
    for name, value in values.items():
        if value is None or value <= 0:
            raise ValueError(f"{name} must be greater than zero (got {value}).")


def _require_non_negative(**values: float) -> None:
    for name, value in values.items():
        if value is None or value < 0:
            raise ValueError(f"{name} cannot be negative (got {value}).")


def calculate_eoq(annual_demand: float, order_cost: float, holding_cost: float) -> float:
    """Economic order quantity.

    EOQ = sqrt(2 * D * S / H)

    Args:
        annual_demand: D, units required per year.
        order_cost: S, fixed cost per order or production run (R).
        holding_cost: H, cost to hold one unit for one year (R/unit/year).

    Returns:
        The order quantity (units) that minimises ordering + holding cost.

    Example:
        D=1000, S=R100, H=R5 -> sqrt(40 000) = 200 units.
    """
    _require_positive(annual_demand=annual_demand, order_cost=order_cost,
                      holding_cost=holding_cost)
    return math.sqrt(2 * annual_demand * order_cost / holding_cost)


def orders_per_year(annual_demand: float, order_quantity: float) -> float:
    """Number of orders per year, N = D / Q."""
    _require_positive(annual_demand=annual_demand, order_quantity=order_quantity)
    return annual_demand / order_quantity


def total_annual_cost(annual_demand: float, order_quantity: float, order_cost: float,
                      holding_cost: float, unit_cost: float = 0.0) -> float:
    """Total annual inventory cost for order quantity Q.

    TC = (D / Q) * S + (Q / 2) * H + D * C

    The first two terms are ordering and holding cost; ``unit_cost`` (C)
    adds the purchase cost, which does not depend on Q. At Q = EOQ the
    ordering and holding terms are equal.
    """
    _require_positive(annual_demand=annual_demand, order_quantity=order_quantity,
                      order_cost=order_cost, holding_cost=holding_cost)
    _require_non_negative(unit_cost=unit_cost)
    return (annual_demand / order_quantity * order_cost
            + order_quantity / 2 * holding_cost
            + annual_demand * unit_cost)


def z_score(service_level: float) -> float:
    """Standard-normal z for a cycle service level, e.g. 0.95 -> 1.645."""
    if not 0 < service_level < 1:
        raise ValueError("service_level must be between 0 and 1 (exclusive).")
    return NormalDist().inv_cdf(service_level)


def safety_stock(daily_demand_std: float, lead_time_days: float,
                 service_level: float = 0.95) -> float:
    """Safety stock for demand variability over a fixed lead time.

    SS = z * sigma_d * sqrt(L)

    Args:
        daily_demand_std: sigma_d, standard deviation of daily demand (units).
        lead_time_days: L, days between placing and receiving an order.
        service_level: probability of not running out during a cycle.

    Assumes daily demand is normally distributed and independent day to day.
    """
    _require_non_negative(daily_demand_std=daily_demand_std)
    _require_positive(lead_time_days=lead_time_days)
    return z_score(service_level) * daily_demand_std * math.sqrt(lead_time_days)


def reorder_point(average_daily_demand: float, lead_time_days: float,
                  safety_stock_units: float = 0.0) -> float:
    """Stock level at which to reorder.

    ROP = d * L + SS
    """
    _require_non_negative(average_daily_demand=average_daily_demand,
                          safety_stock_units=safety_stock_units)
    _require_positive(lead_time_days=lead_time_days)
    return average_daily_demand * lead_time_days + safety_stock_units


#: Badge statuses, most urgent last.
STATUS_OK = "OK"
STATUS_REORDER = "REORDER"
STATUS_CRITICAL = "CRITICAL"


def stock_status(on_hand: float, reorder_point_units: float, safety_stock_units: float) -> str:
    """Badge for a SKU: CRITICAL at/below safety stock, REORDER at/below ROP, else OK."""
    _require_non_negative(on_hand=on_hand)
    if on_hand <= safety_stock_units:
        return STATUS_CRITICAL
    if on_hand <= reorder_point_units:
        return STATUS_REORDER
    return STATUS_OK


@dataclass(frozen=True)
class ReorderPlan:
    sku: str
    eoq: float
    orders_per_year: float
    total_annual_cost: float
    safety_stock: float
    reorder_point: float
    on_hand: float
    status: str


def plan_reorder(sku: str, *, annual_demand: float, order_cost: float, holding_cost: float,
                 daily_demand_std: float, lead_time_days: float, on_hand: float,
                 unit_cost: float = 0.0, service_level: float = 0.95,
                 working_days: int = 250) -> ReorderPlan:
    """Full reorder recommendation for one SKU.

    Average daily demand is ``annual_demand / working_days``.
    """
    _require_positive(working_days=working_days)
    q = calculate_eoq(annual_demand, order_cost, holding_cost)
    ss = safety_stock(daily_demand_std, lead_time_days, service_level)
    rop = reorder_point(annual_demand / working_days, lead_time_days, ss)
    return ReorderPlan(
        sku=sku,
        eoq=q,
        orders_per_year=orders_per_year(annual_demand, q),
        total_annual_cost=total_annual_cost(annual_demand, q, order_cost, holding_cost, unit_cost),
        safety_stock=ss,
        reorder_point=rop,
        on_hand=on_hand,
        status=stock_status(on_hand, rop, ss),
    )
