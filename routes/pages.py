"""HTML page routes. Each page loads its data from MySQL via /api/app-data.js."""

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)

# URL path -> template file
PAGES = {
    "/": "index.html",
    "/boxes": "boxes.html",
    "/scan": "scan.html",
    "/register": "register.html",
    "/reorder": "reorder.html",
    "/metrics": "metrics.html",
}


def _make_view(template: str):
    def view():
        return render_template(template)
    view.__name__ = f"page_{template.removesuffix('.html')}"
    return view


for _path, _template in PAGES.items():
    pages_bp.add_url_rule(_path, view_func=_make_view(_template))
