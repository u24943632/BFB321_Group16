"""API validation, error handling, logging and config - no database needed.

Bad input is refused before any database work, so these run everywhere.
A database that cannot be reached is simulated with an unused local port.
The happy paths that read and write MySQL are in test_database.py.
"""

import unittest

from app import create_app
from config import CONFIGS, ProductionConfig, TestingConfig, get_config
from routes.api.common import paged_response


class NoDatabaseConfig(TestingConfig):
    DB_HOST = "127.0.0.1"
    DB_PORT = 1  # nothing listens here, so connecting fails fast


class ApiValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(NoDatabaseConfig)
        self.client = self.app.test_client()

    def assertError(self, response, status, fragment):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["ok"], False)
        self.assertEqual(body["status"], status)
        self.assertIn(fragment, body["error"])

    # -- pagination and filters ------------------------------------------

    def test_pagination_values_are_checked(self):
        cases = [("/api/boxes?per_page=0", "per_page"), ("/api/boxes?per_page=101", "per_page"),
                 ("/api/boxes?page=0", "page"), ("/api/products?page=abc", "whole number"),
                 ("/api/incidents?per_page=-5", "per_page")]
        for url, fragment in cases:
            with self.subTest(url=url):
                self.assertError(self.client.get(url), 400, fragment)

    def test_filters_only_accept_known_values(self):
        cases = [("/api/boxes?stage=lost", "stage"),
                 ("/api/boxes?in_process=maybe", "true or false"),
                 ("/api/boxes?sort=random", "sort"), ("/api/incidents?status=pending", "status"),
                 ("/api/incidents?kind=fire", "kind"), ("/api/alerts?severity=low", "severity"),
                 ("/api/reorder?status=LOW", "status"), ("/api/products?active=perhaps", "active")]
        for url, fragment in cases:
            with self.subTest(url=url):
                self.assertError(self.client.get(url), 400, fragment)

    # -- request bodies ----------------------------------------------------

    def test_body_must_be_a_json_object(self):
        self.assertError(self.client.post("/api/boxes", data="sku=BGA"), 400, "JSON object")
        self.assertError(self.client.post("/api/boxes", json=["BGA"]), 400, "JSON object")

    def test_register_box_validation(self):
        good = {"username": "thabo.m", "sku": "BGA", "batch_no": "BGA-01", "quantity": 12,
                "location": "PROD"}
        cases = [
            ({**good, "quantity": "12"}, "whole number"),
            ({**good, "quantity": True}, "whole number"),
            ({**good, "quantity": 0}, "at least 1"),
            ({**good, "quantity": 10000}, "at most 9999"),
            ({k: v for k, v in good.items() if k != "sku"}, "'sku' is required"),
            ({**good, "colour": "red"}, "Unknown field(s): colour"),
            ({**good, "batch_no": 42}, "must be text"),
        ]
        for body, fragment in cases:
            with self.subTest(body=body):
                self.assertError(self.client.post("/api/boxes", json=body), 400, fragment)

    def test_move_and_incident_validation(self):
        move = self.client.post("/api/boxes/SG-BGA-261025-0001-X/moves",
                                json={"to_stage": "lost", "location": "PACK"})
        self.assertError(move, 400, "to_stage")
        method = self.client.post("/api/boxes/SG-BGA-261025-0001-X/moves",
                                  json={"to_stage": "awaiting_qc", "location": "QC-STAGE",
                                        "method": "telepathy"})
        self.assertError(method, 400, "method")
        incident = self.client.post("/api/incidents", json={"kind": "fire", "note": "Smoke seen"})
        self.assertError(incident, 400, "kind")
        close = self.client.patch("/api/incidents/INC-0001", json={"status": "open"})
        self.assertError(close, 400, "closed")
        empty = self.client.patch("/api/boxes/SG-BGA-261025-0001-X", json={})
        self.assertError(empty, 400, "at least one")

    def test_product_and_policy_validation(self):
        product = {"sku": "GRW", "name": "Garlic wraps", "category": "Fresh produce",
                   "unit_cost": 120}
        cases = [
            ({**product, "sku": "G1W"}, "three letters"),
            ({**product, "unit_cost": -1}, "at least 0"),
            ({**product, "unit_cost": "cheap"}, "must be a number"),
            ({**product, "name": "ab"}, "at least 3"),
            ({**product, "reorder_policy": "lots"}, "JSON object"),
            ({**product, "reorder_policy": {"annual_demand": 100}}, "'order_cost' is required"),
            ({**product, "reorder_policy": {"annual_demand": 100, "order_cost": 50,
                                            "holding_cost": 5, "daily_demand_std": 1,
                                            "lead_time_days": 3, "service_level": 1}},
             "less than 1"),
            ({**product, "reorder_policy": {"colour": "red"}}, "Unknown reorder policy field"),
        ]
        for body, fragment in cases:
            with self.subTest(body=body):
                self.assertError(self.client.post("/api/products", json=body), 400, fragment)
        self.assertError(self.client.patch("/api/products/BGA", json={}), 400, "at least one")
        self.assertError(self.client.patch("/api/products/BGA", json={"active": "yes"}), 400,
                         "true or false")
        self.assertError(self.client.patch("/api/products/BGA/reorder-policy",
                                           json={"lead_time_days": 0}), 400, "at least 1")
        self.assertError(self.client.patch("/api/products/BGA/reorder-policy",
                                           json={"order_cost": 0}), 400, "greater than 0")

    # -- central error handlers -------------------------------------------

    def test_unknown_api_url_and_wrong_method_return_json(self):
        self.assertError(self.client.get("/api/does-not-exist"), 404, "does not exist")
        self.assertError(self.client.delete("/api/boxes"), 405, "DELETE is not allowed")

    def test_unknown_page_returns_friendly_html(self):
        response = self.client.get("/no-such-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn("text/html", response.content_type)
        self.assertIn("Page not found", response.get_data(as_text=True))

    def test_database_down_returns_503_and_is_logged(self):
        with self.assertLogs("routes.errors", level="ERROR") as logs:
            response = self.client.get("/api/health")
        self.assertError(response, 503, "database is not reachable")
        self.assertTrue(any("Database unavailable" in line for line in logs.output))

    def test_unexpected_error_is_logged_without_leaking_a_stack_trace(self):
        app = create_app(NoDatabaseConfig)

        @app.get("/api/explode")
        def explode():
            raise RuntimeError("secret internal detail")

        with self.assertLogs("routes.errors", level="ERROR") as logs:
            response = app.test_client().get("/api/explode")
        self.assertError(response, 500, "Something went wrong")
        text = response.get_data(as_text=True)
        self.assertNotIn("secret internal detail", text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(any("Traceback" in line for line in logs.output))  # logged server side

    def test_api_requests_are_logged(self):
        with self.assertLogs("api", level="INFO") as logs:
            self.client.get("/api/boxes?per_page=0")
        self.assertTrue(any("GET /api/boxes?per_page=0 -> 400" in line for line in logs.output))


class PagedResponseTests(unittest.TestCase):
    def test_page_counts(self):
        body = paged_response(["a", "b"], total=45, page=2, per_page=20, filters={"x": None})
        self.assertEqual(body["pagination"], {"page": 2, "per_page": 20, "total": 45, "pages": 3,
                                              "has_next": True, "has_prev": True})
        self.assertEqual(body["filters"], {})
        empty = paged_response([], total=0, page=1, per_page=20)
        self.assertEqual(empty["pagination"]["pages"], 1)
        self.assertFalse(empty["pagination"]["has_next"])


class ConfigTests(unittest.TestCase):
    def test_environments(self):
        self.assertEqual(set(CONFIGS), {"development", "testing", "production"})
        self.assertIs(get_config("testing"), TestingConfig)
        self.assertTrue(get_config("development").DEBUG)
        self.assertTrue(TestingConfig.DB_NAME.endswith("_test"))
        with self.assertRaises(RuntimeError):
            get_config("staging")

    def test_production_needs_a_real_secret_key(self):
        class Unsafe(ProductionConfig):
            SECRET_KEY = "dev-only-change-me"

        with self.assertRaises(RuntimeError):
            create_app(Unsafe)


if __name__ == "__main__":
    unittest.main()
