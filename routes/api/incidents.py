"""Incident (exception) endpoints: lost/misplaced boxes, label, QC and dispatch problems.

    GET    /api/incidents             list incidents (filters + pagination)
    POST   /api/incidents             log an incident            -> 201
    GET    /api/incidents/<id>        one incident
    PATCH  /api/incidents/<id>        link a box and/or close the incident

Closing an incident with ``found_location`` also records where the box was
found, so the box board shows its new location straight away.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from database.db import get_db
from models import repository
from models.tracking import IncidentKind
from routes.api.common import (
    json_body, paged_response, pagination, query_choice, query_text, text_field,
)
from services.app_data import box_to_dict, incident_row_to_dict, incident_to_dict
from services.errors import InvalidInputError, NotFoundError
from services.tracker_service import TrackerSession, load_tracker

incidents_bp = Blueprint("incidents_api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)

KIND_CODES = [k.value for k in IncidentKind]


def _find(tracker, incident_id: str):
    incident = next((i for i in tracker.incidents if i.incident_id == incident_id), None)
    if incident is None:
        raise NotFoundError(f"No incident '{incident_id}'.")
    return incident


@incidents_bp.get("/incidents")
def list_incidents():
    page, per_page = pagination()
    filters = {
        "status": query_choice("status", ["open", "closed"]),
        "kind": query_choice("kind", KIND_CODES),
        "box_id": (query_text("box_id", max_length=20) or "").upper() or None,
    }
    rows, total = repository.query_incidents(get_db(), **filters, limit=per_page,
                                             offset=(page - 1) * per_page)
    return jsonify(paged_response([incident_row_to_dict(r) for r in rows], total, page,
                                  per_page, filters))


@incidents_bp.get("/incidents/<incident_id>")
def get_incident(incident_id: str):
    incident = _find(load_tracker(get_db()), incident_id)
    return jsonify({"ok": True, "incident": incident_to_dict(incident)})


@incidents_bp.post("/incidents")
def open_incident():
    data = json_body(allowed={"kind", "box_id", "note"})
    kind = text_field(data, "kind")
    if kind not in KIND_CODES:
        raise InvalidInputError(f"'kind' must be one of: {', '.join(KIND_CODES)}.")
    box_id = text_field(data, "box_id", required=False, max_length=20) or None
    note = text_field(data, "note", min_length=5)
    with TrackerSession(get_db()) as session:
        incident = session.tracker.open_incident(
            IncidentKind(kind), session.user(text_field(data, "username")), box_id=box_id,
            note=note)
    log.info("Opened %s (%s)", incident.incident_id, incident.kind.value)
    return jsonify({"ok": True, "incident": incident_to_dict(incident)}), 201


@incidents_bp.patch("/incidents/<incident_id>")
def update_incident(incident_id: str):
    """``{"box_id": ...}`` links a box; ``{"status": "closed", "resolution": ...,
    "found_location": ...}`` closes it. Both may be sent together."""
    data = json_body(allowed={"box_id", "status", "resolution", "found_location"})
    box_id = text_field(data, "box_id", max_length=20) if "box_id" in data else None
    closing = "status" in data
    if closing and data["status"] != "closed":
        raise InvalidInputError("'status' can only be set to 'closed'.")
    if box_id is None and not closing:
        raise InvalidInputError("Send 'box_id' to link a box and/or 'status': 'closed' to close.")
    with TrackerSession(get_db()) as session:
        user = session.user(text_field(data, "username"))
        _find(session.tracker, incident_id)
        if box_id is not None:
            session.tracker.link_incident(incident_id, box_id, user)
        if closing:
            session.tracker.close_incident(
                incident_id, user, text_field(data, "resolution", min_length=5),
                found_location=text_field(data, "found_location", required=False,
                                          max_length=12) or None)
        incident = _find(session.tracker, incident_id)
        box = session.tracker.get(incident.box_id) if incident.box_id else None
    log.info("Updated %s", incident_id)
    return jsonify({"ok": True, "incident": incident_to_dict(incident),
                    "box": box_to_dict(box) if box else None})
