"""Generate web/assets/js/demo-data.js from the Python model.

The frontend has no backend yet, so this script runs a simulated three-day
pilot *through the real tracking rules* (every move goes through
BoxTracker, so invalid transitions are impossible) and exports:

* stages, transitions, role permissions, locations and alert thresholds,
  so the browser enforces exactly the same rules as the Python module;
* the simulated boxes, histories, incidents and audit trail;
* before/after KPIs (baseline = sample figures to be replaced with the
  measured baseline study) and EOQ / reorder recommendations per SKU.

Usage:  python3 scripts/build_demo_data.py [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import alerts, eoq, metrics  # noqa: E402
from models.products import PRODUCTS  # noqa: E402
from models.tracking import (  # noqa: E402
    ALLOWED_TRANSITIONS, FLOOR_ROLES, HOLD_RELEASE_ROLES, LOCATIONS, MAIN_FLOW,
    NOTE_REQUIRED_STAGES, PREREQUISITES, REGISTER_ROLES, SAST, STAGE_DEFAULT_LOCATIONS,
    TERMINAL_STAGES, TRANSITION_ROLES, BoxTracker, IncidentKind, Role, Stage,
    User,
)

OUT = ROOT / "web" / "assets" / "js" / "demo-data.js"

USERS = {
    Role.OPERATOR: User("thabo.m", Role.OPERATOR),
    Role.QC_INSPECTOR: User("lerato.k", Role.QC_INSPECTOR),
    Role.SUPERVISOR: User("johan.v", Role.SUPERVISOR),
    Role.EXECUTIVE: User("naledi.d", Role.EXECUTIVE),
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

# SAMPLE baseline over the same 3-day window and a similar box volume as the
# demo pilot - replace with the measured time-and-motion study.
BASELINE = metrics.ProcessSnapshot(
    label="Baseline - manual/paper process",
    period_days=3, boxes_dispatched=25, tracking_incidents=3, investigations=3,
    investigation_minutes=3 * 36, status_lookups=120, mean_status_retrieval_seconds=210,
    mean_stage_dwell_hours=5.8, quality_errors=1, dispatch_errors=1, traced_boxes=15,
    labour_hours=4 * 8 * 3,
)

# SAMPLE inventory inputs per SKU (boxes/year, R per order, R/box/year, R/box,
# std dev of daily demand in boxes, supplier/production lead time in days, boxes on hand).
REORDER_INPUTS = {
    "DON": (2400, 650, 18, 180, 3.0, 5, 70),
    "PON": (3100, 700, 22, 210, 4.0, 6, 140),
    "BGA": (4200, 900, 35, 420, 5.0, 10, 160),
    "BGP": (2600, 850, 30, 380, 3.5, 10, 210),
    "GGT": (5200, 600, 40, 160, 7.0, 3, 45),
    "GST": (4800, 600, 38, 150, 6.5, 3, 95),
    "GIT": (3900, 600, 36, 150, 5.5, 3, 30),
    "DGA": (2200, 650, 18, 190, 3.0, 5, 120),
    "SDT": (1800, 700, 20, 240, 2.5, 7, 52),
    "MSP": (6100, 500, 55, 95, 9.0, 2, 38),
}


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def simulate(as_of: datetime, rng: random.Random, days: int = 3, boxes_per_product: int = 5):
    tracker = BoxTracker(clock=lambda: as_of)
    start = as_of - timedelta(days=days)
    actions: list[tuple[datetime, int, str, dict]] = []   # (time, order, kind, payload)
    order = 0

    def plan(at, action, **payload):
        nonlocal order
        if at <= as_of:
            actions.append((at, order, action, payload))
            order += 1
        return at <= as_of

    # Small, readable demo: the same number of boxes for every product.
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
        stuck = rng.random() < 0.06            # some boxes linger -> alerts
        while stage is not Stage.DISPATCHED:
            lo, hi = STAGE_HOURS[stage]
            hours = rng.uniform(lo, hi) * (rng.uniform(2.5, 4) if stuck else 1)
            t += timedelta(hours=hours)
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

        if rng.random() < 0.04:  # misplaced box investigation (assumed pilot rate)
            opened = registered_at + timedelta(hours=rng.uniform(0.5, 4))
            plan(opened, "misplaced", idx=i,
                 minutes=rng.uniform(3, 15), found=rng.choice(list(LOCATIONS)[:9]))

    for hours_ago in (20, 3):  # damaged label scans on whichever box is in progress
        plan(as_of - timedelta(hours=hours_ago), "damaged", idx=None)
    plan(start + timedelta(hours=30), "incident", idx=5, incident_kind=IncidentKind.DISPATCH_ERROR,
         note="Box loaded onto wrong route - recalled at dock")
    plan(start + timedelta(hours=50), "incident", idx=17, incident_kind=IncidentKind.QUALITY_ERROR,
         note="Customer reported leaking tub")

    ids: dict[int, str] = {}
    for at, _, kind, p in sorted(actions, key=lambda a: (a[0], a[1])):
        if kind not in ("register", "damaged") and p["idx"] not in ids:
            continue
        if kind == "register":
            box = tracker.register_box(p["sku"], p["batch"], p["qty"], USERS[Role.OPERATOR],
                                       p["location"], at=at)
            ids[p["idx"]] = box.box_id
        elif kind == "move":
            box_id, to = ids[p["idx"]], p["to"]
            location = rng.choice(STAGE_DEFAULT_LOCATIONS[to])
            user = USERS[p["role"]]
            if to in NOTE_REQUIRED_STAGES or rng.random() < 0.06:
                tracker.move(box_id, to, user, location, at=at, note=p.get("note", ""))
            else:
                result = tracker.scan(box_id, user, location, to, at=at)
                assert result.ok, result.message
        elif kind == "misplaced":
            inc = tracker.open_incident(IncidentKind.MISPLACED, USERS[Role.OPERATOR],
                                        box_id=ids[p["idx"]], at=at,
                                        note="Box not at last scanned location")
            closed = at + timedelta(minutes=p["minutes"])
            if closed <= as_of:
                tracker.close_incident(inc.incident_id, USERS[Role.SUPERVISOR],
                                       "Found via last-scan location and re-scanned",
                                       at=closed)
        elif kind == "damaged":
            active = [b for b in tracker.boxes if not b.is_terminal]
            if not active:
                continue
            box = rng.choice(active)
            good = box.box_id
            result = tracker.scan(good[:-1] + ("X" if good[-1] != "X" else "Y"),
                                  USERS[Role.OPERATOR], box.location, at=at)
            # Operator types the printed ID, which links the incident to the box.
            tracker.link_incident(result.incident.incident_id, good, USERS[Role.OPERATOR], at=at)
            tracker.reprint_label(good, USERS[Role.OPERATOR], "QR torn - reprinted", at=at)
        elif kind == "incident":
            tracker.open_incident(p["incident_kind"], USERS[Role.SUPERVISOR], box_id=ids[p["idx"]],
                                  at=at, note=p["note"])
    return tracker, start


def export(as_of: datetime, seed: int) -> dict:
    rng = random.Random(seed)
    tracker, start = simulate(as_of, rng)
    assert tracker.audit.verify()
    end = as_of + timedelta(seconds=1)

    current = metrics.snapshot_from_tracker(
        tracker, label="After - 3-day pilot", start=start, end=end,
        labour_hours=4 * 8 * 3, status_lookups=120, mean_status_retrieval_seconds=15)

    def snapshot_dict(s: metrics.ProcessSnapshot) -> dict:
        return {"label": s.label, "period_days": s.period_days, **s.kpis(),
                "boxes_dispatched": s.boxes_dispatched, "tracking_incidents": s.tracking_incidents,
                "non_value_hours": s.non_value_hours, "labour_hours": s.labour_hours}

    reorder = []
    for product in PRODUCTS:
        d, s, h, c, sd, lt, on_hand = REORDER_INPUTS[product.sku]
        p = eoq.plan_reorder(product.sku, annual_demand=d, order_cost=s, holding_cost=h,
                             unit_cost=c, daily_demand_std=sd, lead_time_days=lt,
                             on_hand=on_hand)
        reorder.append({"sku": product.sku, "annual_demand": d, "order_cost": s,
                        "holding_cost": h, "unit_cost": c, "daily_demand_std": sd,
                        "lead_time_days": lt, "avg_daily_demand": d / 250,
                        **{k: getattr(p, k) for k in ("eoq", "orders_per_year",
                                                       "total_annual_cost", "safety_stock",
                                                       "reorder_point", "on_hand", "status")}})

    all_stages = list(MAIN_FLOW) + [Stage.ON_HOLD, Stage.REJECTED]
    return {
        "generated_at": iso(as_of),
        "notice": "Simulated pilot data and SAMPLE baseline figures - replace with measured data.",
        "stages": [{"key": s.value, "label": s.label, "terminal": s in TERMINAL_STAGES,
                    "main_flow_index": MAIN_FLOW.index(s) if s in MAIN_FLOW else None}
                   for s in all_stages],
        "main_flow": [s.value for s in MAIN_FLOW],
        "transitions": {s.value: sorted(t.value for t in ALLOWED_TRANSITIONS[s]) for s in Stage},
        "prerequisites": {s.value: [p.value for p in req] for s, req in PREREQUISITES.items()},
        "cycle_reset": [Stage.IN_PRODUCTION.value, Stage.AWAITING_QC.value],
        "transition_roles": {s.value: sorted(r.value for r in roles)
                             for s, roles in TRANSITION_ROLES.items()},
        "hold_release_roles": sorted(r.value for r in HOLD_RELEASE_ROLES),
        "register_roles": sorted(r.value for r in REGISTER_ROLES),
        "floor_roles": sorted(r.value for r in FLOOR_ROLES),
        "note_required": sorted(s.value for s in NOTE_REQUIRED_STAGES),
        "roles": [{"key": r.value, "label": r.label, "user": USERS[r].username} for r in Role],
        "locations": [{"code": c, "label": l} for c, l in LOCATIONS.items()],
        "stage_locations": {s.value: list(v) for s, v in STAGE_DEFAULT_LOCATIONS.items()},
        "thresholds": {s.value: {"warning_minutes": t.warning.total_seconds() / 60,
                                 "critical_minutes": t.critical.total_seconds() / 60}
                       for s, t in alerts.DEFAULT_THRESHOLDS.items()},
        "perishable_factor": alerts.PERISHABLE_FACTOR,
        "products": [{"sku": p.sku, "name": p.name, "category": p.category,
                      "perishable": p.perishable} for p in PRODUCTS],
        "incident_kinds": [{"key": k.value, "label": k.label} for k in IncidentKind],
        "boxes": [{
            "box_id": b.box_id, "sku": b.sku, "batch_no": b.batch_no, "quantity": b.quantity,
            "created_at": iso(b.created_at), "label_version": b.label_version,
            "location": b.location, "location_updated_at": iso(b.location_updated_at),
            "history": [{"from": e.from_stage.value if e.from_stage else None,
                         "to": e.to_stage.value, "at": iso(e.at), "user": e.username,
                         "role": e.role.value, "location": e.location,
                         "method": e.method.value, "note": e.note} for e in b.history],
        } for b in tracker.boxes],
        "incidents": [{
            "id": i.incident_id, "kind": i.kind.value, "box_id": i.box_id, "note": i.note,
            "opened_at": iso(i.opened_at), "opened_by": i.opened_by,
            "closed_at": iso(i.closed_at), "closed_by": i.closed_by, "resolution": i.resolution,
        } for i in tracker.incidents],
        "audit": [{"seq": e.seq, "at": iso(e.at), "user": e.username, "role": e.role,
                   "action": e.action, "outcome": e.outcome, "box_id": e.box_id,
                   "detail": e.detail, "hash": e.hash[:12]} for e in tracker.audit],
        "kpis": {
            "baseline": snapshot_dict(BASELINE),
            "current": snapshot_dict(current),
            "comparison": [{"key": r.kpi.key, "label": r.kpi.label, "unit": r.kpi.unit,
                            "higher_is_better": r.kpi.higher_is_better, "baseline": r.baseline,
                            "current": r.current, "change_pct": r.change_pct,
                            "improvement_pct": r.improvement_pct, "improved": r.improved}
                           for r in metrics.compare(BASELINE, current)],
            "scan_compliance_pct": metrics.scan_compliance_pct(tracker.boxes, start, end),
            "dwell_by_stage_hours": {s.value: h for s, h in
                                     metrics.mean_dwell_by_stage(tracker.boxes, start, end).items()},
        },
        "reorder": reorder,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    as_of = datetime.now(SAST).replace(second=0, microsecond=0)
    data = export(as_of, args.seed)
    OUT.write_text("/* Generated by scripts/build_demo_data.py - do not edit by hand. */\n"
                   "window.SG_DATA = " + json.dumps(data, indent=1) + ";\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(data['boxes'])} boxes, "
          f"{len(data['incidents'])} incidents, {len(data['audit'])} audit entries.")


if __name__ == "__main__":
    main()
