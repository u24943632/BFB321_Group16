"""MySQL integration tests: schema, seed data, models vs ERD and the API.

They build a separate database named ``<DB_NAME>_test`` from schema.sql
using the credentials in .env, and are skipped when MySQL is not reachable.
"""

import unittest
from datetime import datetime, timezone

import pymysql

from app import create_app
from config import TestingConfig
from database.db import connect, create_database, run_schema, split_sql_statements
from database.seed import seed_database
from models.repository import from_db_time, to_db_time
from models.tracking import (
    FLOOR_ROLES, HOLD_RELEASE_ROLES, REGISTER_ROLES, SAST, TRANSITION_ROLES,
)


SETTINGS = {k: getattr(TestingConfig, k) for k in ("DB_HOST", "DB_PORT", "DB_USER",
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
        self.app = create_app(TestingConfig)
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

    def patch(self, path, **body):
        body.setdefault("username", "johan.v")
        return self.client.patch(path, json=body)

    def put(self, path, **body):
        body.setdefault("username", "johan.v")
        return self.client.put(path, json=body)

    def count(self, sql, params=()):
        """Run a ``SELECT COUNT(*) AS n ...`` query and return n."""
        return self.query(sql, params)[0]["n"]

    def on_hand(self, sku):
        rows = self.query("SELECT r.on_hand_qty FROM reorder_policies r JOIN products p "
                          "ON p.product_id = r.product_id WHERE p.sku = %s", (sku,))
        return rows[0]["on_hand_qty"]

    def register(self, sku="BGA", **body):
        body = {"sku": sku, "batch_no": f"{sku}-01", "quantity": 12, "location": "PROD", **body}
        response = self.post("/api/boxes", **body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["box"]["box_id"]

    def walk(self, box_id, *stages):
        """Move a box through ``stages`` (codes) at each stage's standard location."""
        locations = {"awaiting_qc": "QC-STAGE", "qc_complete": "QC-BAY",
                     "packaging_complete": "PACK", "ready_for_dispatch": "DSP-STG",
                     "dispatched": "DSP-DOCK"}
        for stage in stages:
            response = self.post(f"/api/boxes/{box_id}/moves", to_stage=stage,
                                 location=locations[stage])
            self.assertEqual(response.status_code, 201, response.get_json())

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
        self.assertEqual(response.status_code, 201, response.get_json())

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

        linked = self.patch(f"/api/incidents/{incident_id}", username="thabo.m", box_id=box_id)
        self.assertEqual(linked.status_code, 200, linked.get_json())
        closed = self.patch(f"/api/incidents/{incident_id}", status="closed",
                            resolution="Label reprinted", found_location="PACK")
        self.assertEqual(closed.status_code, 200, closed.get_json())

        row = self.query("SELECT i.box_id, i.closed_at, u.username FROM incidents i "
                         "JOIN users u ON u.user_id = i.closed_by WHERE incident_id = %s",
                         (incident_id,))[0]
        self.assertEqual((row["box_id"], row["username"]), (box_id, "johan.v"))
        self.assertIsNotNone(row["closed_at"])
        location = self.query("SELECT current_location_code FROM boxes WHERE box_id = %s",
                              (box_id,))[0]["current_location_code"]
        self.assertEqual(location, "PACK")


    # -- REST reads: pagination, filtering, detail --------------------------

    def test_list_boxes_paginates_and_filters(self):
        total = self.query("SELECT COUNT(*) AS n FROM boxes")[0]["n"]
        first = self.client.get("/api/boxes?per_page=5").get_json()
        self.assertEqual(first["pagination"]["total"], total)
        self.assertEqual(len(first["items"]), min(5, total))
        second = self.client.get("/api/boxes?per_page=5&page=2").get_json()
        first_ids = {b["box_id"] for b in first["items"]}
        self.assertFalse(first_ids & {b["box_id"] for b in second["items"]})

        waiting = self.query("SELECT COUNT(*) AS n FROM boxes "
                             "WHERE current_stage_code = 'awaiting_qc'")[0]["n"]
        body = self.client.get("/api/boxes?stage=awaiting_qc&per_page=100").get_json()
        self.assertEqual(body["pagination"]["total"], waiting)
        self.assertTrue(all(b["stage"] == "awaiting_qc" for b in body["items"]))
        self.assertEqual(body["filters"]["stage"], "awaiting_qc")

        by_sku = self.client.get("/api/boxes?sku=bga&per_page=100").get_json()["items"]
        self.assertTrue(by_sku and all(b["sku"] == "BGA" for b in by_sku))
        active = self.client.get("/api/boxes?in_process=true&per_page=100").get_json()["items"]
        self.assertFalse({b["stage"] for b in active} & {"dispatched", "rejected"})

    def test_box_detail_and_unknown_box(self):
        box_id = self.register()
        box = self.client.get(f"/api/boxes/{box_id}").get_json()["box"]
        self.assertEqual((box["stage"], box["severity"]), ("in_production", "ok"))
        self.assertEqual([e["to"] for e in box["history"]], ["in_production"])
        self.assertEqual([a["action"] for a in box["audit"]], ["REGISTER"])
        self.assertEqual(self.client.get("/api/boxes/SG-BGA-261025-0099-0").status_code, 404)

    # -- REST update on the main entity (box) -------------------------------

    def test_patch_box_correction_and_location_persist(self):
        box_id = self.register()
        response = self.patch(f"/api/boxes/{box_id}", batch_no="bga-02", quantity=10,
                              reason="Wrong batch typed at registration")
        self.assertEqual(response.status_code, 200, response.get_json())
        row = self.query("SELECT batch_no, quantity FROM boxes WHERE box_id = %s", (box_id,))[0]
        self.assertEqual((row["batch_no"], row["quantity"]), ("BGA-02", 10))
        last = self.query("SELECT action FROM audit_log ORDER BY seq DESC LIMIT 1")[0]
        self.assertEqual(last["action"], "CORRECT")

        no_reason = self.patch(f"/api/boxes/{box_id}", quantity=9)
        self.assertEqual(no_reason.status_code, 400)
        moved = self.patch(f"/api/boxes/{box_id}", username="thabo.m", location="PACK")
        self.assertEqual(moved.status_code, 200, moved.get_json())
        location = self.query("SELECT current_location_code FROM boxes WHERE box_id = %s",
                              (box_id,))[0]["current_location_code"]
        self.assertEqual(location, "PACK")
        self.assertTrue(self.client.get("/api/app-data").get_json()["audit_verified"])

    def test_reprint_label_raises_version(self):
        box_id = self.register()
        response = self.post(f"/api/boxes/{box_id}/label-reprints", reason="Label torn at packing")
        self.assertEqual(response.status_code, 201, response.get_json())
        version = self.query("SELECT label_version FROM boxes WHERE box_id = %s",
                             (box_id,))[0]["label_version"]
        self.assertEqual(version, 2)

    # -- scalable product catalogue -----------------------------------------

    def test_create_product_then_register_a_box_for_it(self):
        response = self.post("/api/products", sku="grw", name="Garlic wraps",
                             category="Fresh produce", unit_cost=120.5,
                             reorder_policy={"annual_demand": 1200, "order_cost": 500,
                                             "holding_cost": 25, "daily_demand_std": 2,
                                             "lead_time_days": 3, "on_hand_qty": 40})
        self.assertEqual(response.status_code, 201, response.get_json())
        product = response.get_json()["product"]
        self.assertEqual((product["sku"], product["perishable"]), ("GRW", True))
        self.assertGreater(product["reorder"]["eoq"], 0)
        self.assertEqual(self.on_hand("GRW"), 40)

        box_id = self.register("GRW")  # the tracker reads the catalogue from MySQL
        self.assertTrue(box_id.startswith("SG-GRW-"))
        self.assertEqual(self.on_hand("GRW"), 41)  # a finished box went into stock

        duplicate = self.post("/api/products", sku="GRW", name="Again", category="Dried",
                              unit_cost=1)
        self.assertEqual(duplicate.status_code, 409)
        unknown = self.post("/api/products", sku="XYZ", name="Mystery", category="Frozen",
                            unit_cost=1)
        self.assertEqual(unknown.status_code, 400)
        actions = [r["action"] for r in self.query("SELECT action FROM audit_log "
                                                   "WHERE action LIKE 'PRODUCT%%'")]
        self.assertEqual(actions, ["PRODUCT_CREATE"])

    def test_only_supervisors_change_products(self):
        response = self.post("/api/products", username="thabo.m", sku="GRW", name="Garlic wraps",
                             category="Dried", unit_cost=10)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.count("SELECT COUNT(*) AS n FROM products WHERE sku = 'GRW'"), 0)
        last = self.query("SELECT action, outcome FROM audit_log ORDER BY seq DESC LIMIT 1")[0]
        self.assertEqual((last["action"], last["outcome"]), ("PRODUCT_CREATE", "REJECTED"))

    def test_patch_product_list_filters_and_deactivate(self):
        response = self.patch("/api/products/BGA", name="Black garlic 500 g", unit_cost=450)
        self.assertEqual(response.status_code, 200, response.get_json())
        row = self.query("SELECT product_name, unit_cost FROM products WHERE sku = 'BGA'")[0]
        self.assertEqual((row["product_name"], float(row["unit_cost"])),
                         ("Black garlic 500 g", 450.0))

        self.assertEqual(self.patch("/api/products/BGA", active=False).status_code, 200)
        refused = self.post("/api/boxes", sku="BGA", batch_no="BGA-09", quantity=5, location="PROD")
        self.assertEqual(refused.status_code, 400)
        inactive = self.client.get("/api/products?active=false").get_json()["items"]
        self.assertEqual([p["sku"] for p in inactive], ["BGA"])

        dried = self.client.get("/api/products?category=Dried&per_page=2").get_json()
        self.assertEqual(dried["pagination"]["total"], 3)  # DON, DGA, SDT
        self.assertEqual(len(dried["items"]), 2)
        self.assertTrue(all(p["category"] == "Dried" for p in dried["items"]))
        self.assertEqual(self.client.get("/api/products/XYZ").status_code, 404)

    def test_categories(self):
        created = self.post("/api/categories", name="Fresh salads", perishable=True)
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertEqual(self.post("/api/categories", name="Fresh salads").status_code, 409)
        names = [c["name"] for c in self.client.get("/api/categories").get_json()["items"]]
        self.assertIn("Fresh salads", names)

    # -- supply chain model drives the reorder flow -------------------------

    def test_reorder_policy_update_recalculates_the_plan(self):
        plan = self.client.get("/api/products/BGA/reorder-policy").get_json()["plan"]
        empty = self.patch("/api/products/BGA/reorder-policy", on_hand_qty=0).get_json()["plan"]
        self.assertEqual(empty["status"], "CRITICAL")
        self.assertEqual(self.on_hand("BGA"), 0)
        full = self.patch("/api/products/BGA/reorder-policy", on_hand_qty=100000).get_json()["plan"]
        self.assertEqual(full["status"], "OK")
        bigger = self.patch("/api/products/BGA/reorder-policy",
                            annual_demand=plan["annual_demand"] * 4).get_json()["plan"]
        self.assertAlmostEqual(bigger["eoq"], plan["eoq"] * 2, places=6)  # EOQ ~ sqrt(D)

        self.post("/api/products", sku="GRW", name="Garlic wraps", category="Dried", unit_cost=10)
        self.assertEqual(self.patch("/api/products/GRW/reorder-policy",
                                    on_hand_qty=5).status_code, 404)
        created = self.put("/api/products/GRW/reorder-policy", annual_demand=500, order_cost=300,
                           holding_cost=10, daily_demand_std=1.5, lead_time_days=4)
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(created.get_json()["plan"]["on_hand"], 0)  # default

    def test_stock_follows_box_movements(self):
        before = self.on_hand("DON")
        box_id = self.register("DON")
        self.assertEqual(self.on_hand("DON"), before + 1)
        self.walk(box_id, "awaiting_qc", "qc_complete", "packaging_complete",
                  "ready_for_dispatch", "dispatched")
        self.assertEqual(self.on_hand("DON"), before)

    # -- incidents, scans, KPIs ----------------------------------------------

    def test_incident_list_filters_and_detail(self):
        open_count = self.count("SELECT COUNT(*) AS n FROM incidents WHERE closed_at IS NULL")
        body = self.client.get("/api/incidents?status=open&per_page=100").get_json()
        self.assertEqual(body["pagination"]["total"], open_count)
        self.assertTrue(all(i["closed_at"] is None for i in body["items"]))
        some_id = self.query("SELECT incident_id FROM incidents LIMIT 1")[0]["incident_id"]
        self.assertEqual(self.client.get(f"/api/incidents/{some_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/incidents/INC-9999").status_code, 404)

    def test_scan_endpoint_handles_good_duplicate_and_damaged_codes(self):
        box_id = self.register()
        first = self.post("/api/scans", username="thabo.m", code=box_id.lower(), location="PROD",
                          to_stage="awaiting_qc").get_json()
        self.assertEqual(first["outcome"], "accepted", first)
        again = self.post("/api/scans", username="thabo.m", code=box_id, location="PROD",
                          to_stage="awaiting_qc").get_json()
        self.assertEqual(again["outcome"], "duplicate_ignored", again)
        damaged = self.post("/api/scans", username="thabo.m", code="SG-BGA-26#025-0001",
                            location="PACK").get_json()
        self.assertEqual(damaged["outcome"], "damaged", damaged)
        kind = self.query("SELECT kind_code FROM incidents WHERE incident_id = %s",
                          (damaged["incident_id"],))[0]["kind_code"]
        self.assertEqual(kind, "damaged_label")

    def test_metrics_summary_is_calculated_from_live_data(self):
        summary = self.client.get("/api/metrics/summary").get_json()["summary"]
        self.assertEqual(summary["boxes_total"], self.count("SELECT COUNT(*) AS n FROM boxes"))
        self.assertEqual(summary["open_incidents"], self.count(
            "SELECT COUNT(*) AS n FROM incidents WHERE closed_at IS NULL"))
        self.assertEqual(summary["active_products"], 10)
        plans = self.client.get("/api/reorder").get_json()["items"]
        self.assertEqual(summary["items_to_reorder"], sum(p["status"] != "OK" for p in plans))

        self.register()
        after = self.client.get("/api/metrics/summary").get_json()["summary"]
        self.assertEqual(after["boxes_in_process"], summary["boxes_in_process"] + 1)
        self.assertEqual(after["boxes_by_stage"]["in_production"],
                         summary["boxes_by_stage"]["in_production"] + 1)

    def test_alerts_kpis_and_health(self):
        alerts = self.client.get("/api/alerts").get_json()["items"]
        self.assertTrue(all(a["severity"] in {"warning", "critical"} for a in alerts))
        critical = self.client.get("/api/alerts?severity=critical").get_json()["items"]
        self.assertEqual(len(critical), sum(a["severity"] == "critical" for a in alerts))
        kpis = self.client.get("/api/metrics/kpis").get_json()["kpis"]
        self.assertTrue(kpis["comparison"])
        self.assertEqual(self.client.get("/api/health").get_json(), {"ok": True, "database": "up"})

if __name__ == "__main__":
    unittest.main()
