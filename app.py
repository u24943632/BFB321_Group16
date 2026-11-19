"""Simply Garlic box tracker - Flask application entry point.

    flask --app app init-db     # create the MySQL tables and load seed data
    flask --app app run         # serve on http://127.0.0.1:5000

Layout: routes/ (HTTP), models/ (rules + SQL queries), services/ (glue),
database/ (connection, schema.sql, seed), templates/ and static/ (frontend).
"""

from __future__ import annotations

import logging

from flask import Flask

from config import Config
from database import db
from routes.api import api_bp
from routes.pages import pages_bp


def create_app(config_object: type = Config) -> Flask:
    """Application factory: build and configure the Flask app."""
    app = Flask(__name__)
    app.config.from_object(config_object)
    logging.basicConfig(level=app.config["LOG_LEVEL"],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
