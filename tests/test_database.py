"""MySQL integration tests: schema, seed data, models vs ERD and the API.

They build a separate database named ``<DB_NAME>_test`` from schema.sql
using the credentials in .env, and are skipped when MySQL is not reachable.
"""

import unittest
from datetime import datetime, timezone

import pymysql

from app import create_app
from config import Config
from database.db import connect, create_database, run_schema, split_sql_statements
from database.seed import seed_database
from models.repository import from_db_time, to_db_time
from models.tracking import (
    FLOOR_ROLES, HOLD_RELEASE_ROLES, REGISTER_ROLES, SAST, TRANSITION_ROLES,
)


class TestConfig(Config):
    DB_NAME = f"{Config.DB_NAME}_test"
    TESTING = True


SETTINGS = {k: getattr(TestConfig, k) for k in ("DB_HOST", "DB_PORT", "DB_USER",
                                                "DB_PASSWORD", "DB_NAME")}
MAIN_TABLES = ("product_categories", "products", "reorder_policies", "roles", "users",
               "locations", "stages", "stage_permissions", "incident_kinds", "boxes",
               "status_events", "incidents", "audit_log", "kpi_baselines", "pilot_periods")


def _mysql_available() -> bool:
    try:
        connect(SETTINGS, with_database=False).close()
        return True
    except pymysql.MySQLError:
        return False


class HelperTests(unittest.TestCase):
    def test_time_round_trip_keeps_microseconds_and_sast(self):
        at = datetime(2026, 10, 25, 9, 30, 15, 123456, tzinfo=SAST)
        stored = to_db_time(at)
        self.assertIsNone(stored.tzinfo)
        self.assertEqual(stored, datetime(2026, 10, 25, 7, 30, 15, 123456))
        self.assertEqual(from_db_time(stored).isoformat(), at.isoformat())
        self.assertEqual(from_db_time(stored).astimezone(timezone.utc), at)

    def test_split_sql_statements_ignores_comments(self):
        sql = "-- a comment; with semicolon\nCREATE TABLE a (x INT);\n\nDROP TABLE b;\n"
        self.assertEqual(split_sql_statements(sql), ["CREATE TABLE a (x INT)", "DROP TABLE b"])


@unittest.skipUnless(_mysql_available(), "MySQL not reachable with the .env settings")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_database(SETTINGS)

    def setUp(self):
        self.conn = connect(SETTINGS)
        run_schema(self.conn)
        seed_database(self.conn)
        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

    def tearDown(self):
        self.conn.close()

    def query(self, sql, params=()):
        self.conn.commit()  # end any old snapshot so we see the API's commits
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def post(self, path, **body):
        body.setdefault("username", "johan.v")
        return self.client.post(path, json=body)

    # -- schema and seed ---------------------------------------------------

    def test_seed_fills_every_table(self):
        for table in MAIN_TABLES:
            with self.subTest(table=table):
                count = self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
                self.assertGreater(count, 0)

    def test_constraints_reject_bad_rows(self):
        bad_rows = [
            "UPDATE products SET sku = 'bg1' WHERE sku = 'BGA'",
            "UPDATE products SET unit_cost = -1 WHERE sku = 'BGA'",
            "UPDATE boxes SET quantity = 0 LIMIT 1",
            "UPDATE stages SET warning_minutes = 500, critical_minutes = 100 "
            "WHERE stage_code = 'awaiting_qc'",
            "INSERT INTO products (sku, product_name, category_id, unit_cost) "
            "VALUES ('BGA', 'Duplicate', 1, 10)",
        ]
        for sql in bad_rows:
            with self.subTest(sql=sql), self.assertRaises(pymysql.MySQLError):
                with self.conn.cursor() as cur:
                    cur.execute(sql)
            self.conn.rollback()

    def test_seeded_permissions_match_python_rules(self):
        rows = self.query("SELECT stage_code, role_code FROM stage_permissions")
        self.assertEqual({(r["stage_code"], r["role_code"]) for r in rows},
                         {(s.value, r.value) for s, roles in TRANSITION_ROLES.items()
                          for r in roles})
        roles = {r["role_code"]: r for r in self.query("SELECT * FROM roles")}
        for code, row in roles.items():
            self.assertEqual(bool(row["is_floor_role"]), code in {r.value for r in FLOOR_ROLES})
            self.assertEqual(bool(row["can_register"]), code in {r.value for r in REGISTER_ROLES})
            self.assertEqual(bool(row["can_release_hold"]),
                             code in {r.value for r in HOLD_RELEASE_ROLES})

    def test_current_stage_matches_latest_event(self):
        mismatches = self.query("""
            SELECT b.box_id FROM boxes b
              JOIN status_events e ON e.box_id = b.box_id
             WHERE e.event_id = (SELECT MAX(e2.event_id) FROM status_events e2
                                  WHERE e2.box_id = b.box_id)
               AND e.to_stage_code <> b.current_stage_code""")
        self.assertEqual(mismatches, ())

    # -- reads come from the database -------------------------------------

    def test_page_data_reflects_a_changed_row(self):
        with self.conn.cursor() as cur:
            cur.execute("UPDATE products SET product_name = %s WHERE sku = %s",
                        ("Black garlic (500 g)", "BGA"))
        self.conn.commit()
        data = self.client.get("/api/app-data").get_json()
        names = {p["sku"]: p["name"] for p in data["products"]}
        self.assertEqual(names["BGA"], "Black garlic (500 g)")
        self.assertTrue(data["audit_verified"])

    def test_page_script_serves_database_data(self):
        response = self.client.get("/api/app-data.js")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_data(as_text=True).startswith("window.SG_DATA = {"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    # -- writes are saved -------------------------------------------------

    def test_register_box_is_saved(self):
        response = self.post("/api/boxes", username="thabo.m", sku="BGA",
                             batch_no="bga-2611-c", quantity=12, location="PROD")
        self.assertEqual(response.status_code, 201, response.get_json())
        box_id = response.get_json()["box"]["box_id"]

        row = self.query("SELECT * FROM boxes WHERE box_id = %s", (box_id,))[0]
        self.assertEqual((row["batch_no"], row["current_stage_code"]), ("BGA-2611-C", "in_production"))
        events = self.query("SELECT to_stage_code FROM status_events WHERE box_id = %s", (box_id,))
        self.assertEqual([e["to_stage_code"] for e in events], ["in_production"])
        audit = self.query("SELECT action FROM audit_log WHERE box_id = %s", (box_id,))
        self.assertEqual([a["action"] for a in audit], ["REGISTER"])

    def test_move_then_reload_keeps_history_and_audit_chain(self):
        box_id = self.post("/api/boxes", sku="MSP", batch_no="MSP-01", quantity=20,
                           location="PROD").get_json()["box"]["box_id"]
        response = self.post(f"/api/boxes/{box_id}/moves", username="thabo.m",
                             to_stage="awaiting_qc", location="QC-STAGE", method="scan")
        self.assertEqual(response.status_code, 200, response.get_json())

        data = self.client.get("/api/app-data").get_json()
        box = next(b for b in data["boxes"] if b["box_id"] == box_id)
        self.assertEqual([e["to"] for e in box["history"]], ["in_production", "awaiting_qc"])
        self.assertEqual(box["location"], "QC-STAGE")
        self.assertTrue(data["audit_verified"])

    def test_rejected_move_is_audited_but_changes_nothing(self):
        box_id = self.post("/api/boxes", sku="DON", batch_no="DON-01", quantity=12,
                           location="PROD").get_json()["box"]["box_id"]
        response = self.post(f"/api/boxes/{box_id}/moves", to_stage="dispatched",
                             location="DSP-DOCK")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["ok"])

        stage = self.query("SELECT current_stage_code FROM boxes WHERE box_id = %s", (box_id,))
        self.assertEqual(stage[0]["current_stage_code"], "in_production")
        last = self.query("SELECT action, outcome FROM audit_log ORDER BY seq DESC LIMIT 1")[0]
        self.assertEqual((last["action"], last["outcome"]), ("MOVE", "REJECTED"))

    def test_permission_and_validation_errors(self):
        executive = self.post("/api/boxes", username="naledi.d", sku="BGA", batch_no="BGA-01",
                              quantity=12, location="PROD")
        self.assertEqual(executive.status_code, 403)
        inactive = self.post("/api/boxes", username="anika.b", sku="BGA", batch_no="BGA-01",
                             quantity=12, location="PROD")
        self.assertEqual(inactive.status_code, 400)
        bad_qty = self.post("/api/boxes", sku="BGA", batch_no="BGA-01", quantity="12",
                            location="PROD")
        self.assertEqual(bad_qty.status_code, 400)
        unknown = self.post("/api/boxes/SG-BGA-261025-0099-0/moves", to_stage="awaiting_qc",
                            location="QC-STAGE")
        self.assertEqual(unknown.status_code, 404)

    def test_incident_open_link_and_close(self):
        opened = self.post("/api/incidents", username="thabo.m", kind="damaged_label",
                           note="Unreadable scan at PACK")
        self.assertEqual(opened.status_code, 201, opened.get_json())
        incident_id = opened.get_json()["incident"]["id"]
        box_id = self.query("SELECT box_id FROM boxes WHERE current_stage_code <> 'dispatched' "
                            "LIMIT 1")[0]["box_id"]

        linked = self.post(f"/api/incidents/{incident_id}/link", username="thabo.m", box_id=box_id)
        self.assertEqual(linked.status_code, 200, linked.get_json())
        closed = self.post(f"/api/incidents/{incident_id}/close", resolution="Label reprinted",
                           found_location="PACK")
        self.assertEqual(closed.status_code, 200, closed.get_json())

        row = self.query("SELECT i.box_id, i.closed_at, u.username FROM incidents i "
                         "JOIN users u ON u.user_id = i.closed_by WHERE incident_id = %s",
                         (incident_id,))[0]
        self.assertEqual((row["box_id"], row["username"]), (box_id, "johan.v"))
        self.assertIsNotNone(row["closed_at"])
        location = self.query("SELECT current_location_code FROM boxes WHERE box_id = %s",
                              (box_id,))[0]["current_location_code"]
        self.assertEqual(location, "PACK")


if __name__ == "__main__":
    unittest.main()
