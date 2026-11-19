"""Realistic demo data for every table.

Run through the CLI (reads the connection settings from .env)::

    flask --app app init-db

Lookup tables are filled from the catalogue and rule constants in
:mod:`models`. Boxes, status events, incidents and the audit trail come from
a simulated three-day pilot that is run *through the real tracking rules*
(every move goes through :class:`BoxTracker`), so the seed data can never
contain an invalid stage change and the audit hash chain is valid.

Baseline KPIs and reorder inputs are SAMPLE figures; replace them with the
measured time-and-motion study and real inventory data.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from models import alerts
from models.products import PRODUCTS
from models.repository import to_db_time
from models.tracking import (
    FLOOR_ROLES, HOLD_RELEASE_ROLES, LOCATIONS, MAIN_FLOW, NOTE_REQUIRED_STAGES,
    REGISTER_ROLES, SAST, STAGE_DEFAULT_LOCATIONS, TERMINAL_STAGES, TRACKING_INCIDENT_KINDS,
    TRANSITION_ROLES, BoxTracker, IncidentKind, Role, Stage, User,
)
from services.tracker_service import TrackerSnapshot, save_changes

USERS = {
    Role.OPERATOR: User("thabo.m", Role.OPERATOR),
    Role.QC_INSPECTOR: User("lerato.k", Role.QC_INSPECTOR),
    Role.SUPERVISOR: User("johan.v", Role.SUPERVISOR),
    Role.EXECUTIVE: User("naledi.d", Role.EXECUTIVE),
}
USER_ROWS = [  
    ("thabo.m", "Thabo Mokoena", Role.OPERATOR, True),
    ("lerato.k", "Lerato Khumalo", Role.QC_INSPECTOR, True),
    ("johan.v", "Johan van Wyk", Role.SUPERVISOR, True),
    ("naledi.d", "Naledi Dlamini", Role.EXECUTIVE, True),
    ("sipho.n", "Sipho Ndlovu", Role.OPERATOR, True),
    ("anika.b", "Anika Botha", Role.QC_INSPECTOR, False),  
]

PRODUCT_UNIT_COST = {  
    "DON": 180, "PON": 210, "BGA": 420, "BGP": 380, "GGT": 160,
    "GST": 150, "GIT": 150, "DGA": 190, "SDT": 240, "MSP": 95,
}

# SAMPLE inventory inputs per SKU: (boxes/year, R per order, R/box/year,
# std dev of daily demand in boxes, lead time in days, boxes on hand).
REORDER_INPUTS = {
    "DON": (2400, 650, 18, 3.0, 5, 70),
    "PON": (3100, 700, 22, 4.0, 6, 140),
    "BGA": (4200, 900, 35, 5.0, 10, 160),
    "BGP": (2600, 850, 30, 3.5, 10, 210),
    "GGT": (5200, 600, 40, 7.0, 3, 45),
    "GST": (4800, 600, 38, 6.5, 3, 95),
    "GIT": (3900, 600, 36, 5.5, 3, 30),
    "DGA": (2200, 650, 18, 3.0, 5, 120),
    "SDT": (1800, 700, 20, 2.5, 7, 52),
    "MSP": (6100, 500, 55, 9.0, 2, 38),
}


BASELINE = {
    "label": "Baseline - manual/paper process", "period_days": 3, "boxes_dispatched": 25,
    "tracking_incidents": 3, "investigations": 3, "investigation_minutes": 108,
    "status_lookups": 120, "mean_status_retrieval_seconds": 210,
    "mean_stage_dwell_hours": 5.8, "quality_errors": 1, "dispatch_errors": 1,
    "traced_boxes": 15, "labour_hours": 96,
}

# Hours a box typically spends in a stage before moving on (min, max).
STAGE_HOURS = {
    Stage.IN_PRODUCTION: (0.75, 4.0),
    Stage.AWAITING_QC: (0.25, 2.5),
    Stage.QC_COMPLETE: (0.25, 2.0),
    Stage.PACKAGING_COMPLETE: (0.5, 4.0),
    Stage.READY_FOR_DISPATCH: (1.0, 14.0),
}

QC_FAIL_REASONS = ["Seal integrity failure", "Underweight sample", "Foreign matter found",
                   "Label/lot mismatch", "Temperature excursion"]

PILOT_DAYS = 3



def _insert_many(conn, sql: str, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def seed_catalogue(conn) -> dict[str, int]:
    categories = {}
    for product in PRODUCTS:
        categories.setdefault(product.category, product.perishable)
    counts = {"product_categories": _insert_many(conn, """
        INSERT INTO product_categories (category_name, is_perishable) VALUES (%s, %s)""",
        list(categories.items()))}
    counts["products"] = _insert_many(conn, """
        INSERT INTO products (sku, product_name, category_id, unit_cost)
        VALUES (%s, %s, (SELECT category_id FROM product_categories WHERE category_name = %s), %s)""",
        [(p.sku, p.name, p.category, PRODUCT_UNIT_COST[p.sku]) for p in PRODUCTS])
    counts["reorder_policies"] = _insert_many(conn, """
        INSERT INTO reorder_policies (product_id, annual_demand, order_cost, holding_cost,
                                      daily_demand_std, lead_time_days, on_hand_qty)
        VALUES ((SELECT product_id FROM products WHERE sku = %s), %s, %s, %s, %s, %s, %s)""",
        [(sku, *values) for sku, values in REORDER_INPUTS.items()])
    return counts


def seed_people_and_places(conn) -> dict[str, int]:
    counts = {"roles": _insert_many(conn, """
        INSERT INTO roles (role_code, role_label, is_floor_role, can_register, can_release_hold)
        VALUES (%s, %s, %s, %s, %s)""",
        [(r.value, r.label, r in FLOOR_ROLES, r in REGISTER_ROLES, r in HOLD_RELEASE_ROLES)
         for r in Role])}
    counts["users"] = _insert_many(conn, """
        INSERT INTO users (username, full_name, role_code, is_active) VALUES (%s, %s, %s, %s)""",
        [(u, name, role.value, active) for u, name, role, active in USER_ROWS])
    counts["locations"] = _insert_many(conn, """
        INSERT INTO locations (location_code, location_name) VALUES (%s, %s)""",
        list(LOCATIONS.items()))
    return counts


def seed_process_rules(conn) -> dict[str, int]:
    all_stages = list(MAIN_FLOW) + [Stage.ON_HOLD, Stage.REJECTED]
    stage_rows = []
    for stage in all_stages:
        threshold = alerts.DEFAULT_THRESHOLDS.get(stage)
        stage_rows.append((
            stage.value, stage.label,
            MAIN_FLOW.index(stage) + 1 if stage in MAIN_FLOW else None,
            stage in TERMINAL_STAGES, stage in NOTE_REQUIRED_STAGES,
            STAGE_DEFAULT_LOCATIONS[stage][0],
            int(threshold.warning.total_seconds() // 60) if threshold else None,
            int(threshold.critical.total_seconds() // 60) if threshold else None,
        ))
    counts = {"stages": _insert_many(conn, """
        INSERT INTO stages (stage_code, stage_label, flow_position, is_terminal, requires_note,
                            default_location_code, warning_minutes, critical_minutes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", stage_rows)}
    counts["stage_permissions"] = _insert_many(conn, """
        INSERT INTO stage_permissions (stage_code, role_code) VALUES (%s, %s)""",
        [(stage.value, role.value) for stage, roles in TRANSITION_ROLES.items()
         for role in sorted(roles, key=lambda r: r.value)])
    counts["incident_kinds"] = _insert_many(conn, """
        INSERT INTO incident_kinds (kind_code, kind_label, is_tracking_incident)
        VALUES (%s, %s, %s)""",
        [(k.value, k.label, k in TRACKING_INCIDENT_KINDS) for k in IncidentKind])
    return counts


def seed_evaluation(conn, pilot_start: datetime) -> dict[str, int]:
    columns = ", ".join(BASELINE)
    placeholders = ", ".join(["%s"] * len(BASELINE))
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO kpi_baselines ({columns}) VALUES ({placeholders})",
                    tuple(BASELINE.values()))
        baseline_id = cur.lastrowid
        cur.execute("""
            INSERT INTO pilot_periods (label, baseline_id, starts_at, ends_at,
                                       labour_hours_per_day, status_lookups_per_day,
                                       mean_status_retrieval_seconds)
            VALUES (%s, %s, %s, NULL, %s, %s, %s)""",
            ("After - Derdepoort QR pilot", baseline_id, to_db_time(pilot_start), 32, 40, 15))
    return {"kpi_baselines": 1, "pilot_periods": 1}


# ---------------------------------------------------------------------------
# Simulated pilot (boxes, status_events, incidents, audit_log)
# ---------------------------------------------------------------------------

def simulate_pilot(as_of: datetime, rng: random.Random, days: int = PILOT_DAYS,
                   boxes_per_product: int = 5) -> BoxTracker:
    """Plan a pilot's events, then replay them in time order through the tracker."""
    tracker = BoxTracker(clock=lambda: as_of)
    start = as_of - timedelta(days=days)
    actions: list[tuple[datetime, int, str, dict]] = [] 

    def plan(at, action, **payload):
        if at <= as_of:
            actions.append((at, len(actions), action, payload))
        return at <= as_of

    products = [p for p in PRODUCTS for _ in range(boxes_per_product)]
    for i, product in enumerate(products):
        t = start + timedelta(hours=rng.uniform(0, days * 24))
        registered_at = t
        batch = f"{product.sku}-{t.strftime('%d%m')}-{'AB'[i % 2]}"
        if not plan(t, "register", idx=i, sku=product.sku, batch=batch,
                    qty=rng.choice([12, 12, 24, 20]),
                    location=STAGE_DEFAULT_LOCATIONS[Stage.IN_PRODUCTION][0]):
            continue
        stage = Stage.IN_PRODUCTION
        stuck = rng.random() < 0.06  
        while stage is not Stage.DISPATCHED:
            lo, hi = STAGE_HOURS[stage]
            t += timedelta(hours=rng.uniform(lo, hi) * (rng.uniform(2.5, 4) if stuck else 1))
            nxt = MAIN_FLOW[MAIN_FLOW.index(stage) + 1]
            if stage is Stage.AWAITING_QC and rng.random() < 0.07: 
                reason = rng.choice(QC_FAIL_REASONS)
                if not plan(t, "move", idx=i, to=Stage.ON_HOLD, role=Role.QC_INSPECTOR,
                            note=reason):
                    break
                t += timedelta(hours=rng.uniform(1, 5))
                if rng.random() < 0.35:
                    plan(t, "move", idx=i, to=Stage.REJECTED, role=Role.SUPERVISOR,
                         note=f"Write-off: {reason.lower()}")
                    break
                if not plan(t, "move", idx=i, to=Stage.AWAITING_QC, role=Role.SUPERVISOR,
                            note="Released for re-inspection"):
                    break
                continue
            role = Role.QC_INSPECTOR if nxt is Stage.QC_COMPLETE else Role.OPERATOR
            if not plan(t, "move", idx=i, to=nxt, role=role):
                break
            stage = nxt

        if rng.random() < 0.04:  
            plan(registered_at + timedelta(hours=rng.uniform(0.5, 4)), "misplaced", idx=i,
                 minutes=rng.uniform(3, 15))

    for hours_ago in (20, 3): 
        plan(as_of - timedelta(hours=hours_ago), "damaged", idx=None)
    plan(start + timedelta(hours=30), "incident", idx=5, kind=IncidentKind.DISPATCH_ERROR,
         note="Box loaded onto wrong route - recalled at dock")
    plan(start + timedelta(hours=50), "incident", idx=17, kind=IncidentKind.QUALITY_ERROR,
         note="Customer reported leaking tub")

    ids: dict[int, str] = {}
    for at, _, kind, p in sorted(actions, key=lambda a: (a[0], a[1])):
        if kind not in ("register", "damaged") and p["idx"] not in ids:
            continue
        _replay(tracker, rng, ids, at, kind, p, as_of)
    return tracker


def _replay(tracker: BoxTracker, rng: random.Random, ids: dict[int, str], at: datetime,
            kind: str, p: dict, as_of: datetime) -> None:
    """Apply one planned action to the tracker."""
    operator, supervisor = USERS[Role.OPERATOR], USERS[Role.SUPERVISOR]
    if kind == "register":
        box = tracker.register_box(p["sku"], p["batch"], p["qty"], operator, p["location"], at=at)
        ids[p["idx"]] = box.box_id
    elif kind == "move":
        box_id, to = ids[p["idx"]], p["to"]
        location = STAGE_DEFAULT_LOCATIONS[to][0]
        user = USERS[p["role"]]
        if to in NOTE_REQUIRED_STAGES or rng.random() < 0.06:  # typed in by hand
            tracker.move(box_id, to, user, location, at=at, note=p.get("note", ""))
        else:
            result = tracker.scan(box_id, user, location, to, at=at)
            assert result.ok, result.message
    elif kind == "misplaced":
        incident = tracker.open_incident(IncidentKind.MISPLACED, operator, box_id=ids[p["idx"]],
                                         at=at, note="Box not at last scanned location")
        closed = at + timedelta(minutes=p["minutes"])
        if closed <= as_of:
            tracker.close_incident(incident.incident_id, supervisor,
                                   "Found via last-scan location and re-scanned", at=closed)
    elif kind == "damaged":
        active = [b for b in tracker.boxes if not b.is_terminal]
        if not active:
            return
        box = rng.choice(active)
        good = box.box_id
        result = tracker.scan(good[:-1] + ("X" if good[-1] != "X" else "Y"), operator,
                              box.location, at=at)
        
        tracker.link_incident(result.incident.incident_id, good, operator, at=at)
        tracker.reprint_label(good, operator, "QR torn - reprinted", at=at)
    elif kind == "incident":
        tracker.open_incident(p["kind"], supervisor, box_id=ids[p["idx"]], at=at, note=p["note"])


def seed_database(conn, *, as_of: datetime | None = None, seed: int = 7) -> dict[str, int]:
    """Fill every table (which must be empty) and commit. Returns rows per table."""
    as_of = as_of or datetime.now(SAST).replace(second=0, microsecond=0)
    try:
        counts = {**seed_catalogue(conn), **seed_people_and_places(conn),
                  **seed_process_rules(conn)}
        tracker = simulate_pilot(as_of, random.Random(seed))
        with conn.cursor() as cur:
            cur.execute("SELECT username, user_id FROM users")
            user_ids = {r["username"]: r["user_id"] for r in cur.fetchall()}
        save_changes(conn, tracker, TrackerSnapshot(), user_ids)
        counts.update({
            "boxes": len(tracker.boxes),
            "status_events": sum(len(b.history) for b in tracker.boxes),
            "incidents": len(tracker.incidents),
            "audit_log": len(tracker.audit),
        })
        counts.update(seed_evaluation(conn, as_of - timedelta(days=PILOT_DAYS)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts
