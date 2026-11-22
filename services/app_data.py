"""Build the data the pages render (``window.SG_DATA``) from MySQL.

Everything shown on screen - products, stages, locations, thresholds, role
permissions, boxes, incidents, audit trail, KPIs and reorder advice - is read
from the database on each request. Only the stage graph (allowed transitions
and prerequisites) comes from :mod:`models.tracking`, because it is process
logic rather than data.

The serialisers here are also used by the API so a box, incident, product or
reorder plan has the same JSON shape everywhere. :func:`build_summary` and
:func:`current_alerts` calculate the dashboard KPIs on the server.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from models import alerts, eoq, metrics, repository
from models.products import catalogue_from_rows
from models.tracking import (
    ALLOWED_TRANSITIONS, CATALOGUE_ROLES, PREREQUISITES, SAST, AuditEntry, Box, BoxTracker,
    Incident, Stage,
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


def box_summary_to_dict(row: dict) -> dict:
    """A ``repository.query_boxes`` row (no history) as JSON."""
    return {
        "box_id": row["box_id"], "sku": row["sku"], "product_name": row["product_name"],
        "batch_no": row["batch_no"], "quantity": row["quantity"],
        "stage": row["current_stage_code"], "stage_label": row["stage_label"],
        "stage_entered_at": iso(repository.from_db_time(row["stage_entered_at"])),
        "location": row["current_location_code"],
        "location_updated_at": iso(repository.from_db_time(row["location_updated_at"])),
        "label_version": row["label_version"],
        "created_at": iso(repository.from_db_time(row["created_at"])),
    }


def incident_row_to_dict(row: dict) -> dict:
    """A ``repository.query_incidents`` row as JSON (same keys as incident_to_dict)."""
    return {
        "id": row["incident_id"], "kind": row["kind_code"], "kind_label": row["kind_label"],
        "box_id": row["box_id"], "note": row["note"],
        "opened_at": iso(repository.from_db_time(row["opened_at"])),
        "opened_by": row["opened_by_username"],
        "closed_at": iso(repository.from_db_time(row["closed_at"])),
        "closed_by": row["closed_by_username"], "resolution": row["resolution"],
    }


def product_to_dict(row: dict) -> dict:
    """A products JOIN product_categories row as JSON."""
    return {
        "sku": row["sku"], "name": row["product_name"], "category": row["category_name"],
        "perishable": bool(row["is_perishable"]), "unit_cost": float(row["unit_cost"]),
        "active": bool(row["is_active"]),
        "created_at": iso(repository.from_db_time(row.get("created_at"))),
        "updated_at": iso(repository.from_db_time(row.get("updated_at"))),
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
                      "perishable": bool(p["is_perishable"]), "active": bool(p["is_active"]),
                      "unit_cost": float(p["unit_cost"])}
                     for p in repository.fetch_products(conn)],
        "categories": [{"name": c["category_name"], "perishable": bool(c["is_perishable"])}
                       for c in repository.fetch_categories(conn)],
        "catalogue_roles": sorted(r.value for r in CATALOGUE_ROLES),
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


def kpi_comparison(conn, tracker: BoxTracker, now: datetime) -> Optional[dict]:
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


def reorder_plan_to_dict(row: dict) -> dict:
    """Run the EOQ / safety stock / reorder point model on one reorder_policies row."""
    inputs = {
        "annual_demand": row["annual_demand"], "order_cost": float(row["order_cost"]),
        "holding_cost": float(row["holding_cost"]), "unit_cost": float(row["unit_cost"]),
        "daily_demand_std": float(row["daily_demand_std"]),
        "lead_time_days": row["lead_time_days"],
    }
    plan = eoq.plan_reorder(row["sku"], **inputs, on_hand=row["on_hand_qty"],
                            service_level=float(row["service_level"]),
                            working_days=row["working_days"])
    return {"sku": row["sku"], "name": row["product_name"], "active": bool(row["is_active"]),
            **inputs, "service_level": float(row["service_level"]),
            "working_days": row["working_days"],
            "avg_daily_demand": row["annual_demand"] / row["working_days"],
            "stock_value": row["on_hand_qty"] * float(row["unit_cost"]),
            **{k: getattr(plan, k) for k in ("eoq", "orders_per_year", "total_annual_cost",
                                             "safety_stock", "reorder_point", "on_hand",
                                             "status")}}


def reorder_plans(conn) -> list[dict]:
    """EOQ, safety stock and reorder point per product from reorder_policies."""
    return [reorder_plan_to_dict(row) for row in repository.fetch_reorder_policies(conn)]


def current_alerts(conn, tracker: BoxTracker, now: datetime) -> list[dict]:
    """Stage-dwell alerts using the thresholds stored in the stages table."""
    thresholds = alerts.thresholds_from_rows(repository.fetch_stages(conn))
    catalogue = catalogue_from_rows(repository.fetch_products(conn), active_only=False)
    return [{"box_id": a.box_id, "sku": a.sku, "stage": a.stage.value,
             "severity": a.severity.name.lower(),
             "dwell_minutes": round(a.dwell.total_seconds() / 60),
             "limit_minutes": round(a.threshold.total_seconds() / 60),
             "location": a.location, "message": a.message}
            for a in alerts.evaluate_alerts(tracker.boxes, now, thresholds, catalogue)]


def box_detail_to_dict(conn, tracker: BoxTracker, box: Box, now: datetime) -> dict:
    """One box with its full history, current alert, incidents and audit trail."""
    alert = next((a for a in current_alerts(conn, tracker, now) if a["box_id"] == box.box_id),
                 None)
    return {
        **box_to_dict(box),
        "stage": box.stage.value,
        "stage_entered_at": iso(box.stage_entered_at),
        "minutes_in_stage": None if box.is_terminal
        else round(box.dwell(now).total_seconds() / 60),
        "severity": alert["severity"] if alert else "ok",
        "complete_trace": box.has_complete_trace(),
        "incidents": [incident_to_dict(i) for i in tracker.incidents if i.box_id == box.box_id],
        "audit": [audit_to_dict(e) for e in tracker.audit.for_box(box.box_id)],
    }


def build_summary(conn, now: Optional[datetime] = None) -> dict:
    """Live operational KPIs for the dashboard, calculated from MySQL."""
    now = now or datetime.now(SAST)
    tracker = load_tracker(conn, clock=lambda: now)
    boxes = tracker.boxes
    in_process = [b for b in boxes if not b.is_terminal]
    alert_rows = current_alerts(conn, tracker, now)
    day_ago = now - timedelta(days=1)
    plans = reorder_plans(conn)
    dwell_hours = [b.dwell(now).total_seconds() / 3600 for b in in_process]
    dispatched = [b for b in boxes if b.stage is Stage.DISPATCHED]
    return {
        "generated_at": iso(now),
        "boxes_total": len(boxes),
        "boxes_in_process": len(in_process),
        "boxes_by_stage": {s.value: sum(1 for b in boxes if b.stage is s) for s in Stage},
        "ready_for_dispatch": sum(1 for b in boxes if b.stage is Stage.READY_FOR_DISPATCH),
        "dispatched_24h": sum(1 for b in dispatched if b.stage_entered_at >= day_ago),
        "alerts_critical": sum(1 for a in alert_rows if a["severity"] == "critical"),
        "alerts_warning": sum(1 for a in alert_rows if a["severity"] == "warning"),
        "open_incidents": len(tracker.open_incidents()),
        "mean_hours_in_current_stage": (round(sum(dwell_hours) / len(dwell_hours), 2)
                                        if dwell_hours else 0.0),
        # Share of dispatched boxes whose history shows every required stage.
        "traceability_coverage_pct": (round(100 * sum(b.has_complete_trace() for b in dispatched)
                                            / len(dispatched), 1) if dispatched else 100.0),
        "active_products": sum(1 for p in repository.fetch_products(conn) if p["is_active"]),
        "stock_on_hand_boxes": sum(p["on_hand"] for p in plans),
        "stock_value": round(sum(p["stock_value"] for p in plans), 2),
        "items_to_reorder": sum(1 for p in plans if p["status"] != eoq.STATUS_OK),
        "items_critical": sum(1 for p in plans if p["status"] == eoq.STATUS_CRITICAL),
    }


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
        "kpis": kpi_comparison(conn, tracker, now),
        "reorder": reorder_plans(conn),
    }
