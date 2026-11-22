"""Box endpoints: list, register, read, correct, move between stages, scan, reprint.

    GET    /api/boxes                         list boxes (filters + pagination)
    POST   /api/boxes                         register a box                -> 201
    GET    /api/boxes/<box_id>                one box with history, alert, incidents, audit
    PATCH  /api/boxes/<box_id>                correct batch/quantity and/or record location
    POST   /api/boxes/<box_id>/moves          move the box to another stage -> 201
    POST   /api/boxes/<box_id>/label-reprints reprint a damaged label       -> 201
    POST   /api/scans                         handle a raw QR scan at a station

Every write names the acting ``username`` in its JSON body and runs the
process rules in :mod:`models.tracking` inside one transaction
(:class:`services.tracker_service.TrackerSession`).
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify

from database.db import get_db
from models import repository
from models.tracking import MAX_QUANTITY, SAST, CaptureMethod, Stage
from routes.api.common import (
    int_field, json_body, paged_response, pagination, query_bool, query_choice, query_text,
    text_field,
)
from services.app_data import box_detail_to_dict, box_summary_to_dict, box_to_dict
from services.errors import InvalidInputError
from services.tracker_service import TrackerSession, load_tracker

boxes_bp = Blueprint("boxes_api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)

STAGE_CODES = [s.value for s in Stage]
CAPTURE_METHODS = [m.value for m in CaptureMethod]


def _stage(data: dict, key: str = "to_stage") -> Stage:
    value = text_field(data, key)
    if value not in STAGE_CODES:
        raise InvalidInputError(f"'{key}' must be one of: {', '.join(STAGE_CODES)}.")
    return Stage(value)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@boxes_bp.get("/boxes")
def list_boxes():
    """Boxes filtered by stage, product, location, batch or text, newest first."""
    page, per_page = pagination()
    filters = {
        "stage": query_choice("stage", STAGE_CODES),
        "sku": (query_text("sku", max_length=3) or "").upper() or None,
        "location": (query_text("location", max_length=12) or "").upper() or None,
        "batch_no": (query_text("batch_no", max_length=20) or "").upper() or None,
        "q": query_text("q", max_length=40),
        "in_process": query_bool("in_process"),
        "sort": query_choice("sort", repository.BOX_SORTS, default="newest"),
    }
    rows, total = repository.query_boxes(
        get_db(), stage=filters["stage"], sku=filters["sku"], location=filters["location"],
        batch_no=filters["batch_no"], search=filters["q"], in_process=filters["in_process"],
        sort=filters["sort"], limit=per_page, offset=(page - 1) * per_page)
    return jsonify(paged_response([box_summary_to_dict(r) for r in rows], total, page,
                                  per_page, filters))


@boxes_bp.get("/boxes/<box_id>")
def get_box(box_id: str):
    """Status lookup: the box, its full stage history, alert, incidents and audit trail."""
    conn = get_db()
    now = datetime.now(SAST)
    tracker = load_tracker(conn, clock=lambda: now)
    box = tracker.get(box_id)  # UnknownBoxError -> 404
    return jsonify({"ok": True, "box": box_detail_to_dict(conn, tracker, box, now)})


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@boxes_bp.post("/boxes")
def register_box():
    data = json_body(allowed={"sku", "batch_no", "quantity", "location"})
    sku = text_field(data, "sku", max_length=3)
    batch_no = text_field(data, "batch_no", max_length=20)
    quantity = int_field(data, "quantity", minimum=1, maximum=MAX_QUANTITY)
    location = text_field(data, "location", max_length=12)
    with TrackerSession(get_db()) as session:
        box = session.tracker.register_box(sku, batch_no, quantity,
                                           session.user(text_field(data, "username")), location)
    log.info("Registered %s", box.box_id)
    return jsonify({"ok": True, "box": box_to_dict(box)}), 201


@boxes_bp.patch("/boxes/<box_id>")
def update_box(box_id: str):
    """Correct ``batch_no`` / ``quantity`` (needs ``reason``) and/or check in at ``location``."""
    data = json_body(allowed={"batch_no", "quantity", "reason", "location"})
    batch_no = text_field(data, "batch_no", max_length=20) if "batch_no" in data else None
    quantity = (int_field(data, "quantity", minimum=1, maximum=MAX_QUANTITY)
                if "quantity" in data else None)
    location = text_field(data, "location", max_length=12) if "location" in data else None
    if batch_no is None and quantity is None and location is None:
        raise InvalidInputError("Send at least one of: batch_no, quantity, location.")
    with TrackerSession(get_db()) as session:
        user = session.user(text_field(data, "username"))
        if batch_no is not None or quantity is not None:
            session.tracker.correct_box(box_id, user, text_field(data, "reason", min_length=5),
                                        batch_no=batch_no, quantity=quantity)
        if location is not None:
            session.tracker.record_location(box_id, user, location)
        box = session.tracker.get(box_id)
    log.info("Updated %s", box.box_id)
    return jsonify({"ok": True, "box": box_to_dict(box)})


@boxes_bp.post("/boxes/<box_id>/moves")
def move_box(box_id: str):
    """Create a status event: move the box to ``to_stage`` (rules enforced)."""
    data = json_body(allowed={"to_stage", "location", "method", "note"})
    to_stage = _stage(data)
    location = text_field(data, "location", max_length=12)
    method = text_field(data, "method", required=False) or CaptureMethod.SCAN.value
    if method not in CAPTURE_METHODS:
        raise InvalidInputError(f"'method' must be one of: {', '.join(CAPTURE_METHODS)}.")
    note = text_field(data, "note", required=False)
    with TrackerSession(get_db()) as session:
        user = session.user(text_field(data, "username"))
        event = session.tracker.move(box_id, to_stage, user, location,
                                     method=CaptureMethod(method), note=note)
        box = session.tracker.get(event.box_id)
    log.info("Moved %s to %s", box.box_id, event.to_stage.value)
    return jsonify({"ok": True, "box": box_to_dict(box)}), 201


@boxes_bp.post("/boxes/<box_id>/label-reprints")
def reprint_label(box_id: str):
    """Reprint a damaged label: same box ID, label version + 1."""
    data = json_body(allowed={"reason"})
    reason = text_field(data, "reason", min_length=5)
    with TrackerSession(get_db()) as session:
        box = session.tracker.reprint_label(box_id, session.user(text_field(data, "username")),
                                            reason)
    log.info("Reprinted label for %s (v%s)", box.box_id, box.label_version)
    return jsonify({"ok": True, "box": box_to_dict(box)}), 201


@boxes_bp.post("/scans")
def scan():
    """Handle a raw QR code read at a station.

    The tracker decides the outcome: accepted move or check-in, duplicate scan
    ignored, rule violation rejected, or a damaged / unknown / suspected
    duplicate label (which opens an incident). The outcome is always 200;
    ``outcome`` tells the scanner app what to show.
    """
    data = json_body(allowed={"code", "location", "to_stage"})
    code = text_field(data, "code", max_length=60)
    location = text_field(data, "location", max_length=12)
    to_stage = _stage(data) if data.get("to_stage") else None
    with TrackerSession(get_db()) as session:
        result = session.tracker.scan(code, session.user(text_field(data, "username")),
                                      location, to_stage)
    log.info("Scan %r at %s: %s", code, location, result.outcome.value)
    return jsonify({
        "ok": result.ok, "outcome": result.outcome.value, "message": result.message,
        "box": box_to_dict(result.box) if result.box else None,
        "incident_id": result.incident.incident_id if result.incident else None,
    })
