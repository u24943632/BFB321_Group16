"""Read-only reporting endpoints: page data, live KPIs, alerts and reorder advice.

    GET  /api/app-data          everything the pages show (JSON)
    GET  /api/app-data.js       the same, as ``window.SG_DATA = {...}`` for a <script> tag
    GET  /api/metrics/summary   live dashboard KPIs calculated from MySQL
    GET  /api/metrics/kpis      before (paper baseline) vs after (pilot) comparison
    GET  /api/alerts            boxes over their stage-dwell limit (?severity=)
    GET  /api/reorder           EOQ / safety stock / reorder point per product (?status=)
    GET  /api/health            is the app up and can it reach MySQL?
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, Response, jsonify

from database.db import get_db
from models import eoq
from models.tracking import SAST
from routes.api.common import query_choice
from services.app_data import (
    build_app_data, build_summary, current_alerts, kpi_comparison, reorder_plans,
)
from services.errors import NotFoundError
from services.tracker_service import load_tracker

reports_bp = Blueprint("reports_api", __name__, url_prefix="/api")

STOCK_STATUSES = [eoq.STATUS_OK, eoq.STATUS_REORDER, eoq.STATUS_CRITICAL]


@reports_bp.get("/app-data")
def app_data():
    return jsonify(build_app_data(get_db()))


@reports_bp.get("/app-data.js")
def app_data_script():
    """Page data as a script, so pages load it with a plain <script> tag."""
    payload = json.dumps(build_app_data(get_db())).replace("</", "<\\/")
    response = Response(f"window.SG_DATA = {payload};\n", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"  # always show the current database
    return response


@reports_bp.get("/metrics/summary")
def metrics_summary():
    return jsonify({"ok": True, "summary": build_summary(get_db())})


@reports_bp.get("/metrics/kpis")
def metrics_kpis():
    conn = get_db()
    now = datetime.now(SAST)
    kpis = kpi_comparison(conn, load_tracker(conn, clock=lambda: now), now)
    if kpis is None:
        raise NotFoundError("No active pilot period, so there is nothing to compare yet.")
    return jsonify({"ok": True, "kpis": kpis})


@reports_bp.get("/alerts")
def list_alerts():
    severity = query_choice("severity", ["warning", "critical"])
    conn = get_db()
    now = datetime.now(SAST)
    items = current_alerts(conn, load_tracker(conn, clock=lambda: now), now)
    if severity:
        items = [a for a in items if a["severity"] == severity]
    return jsonify({"ok": True, "items": items,
                    "filters": {"severity": severity} if severity else {}})


@reports_bp.get("/reorder")
def list_reorder_plans():
    status = query_choice("status", STOCK_STATUSES)
    items = [p for p in reorder_plans(get_db()) if status is None or p["status"] == status]
    return jsonify({"ok": True, "items": items,
                    "filters": {"status": status} if status else {}})


@reports_bp.get("/health")
def health():
    with get_db().cursor() as cur:
        cur.execute("SELECT 1 AS up")
        cur.fetchone()
    return jsonify({"ok": True, "database": "up"})
