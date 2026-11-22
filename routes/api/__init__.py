"""JSON REST API, one blueprint per resource (all under /api).

    boxes.py      boxes, stage moves, scans, label reprints   (boxes_api)
    incidents.py  exceptions: misplaced boxes, label/QC/dispatch issues  (incidents_api)
    products.py   product catalogue, categories, reorder policies  (products_api)
    reports.py    page data, live KPIs, alerts, reorder advice, health  (reports_api)
    common.py     request validation, pagination and the response envelope
"""

from routes.api.boxes import boxes_bp
from routes.api.incidents import incidents_bp
from routes.api.products import products_bp
from routes.api.reports import reports_bp

API_BLUEPRINTS = (boxes_bp, incidents_bp, products_bp, reports_bp)
