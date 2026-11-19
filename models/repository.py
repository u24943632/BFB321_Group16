"""MySQL queries for the tables in docs/ERD.md.

Every function takes an open PyMySQL connection and uses ``%s`` placeholders,
so values are always sent as parameters, never joined into the SQL text.
Table and column names are exactly those in ``database/schema.sql``.

Rows are turned into the domain objects of :mod:`models.tracking`
(``Box``, ``StatusEvent``, ``Incident``, ``AuditEntry``) so the process rules
run on database data unchanged. The functions do not commit; the caller
(:mod:`services.tracker_service`) owns the transaction.

Timestamps are stored as UTC ``DATETIME(6)`` and returned as SAST-aware
datetimes, which the tracking rules require.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .tracking import (
    SAST, AuditEntry, Box, CaptureMethod, Incident, IncidentKind, Role, Stage, StatusEvent,
    User, parse_box_id,
)


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------

def to_db_time(value: Optional[datetime]) -> Optional[datetime]:
    """Aware datetime -> naive UTC for a DATETIME column."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value else None


def from_db_time(value: Optional[datetime]) -> Optional[datetime]:
    """Naive UTC from a DATETIME column -> aware SAST datetime."""
    return value.replace(tzinfo=timezone.utc).astimezone(SAST) if value else None


def _fetch_all(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _execute(conn, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        return cur.execute(sql, params)


# ---------------------------------------------------------------------------
# Lookup tables: product_categories, products, roles, users, locations,
# stages, stage_permissions, incident_kinds
# ---------------------------------------------------------------------------

def fetch_products(conn) -> list[dict]:
    """Products with their category (products JOIN product_categories)."""
    return _fetch_all(conn, """
        SELECT p.product_id, p.sku, p.product_name, p.unit_cost, p.is_active,
               c.category_name, c.is_perishable
          FROM products p
          JOIN product_categories c ON c.category_id = p.category_id
         ORDER BY p.product_id""")


def fetch_roles(conn) -> list[dict]:
    return _fetch_all(conn, """
        SELECT role_code, role_label, is_floor_role, can_register, can_release_hold
          FROM roles
         ORDER BY FIELD(role_code, 'operator', 'qc_inspector', 'supervisor', 'executive'),
                  role_code""")


def fetch_active_users(conn) -> list[dict]:
    return _fetch_all(conn, """
        SELECT user_id, username, full_name, role_code
          FROM users
         WHERE is_active = TRUE
         ORDER BY user_id""")


def fetch_user_ids(conn) -> dict[str, int]:
    """Map every username (active or not) to its user_id."""
    return {r["username"]: r["user_id"]
            for r in _fetch_all(conn, "SELECT user_id, username FROM users")}


def find_active_user(conn, username: str) -> Optional[User]:
    """The active user with this username, or None."""
    rows = _fetch_all(conn, """
        SELECT username, role_code FROM users
         WHERE username = %s AND is_active = TRUE""", (username,))
    return User(rows[0]["username"], Role(rows[0]["role_code"])) if rows else None


def fetch_locations(conn) -> list[dict]:
    return _fetch_all(conn, """
        SELECT location_code, location_name, is_active
          FROM locations
         ORDER BY created_at, location_code""")


def fetch_stages(conn) -> list[dict]:
    """Stages in process order: the main flow first, then the exception stages."""
    return _fetch_all(conn, """
        SELECT stage_code, stage_label, flow_position, is_terminal, requires_note,
               default_location_code, warning_minutes, critical_minutes
          FROM stages
         ORDER BY flow_position IS NULL, flow_position, stage_code""")


def fetch_stage_permissions(conn) -> list[dict]:
    return _fetch_all(conn, """
        SELECT stage_code, role_code
          FROM stage_permissions
         ORDER BY stage_code, role_code""")


def fetch_incident_kinds(conn) -> list[dict]:
    return _fetch_all(conn, """
        SELECT kind_code, kind_label, is_tracking_incident
          FROM incident_kinds
         ORDER BY created_at, kind_code""")


# ---------------------------------------------------------------------------
# boxes + status_events
# ---------------------------------------------------------------------------

def _status_event_from_row(row: dict) -> StatusEvent:
    return StatusEvent(
        box_id=row["box_id"],
        from_stage=Stage(row["from_stage_code"]) if row["from_stage_code"] else None,
        to_stage=Stage(row["to_stage_code"]),
        at=from_db_time(row["occurred_at"]),
        username=row["username"],
        role=Role(row["role_code"]),
        location=row["location_code"],
        method=CaptureMethod(row["capture_method"]),
        note=row["note"],
    )


def fetch_boxes(conn) -> list[Box]:
    """Every box with its full status history, oldest registration first."""
    box_rows = _fetch_all(conn, """
        SELECT b.box_id, p.sku, b.batch_no, b.quantity, b.created_at,
               b.current_location_code, b.location_updated_at, b.label_version
          FROM boxes b
          JOIN products p ON p.product_id = b.product_id
         ORDER BY b.created_at, b.box_id""")
    event_rows = _fetch_all(conn, """
        SELECT e.box_id, e.from_stage_code, e.to_stage_code, e.occurred_at, u.username,
               e.role_code, e.location_code, e.capture_method, e.note
          FROM status_events e
          JOIN users u ON u.user_id = e.user_id
         ORDER BY e.box_id, e.occurred_at, e.event_id""")

    history: dict[str, list[StatusEvent]] = {}
    for row in event_rows:
        history.setdefault(row["box_id"], []).append(_status_event_from_row(row))

    return [Box(
        box_id=row["box_id"],
        sku=row["sku"],
        batch_no=row["batch_no"],
        quantity=row["quantity"],
        created_at=from_db_time(row["created_at"]),
        history=history.get(row["box_id"], []),
        location=row["current_location_code"],
        location_updated_at=from_db_time(row["location_updated_at"]),
        label_version=row["label_version"],
    ) for row in box_rows if row["box_id"] in history]


def insert_box(conn, box: Box, registered_by: int) -> None:
    """Insert a newly registered box. Its status_events are inserted separately."""
    parsed = parse_box_id(box.box_id)
    _execute(conn, """
        INSERT INTO boxes (box_id, product_id, batch_no, quantity, production_date, sequence_no,
                           current_stage_code, current_location_code, location_updated_at,
                           label_version, registered_by, created_at)
        VALUES (%s, (SELECT product_id FROM products WHERE sku = %s), %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)""",
        (box.box_id, box.sku, box.batch_no, box.quantity, parsed.production_date,
         parsed.sequence, box.stage.value, box.location, to_db_time(box.location_updated_at),
         box.label_version, registered_by, to_db_time(box.created_at)))


def update_box_state(conn, box: Box) -> None:
    """Save a box's current stage, location and label version."""
    _execute(conn, """
        UPDATE boxes
           SET current_stage_code = %s, current_location_code = %s,
               location_updated_at = %s, label_version = %s
         WHERE box_id = %s""",
        (box.stage.value, box.location, to_db_time(box.location_updated_at),
         box.label_version, box.box_id))


def insert_status_event(conn, event: StatusEvent, user_id: int) -> None:
    _execute(conn, """
        INSERT INTO status_events (box_id, from_stage_code, to_stage_code, occurred_at, user_id,
                                   role_code, location_code, capture_method, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (event.box_id, event.from_stage.value if event.from_stage else None,
         event.to_stage.value, to_db_time(event.at), user_id, event.role.value,
         event.location, event.method.value, event.note))


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------

def fetch_incidents(conn) -> list[Incident]:
    rows = _fetch_all(conn, """
        SELECT i.incident_id, i.kind_code, i.box_id, i.note, i.opened_at,
               opener.username AS opened_by_username, i.closed_at,
               closer.username AS closed_by_username, i.resolution
          FROM incidents i
          JOIN users opener ON opener.user_id = i.opened_by
          LEFT JOIN users closer ON closer.user_id = i.closed_by
         ORDER BY i.opened_at, i.incident_id""")
    return [Incident(
        incident_id=row["incident_id"],
        kind=IncidentKind(row["kind_code"]),
        opened_at=from_db_time(row["opened_at"]),
        opened_by=row["opened_by_username"],
        box_id=row["box_id"],
        note=row["note"],
        closed_at=from_db_time(row["closed_at"]),
        closed_by=row["closed_by_username"],
        resolution=row["resolution"],
    ) for row in rows]


def insert_incident(conn, incident: Incident, user_ids: dict[str, int]) -> None:
    _execute(conn, """
        INSERT INTO incidents (incident_id, kind_code, box_id, note, opened_at, opened_by,
                               closed_at, closed_by, resolution)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (incident.incident_id, incident.kind.value, incident.box_id, incident.note,
         to_db_time(incident.opened_at), user_ids[incident.opened_by],
         to_db_time(incident.closed_at),
         user_ids[incident.closed_by] if incident.closed_by else None, incident.resolution))


def update_incident(conn, incident: Incident, user_ids: dict[str, int]) -> None:
    """Save a link to a box and/or the closing details."""
    _execute(conn, """
        UPDATE incidents
           SET box_id = %s, closed_at = %s, closed_by = %s, resolution = %s
         WHERE incident_id = %s""",
        (incident.box_id, to_db_time(incident.closed_at),
         user_ids[incident.closed_by] if incident.closed_by else None,
         incident.resolution, incident.incident_id))


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------

def lock_audit_log(conn) -> None:
    """Lock the newest audit row until commit.

    Every write appends to the hash chain, so two requests must not both
    read the same last entry. Writers therefore queue on this lock.
    """
    _fetch_all(conn, "SELECT seq FROM audit_log ORDER BY seq DESC LIMIT 1 FOR UPDATE")


def fetch_audit_entries(conn) -> list[AuditEntry]:
    rows = _fetch_all(conn, """
        SELECT a.seq, a.occurred_at, u.username, a.role_code, a.action, a.outcome,
               a.box_id, a.detail, a.prev_hash, a.entry_hash
          FROM audit_log a
          JOIN users u ON u.user_id = a.user_id
         ORDER BY a.seq""")
    return [AuditEntry(
        seq=row["seq"],
        at=from_db_time(row["occurred_at"]),
        username=row["username"],
        role=row["role_code"],
        action=row["action"],
        outcome=row["outcome"],
        box_id=row["box_id"],
        detail=row["detail"],
        prev_hash=row["prev_hash"],
        hash=row["entry_hash"],
    ) for row in rows]


def insert_audit_entry(conn, entry: AuditEntry, user_id: int) -> None:
    _execute(conn, """
        INSERT INTO audit_log (seq, occurred_at, user_id, role_code, action, outcome, box_id,
                               detail, prev_hash, entry_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (entry.seq, to_db_time(entry.at), user_id, entry.role, entry.action, entry.outcome,
         entry.box_id, entry.detail, entry.prev_hash, entry.hash))


# ---------------------------------------------------------------------------
# reorder_policies, kpi_baselines, pilot_periods
# ---------------------------------------------------------------------------

def fetch_reorder_policies(conn) -> list[dict]:
    """Reorder inputs per product (reorder_policies JOIN products)."""
    return _fetch_all(conn, """
        SELECT p.sku, p.unit_cost, r.annual_demand, r.order_cost, r.holding_cost,
               r.daily_demand_std, r.lead_time_days, r.on_hand_qty, r.service_level,
               r.working_days
          FROM reorder_policies r
          JOIN products p ON p.product_id = r.product_id
         ORDER BY p.product_id""")


def fetch_active_pilot(conn) -> Optional[dict]:
    """The current pilot period with its baseline columns (prefixed ``baseline_``)."""
    rows = _fetch_all(conn, """
        SELECT pp.pilot_id, pp.label, pp.starts_at, pp.ends_at, pp.labour_hours_per_day,
               pp.status_lookups_per_day, pp.mean_status_retrieval_seconds,
               kb.label AS baseline_label, kb.period_days AS baseline_period_days,
               kb.boxes_dispatched AS baseline_boxes_dispatched,
               kb.tracking_incidents AS baseline_tracking_incidents,
               kb.investigations AS baseline_investigations,
               kb.investigation_minutes AS baseline_investigation_minutes,
               kb.status_lookups AS baseline_status_lookups,
               kb.mean_status_retrieval_seconds AS baseline_mean_status_retrieval_seconds,
               kb.mean_stage_dwell_hours AS baseline_mean_stage_dwell_hours,
               kb.quality_errors AS baseline_quality_errors,
               kb.dispatch_errors AS baseline_dispatch_errors,
               kb.traced_boxes AS baseline_traced_boxes,
               kb.labour_hours AS baseline_labour_hours
          FROM pilot_periods pp
          JOIN kpi_baselines kb ON kb.baseline_id = pp.baseline_id
         WHERE pp.is_active = TRUE
         ORDER BY pp.starts_at DESC
         LIMIT 1""")
    return rows[0] if rows else None
