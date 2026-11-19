"""JSON API backed by MySQL.

Reads:
    GET  /api/app-data             everything the pages show (JSON)
    GET  /api/app-data.js          the same, as ``window.SG_DATA = {...}`` for <script>

Writes (body is JSON and always includes the acting ``username``):
    POST /api/boxes                       register a box
    POST /api/boxes/<box_id>/moves        move a box to another stage
    POST /api/incidents                   log an exception
    POST /api/incidents/<id>/link         attach a box to an incident
    POST /api/incidents/<id>/close        close an incident

Each write runs the rules in :mod:`models.tracking` inside one database
transaction (:class:`services.tracker_service.TrackerSession`).
"""

from __future__ import annotations

import json
import logging

from flask import Blueprint, Response, jsonify, request

from database.db import get_db
from models.products import UnknownProductError
from models.tracking import (
    CaptureMethod, InvalidTransitionError, PermissionDeniedError, TrackingError, UnknownBoxError,
)
from services.app_data import box_to_dict, build_app_data, incident_to_dict
from services.tracker_service import TrackerSession

api_bp = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 500  # matches VARCHAR(500) note / resolution columns


class BadRequest(Exception):
    """The request body is missing a field or has the wrong type."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("Send a JSON object in the request body.")
    return data


def _text(data: dict, key: str, *, required: bool = True) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise BadRequest(f"'{key}' must be text.")
    value = value.strip()
    if required and not value:
        raise BadRequest(f"'{key}' is required.")
    if len(value) > MAX_TEXT_LENGTH:
        raise BadRequest(f"'{key}' may be at most {MAX_TEXT_LENGTH} characters.")
    return value


def _status_for(error: Exception) -> int:
    """HTTP status for a rejected request."""
    if isinstance(error, UnknownBoxError):
        return 404
    if isinstance(error, PermissionDeniedError):
        return 403
    if isinstance(error, InvalidTransitionError):
        return 409
    return 400  # bad input: validation, malformed box ID, unknown user/product


@api_bp.errorhandler(BadRequest)
@api_bp.errorhandler(TrackingError)
@api_bp.errorhandler(UnknownProductError)
def _handle_rule_error(error: Exception):
    message = error.args[0] if error.args else str(error)
    status = _status_for(error)
    log.info("Rejected %s %s: %s", request.method, request.path, message)
    return jsonify({"ok": False, "error": message}), status


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@api_bp.get("/app-data")
def app_data():
    return jsonify(build_app_data(get_db()))


@api_bp.get("/app-data.js")
def app_data_script():
    """Page data as a script, so pages load it with a plain <script> tag."""
    payload = json.dumps(build_app_data(get_db())).replace("</", "<\\/")
    response = Response(f"window.SG_DATA = {payload};\n", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"  # always show the current database
    return response


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@api_bp.post("/boxes")
def register_box():
    data = _body()
    quantity = data.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise BadRequest("'quantity' must be a whole number.")
    with TrackerSession(get_db()) as session:
        box = session.tracker.register_box(
            _text(data, "sku"), _text(data, "batch_no"), quantity,
            session.user(_text(data, "username")), _text(data, "location"))
    log.info("Registered %s", box.box_id)
    return jsonify({"ok": True, "box": box_to_dict(box)}), 201


@api_bp.post("/boxes/<box_id>/moves")
def move_box(box_id: str):
    data = _body()
    method = _text(data, "method", required=False) or CaptureMethod.SCAN.value
    if method not in {m.value for m in CaptureMethod}:
        raise BadRequest("'method' must be 'scan' or 'manual'.")
    with TrackerSession(get_db()) as session:
        user = session.user(_text(data, "username"))
        to_stage = _text(data, "to_stage")
        try:
            event = session.tracker.move(box_id, to_stage, user, _text(data, "location"),
                                         method=CaptureMethod(method),
                                         note=_text(data, "note", required=False))
        except ValueError as exc:  # not a known stage code
            if isinstance(exc, TrackingError):
                raise
            raise BadRequest(f"Unknown stage '{to_stage}'.") from None
        box = session.tracker.get(event.box_id)
    log.info("Moved %s to %s", box.box_id, event.to_stage.value)
    return jsonify({"ok": True, "box": box_to_dict(box)})


@api_bp.post("/incidents")
def open_incident():
    data = _body()
    box_id = data.get("box_id") or None
    with TrackerSession(get_db()) as session:
        user = session.user(_text(data, "username"))
        kind = _text(data, "kind")
        try:
            incident = session.tracker.open_incident(kind, user, box_id=box_id,
                                                     note=_text(data, "note"))
        except ValueError as exc:
            if isinstance(exc, TrackingError):
                raise
            raise BadRequest(f"Unknown incident kind '{kind}'.") from None
    log.info("Opened %s (%s)", incident.incident_id, incident.kind.value)
    return jsonify({"ok": True, "incident": incident_to_dict(incident)}), 201


@api_bp.post("/incidents/<incident_id>/link")
def link_incident(incident_id: str):
    data = _body()
    with TrackerSession(get_db()) as session:
        incident = session.tracker.link_incident(incident_id, _text(data, "box_id"),
                                                 session.user(_text(data, "username")))
    return jsonify({"ok": True, "incident": incident_to_dict(incident)})


@api_bp.post("/incidents/<incident_id>/close")
def close_incident(incident_id: str):
    data = _body()
    with TrackerSession(get_db()) as session:
        incident = session.tracker.close_incident(
            incident_id, session.user(_text(data, "username")), _text(data, "resolution"),
            found_location=_text(data, "found_location", required=False) or None)
        box = session.tracker.get(incident.box_id) if incident.box_id else None
    return jsonify({"ok": True, "incident": incident_to_dict(incident),
                    "box": box_to_dict(box) if box else None})
