"""Simply Garlic box tracker - Flask application entry point.

    flask --app app init-db     # create the MySQL tables and load seed data
    flask --app app run         # serve on http://127.0.0.1:5000

Set APP_ENV to development (default), testing or production (see config.py).

Layout: routes/ (HTTP: pages, JSON API blueprints, error handlers),
models/ (process rules + SQL queries), services/ (glue between them),
database/ (connection, schema.sql, seed), templates/ and static/ (frontend).
"""

from __future__ import annotations

import logging
import time

from flask import Flask, g, request

from config import Config, get_config
from database import db
from routes.api import API_BLUEPRINTS
from routes.errors import register_error_handlers
from routes.pages import pages_bp


def create_app(config_object: type[Config] | str | None = None) -> Flask:
    """Application factory: build and configure the Flask app.

    Args:
        config_object: a Config class, an environment name, or None for APP_ENV.
    """
    config_class = (config_object if isinstance(config_object, type)
                    else get_config(config_object))
    if hasattr(config_class, "validate"):
        config_class.validate()

    app = Flask(__name__)
    app.config.from_object(config_class)
    app.json.sort_keys = False  # keep fields in the documented order
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(app.config["LOG_LEVEL"])  # the latest app's level wins

    db.init_app(app)
    app.register_blueprint(pages_bp)
    for blueprint in API_BLUEPRINTS:
        app.register_blueprint(blueprint)
    register_error_handlers(app)
    _register_request_logging(app)
    return app


def _register_request_logging(app: Flask) -> None:
    """Log every API call with its status code and duration."""
    log = logging.getLogger("api")

    @app.before_request
    def start_timer():
        g.request_started = time.perf_counter()

    @app.after_request
    def log_request(response):
        if request.path.startswith("/api/"):
            started = g.get("request_started", time.perf_counter())
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info("%s %s -> %s (%.0f ms)", request.method, request.full_path.rstrip("?"),
                     response.status_code, elapsed_ms)
        return response


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
