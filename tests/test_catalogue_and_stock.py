"""Scalable catalogue, box corrections, catalogue permissions and stock bookkeeping."""

import unittest
from datetime import timedelta

from models import alerts
from models.products import UnknownProductError, catalogue_from_rows, get_product
from models.tracking import (
    MAIN_FLOW, BoxTracker, PermissionDeniedError, Stage, ValidationError,
)
from services.tracker_service import stock_changes, take_snapshot
from tests.helpers import EXECUTIVE, OPERATOR, SUPERVISOR, FakeClock, box_at_stage

ROWS = [
    {"sku": "BGA", "product_name": "Black garlic", "category_name": "Black garlic",
     "is_perishable": 0, "is_active": 1},
    {"sku": "GRW", "product_name": "Garlic wraps", "category_name": "Fresh salads",
     "is_perishable": 1, "is_active": 1},
    {"sku": "OLD", "product_name": "Discontinued", "category_name": "Dried",
     "is_perishable": 0, "is_active": 0},
]


class CatalogueTests(unittest.TestCase):
    def test_catalogue_from_rows_skips_inactive_unless_asked(self):
        self.assertEqual(set(catalogue_from_rows(ROWS)), {"BGA", "GRW"})
        self.assertEqual(set(catalogue_from_rows(ROWS, active_only=False)), {"BGA", "GRW", "OLD"})
        self.assertTrue(get_product("grw", catalogue_from_rows(ROWS)).perishable)

    def test_tracker_registers_products_added_to_the_database(self):
        tracker = BoxTracker(clock=FakeClock(), catalogue=catalogue_from_rows(ROWS))
        box = tracker.register_box("GRW", "GRW-01", 10, OPERATOR, "PROD")
        self.assertTrue(box.box_id.startswith("SG-GRW-"))
        with self.assertRaises(UnknownProductError):
            tracker.register_box("OLD", "OLD-01", 10, OPERATOR, "PROD")  # inactive
        with self.assertRaises(UnknownProductError):
            tracker.register_box("DON", "DON-01", 10, OPERATOR, "PROD")  # not in this catalogue

    def test_new_perishable_product_gets_tighter_alerts(self):
        clock = FakeClock()
        catalogue = catalogue_from_rows(ROWS)
        tracker = BoxTracker(clock=clock, catalogue=catalogue)
        box = tracker.register_box("GRW", "GRW-01", 10, OPERATOR, "PROD")
        thresholds = alerts.thresholds_from_rows([
            {"stage_code": "in_production", "warning_minutes": 240, "critical_minutes": 480},
            {"stage_code": "dispatched", "warning_minutes": None, "critical_minutes": None},
        ])
        self.assertEqual(set(thresholds), {Stage.IN_PRODUCTION})
        # 2.5 h: over the halved (perishable) 2 h warning limit, under the normal 4 h one.
        alert = alerts.evaluate_box(box, clock.now + timedelta(hours=2.5), thresholds, catalogue)
        self.assertEqual(alert.severity, alerts.Severity.WARNING)

    def test_only_supervisors_change_the_catalogue_and_refusals_are_audited(self):
        tracker = BoxTracker(clock=FakeClock())
        tracker.record_catalogue_change(SUPERVISOR, "PRODUCT_CREATE", "GRW Garlic wraps")
        with self.assertRaises(PermissionDeniedError):
            tracker.record_catalogue_change(OPERATOR, "PRODUCT_UPDATE", "BGA price")
        outcomes = [(e.action, e.outcome) for e in tracker.audit]
        self.assertEqual(outcomes, [("PRODUCT_CREATE", "OK"), ("PRODUCT_UPDATE", "REJECTED")])
        self.assertTrue(tracker.audit.verify())


class CorrectBoxTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tracker = BoxTracker(clock=self.clock)
        self.box = self.tracker.register_box("BGA", "BGA-01", 12, OPERATOR, "PROD")

    def test_correction_changes_values_and_is_audited(self):
        self.tracker.correct_box(self.box.box_id, SUPERVISOR, "Typed the wrong batch",
                                 batch_no="bga-02", quantity=10)
        self.assertEqual((self.box.batch_no, self.box.quantity), ("BGA-02", 10))
        self.assertEqual(len(self.box.history), 1)  # stage history untouched
        last = list(self.tracker.audit)[-1]
        self.assertEqual((last.action, last.outcome), ("CORRECT", "OK"))
        self.assertIn("batch BGA-01 -> BGA-02", last.detail)
        self.assertIn("qty 12 -> 10", last.detail)

    def test_correction_rules(self):
        box_id = self.box.box_id
        with self.assertRaises(ValidationError):
            self.tracker.correct_box(box_id, OPERATOR, "", quantity=10)  # no reason
        with self.assertRaises(ValidationError):
            self.tracker.correct_box(box_id, OPERATOR, "Recount")  # nothing to change
        with self.assertRaises(ValidationError):
            self.tracker.correct_box(box_id, OPERATOR, "Recount", quantity=12)  # same value
        with self.assertRaises(ValidationError):
            self.tracker.correct_box(box_id, OPERATOR, "Recount", quantity=0)
        with self.assertRaises(PermissionDeniedError):
            self.tracker.correct_box(box_id, EXECUTIVE, "Recount", quantity=10)


class StockChangeTests(unittest.TestCase):
    def test_register_adds_and_dispatch_or_reject_removes_stock(self):
        clock = FakeClock()
        tracker = BoxTracker(clock=clock)
        shipped = box_at_stage(tracker, clock, list(MAIN_FLOW[1:-1]), sku="BGA")
        before = take_snapshot(tracker)

        new_box = tracker.register_box("DON", "DON-01", 12, OPERATOR, "PROD")
        clock.advance(minutes=5)
        tracker.move(shipped.box_id, Stage.DISPATCHED, OPERATOR, "DSP-DOCK")
        clock.advance(minutes=5)
        tracker.move(new_box.box_id, Stage.ON_HOLD, OPERATOR, "QUARANTINE", note="Seal failure")
        clock.advance(minutes=5)
        tracker.move(new_box.box_id, Stage.REJECTED, SUPERVISOR, "WASTE", note="Seal failure")

        # DON: +1 registered, -1 rejected; BGA: -1 dispatched (registered before the snapshot)
        self.assertEqual(dict(stock_changes(tracker, before)), {"DON": 0, "BGA": -1})


if __name__ == "__main__":
    unittest.main()
