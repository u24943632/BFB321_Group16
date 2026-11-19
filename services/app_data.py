"""Build the data the pages render (``window.SG_DATA``) from MySQL.

Everything shown on screen - products, stages, locations, thresholds, role
permissions, boxes, incidents, audit trail, KPIs and reorder advice - is read
from the database on each request. Only the stage graph (allowed transitions
and prerequisites) comes from :mod:`models.tracking`, because it is process
logic rather than data.

The serialisers here are also used by the API so a box or incident has the
same JSON shape everywhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from models import alerts, eoq, metrics, repository
from models.tracking import (
    ALLOWED_TRANSITIONS, PREREQUISITES, SAST, AuditEntry, Box, BoxTracker, Incident, Stage,
)
from services.tracker_service import load_tracker

NOTICE = "Simulated pilot data and SAMPLE baseline figures - replace with measured data."


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def box_to_dict(box: Box) -> dict:
    return {
        "box_id": box.box_id, "sku": box.sku, "batch_no": box.batch_no,
        "quantity": box.quantity, "created_at": iso(box.created_at),
        "label_version": box.label_version, "location": box.location,
        "location_updated_at": iso(box.location_updated_at),
        "history": [{
            "from": e.from_stage.value if e.from_stage else None, "to": e.to_stage.value,
            "at": iso(e.at), "user": e.username, "role": e.role.value,
            "location": e.location, "method": e.method.value, "note": e.note,
        } for e in box.history],
    }


def incident_to_dict(incident: Incident) -> dict:
    return {
        "id": incident.incident_id, "kind": incident.kind.value, "box_id": incident.box_id,
        "note": incident.note, "opened_at": iso(incident.opened_at),
        "opened_by": incident.opened_by, "closed_at": iso(incident.closed_at),
        "closed_by": incident.closed_by, "resolution": incident.resolution,
    }


def audit_to_dict(entry: AuditEntry) -> dict:
    return {
        "seq": entry.seq, "at": iso(entry.at), "user": entry.username, "role": entry.role,
        "action": entry.action, "outcome": entry.outcome, "box_id": entry.box_id,
        "detail": entry.detail, "hash": entry.hash[:12],
    }


def _rules(conn) -> dict:
    """Stages, roles, locations and permissions, read from the lookup tables."""
    stages = repository.fetch_stages(conn)
    roles = repository.fetch_roles(conn)
    users = repository.fetch_active_users(conn)
    first_user_by_role: dict[str, str] = {}
    for user in users:
        first_user_by_role.setdefault(user["role_code"], user["username"])

    transition_roles: dict[str, list[str]] = {s["stage_code"]: [] for s in stages}
    for row in repository.fetch_stage_permissions(conn):
        transition_roles[row["stage_code"]].append(row["role_code"])

    main_flow = [s["stage_code"] for s in stages if s["flow_position"] is not None]
    return {
        "stages": [{"key": s["stage_code"], "label": s["stage_label"],
                    "terminal": bool(s["is_terminal"]),
                    "main_flow_index": main_flow.index(s["stage_code"])
                    if s["stage_code"] in main_flow else None} for s in stages],
        "main_flow": main_flow,
        "transitions": {s.value: sorted(t.value for t in ALLOWED_TRANSITIONS[s]) for s in Stage},
        "prerequisites": {s.value: [p.value for p in req] for s, req in PREREQUISITES.items()},
        "cycle_reset": [Stage.IN_PRODUCTION.value, Stage.AWAITING_QC.value],
        "transition_roles": transition_roles,
        "hold_release_roles": [r["role_code"] for r in roles if r["can_release_hold"]],
        "register_roles": [r["role_code"] for r in roles if r["can_register"]],
        "floor_roles": [r["role_code"] for r in roles if r["is_floor_role"]],
        "note_required": [s["stage_code"] for s in stages if s["requires_note"]],
        # Roles with no active user cannot be chosen in the role switcher.
        "roles": [{"key": r["role_code"], "label": r["role_label"],
                   "user": first_user_by_role[r["role_code"]]}
                  for r in roles if r["role_code"] in first_user_by_role],
        "locations": [{"code": r["location_code"], "label": r["location_name"]}
                      for r in repository.fetch_locations(conn) if r["is_active"]],
        "stage_locations": {s["stage_code"]: [s["default_location_code"]] for s in stages},
        "thresholds": {s["stage_code"]: {"warning_minutes": s["warning_minutes"],
                                         "critical_minutes": s["critical_minutes"]}
                       for s in stages if s["warning_minutes"] is not None},
        "perishable_factor": alerts.PERISHABLE_FACTOR,
        "products": [{"sku": p["sku"], "name": p["product_name"], "category": p["category_name"],
                      "perishable": bool(p["is_perishable"])}
                     for p in repository.fetch_products(conn)],
        "incident_kinds": [{"key": k["kind_code"], "label": k["kind_label"]}
                           for k in repository.fetch_incident_kinds(conn)],
    }


def _baseline_snapshot(pilot: dict) -> metrics.ProcessSnapshot:
    def col(name):
        return pilot[f"baseline_{name}"]

    return metrics.ProcessSnapshot(
        label=col("label"), period_days=float(col("period_days")),
        boxes_dispatched=col("boxes_dispatched"), tracking_incidents=col("tracking_incidents"),
        investigations=col("investigations"),
        investigation_minutes=float(col("investigation_minutes")),
        status_lookups=col("status_lookups"),
        mean_status_retrieval_seconds=float(col("mean_status_retrieval_seconds")),
        mean_stage_dwell_hours=float(col("mean_stage_dwell_hours")),
        quality_errors=col("quality_errors"), dispatch_errors=col("dispatch_errors"),
        traced_boxes=col("traced_boxes"), labour_hours=float(col("labour_hours")),
    )


def _snapshot_to_dict(snapshot: metrics.ProcessSnapshot) -> dict:
    return {"label": snapshot.label, "period_days": snapshot.period_days, **snapshot.kpis(),
            "boxes_dispatched": snapshot.boxes_dispatched,
            "tracking_incidents": snapshot.tracking_incidents,
            "non_value_hours": snapshot.non_value_hours, "labour_hours": snapshot.labour_hours}


def _kpis(conn, tracker: BoxTracker, now: datetime) -> Optional[dict]:
    """Baseline (kpi_baselines) vs the active pilot (pilot_periods), or None if no pilot."""
    pilot = repository.fetch_active_pilot(conn)
    if pilot is None:
        return None
    start = repository.from_db_time(pilot["starts_at"])
    end = repository.from_db_time(pilot["ends_at"]) or now + timedelta(seconds=1)
    days = (end - start).total_seconds() / 86400
    baseline = _baseline_snapshot(pilot)
    current = metrics.snapshot_from_tracker(
        tracker, label=pilot["label"], start=start, end=end,
        labour_hours=float(pilot["labour_hours_per_day"]) * days,
        status_lookups=round(pilot["status_lookups_per_day"] * days),
        mean_status_retrieval_seconds=float(pilot["mean_status_retrieval_seconds"]))
    return {
        "baseline": _snapshot_to_dict(baseline),
        "current": _snapshot_to_dict(current),
        "comparison": [{"key": r.kpi.key, "label": r.kpi.label, "unit": r.kpi.unit,
                        "higher_is_better": r.kpi.higher_is_better, "baseline": r.baseline,
                        "current": r.current, "change_pct": r.change_pct,
                        "improvement_pct": r.improvement_pct, "improved": r.improved}
                       for r in metrics.compare(baseline, current)],
        "scan_compliance_pct": metrics.scan_compliance_pct(tracker.boxes, start, end),
        "dwell_by_stage_hours": {s.value: h for s, h in
                                 metrics.mean_dwell_by_stage(tracker.boxes, start, end).items()},
    }


def _reorder(conn) -> list[dict]:
    """EOQ, safety stock and reorder point per product from reorder_policies."""
    plans = []
    for row in repository.fetch_reorder_policies(conn):
        inputs = {
            "annual_demand": row["annual_demand"], "order_cost": float(row["order_cost"]),
            "holding_cost": float(row["holding_cost"]), "unit_cost": float(row["unit_cost"]),
            "daily_demand_std": float(row["daily_demand_std"]),
            "lead_time_days": row["lead_time_days"],
        }
        plan = eoq.plan_reorder(row["sku"], **inputs, on_hand=row["on_hand_qty"],
                                service_level=float(row["service_level"]),
                                working_days=row["working_days"])
        plans.append({"sku": row["sku"], **inputs,
                      "avg_daily_demand": row["annual_demand"] / row["working_days"],
                      **{k: getattr(plan, k) for k in ("eoq", "orders_per_year",
                                                       "total_annual_cost", "safety_stock",
                                                       "reorder_point", "on_hand", "status")}})
    return plans


def build_app_data(conn, now: Optional[datetime] = None) -> dict:
    """Everything the frontend needs, read from MySQL in one request."""
    now = now or datetime.now(SAST)
    tracker = load_tracker(conn, clock=lambda: now)
    return {
        "generated_at": iso(now),
        "notice": NOTICE,
        **_rules(conn),
        "boxes": [box_to_dict(b) for b in tracker.boxes],
        "incidents": [incident_to_dict(i) for i in tracker.incidents],
        "audit": [audit_to_dict(e) for e in tracker.audit],
        "audit_verified": tracker.audit.verify(),
        "kpis": _kpis(conn, tracker, now),
        "reorder": _reorder(conn),
    }
