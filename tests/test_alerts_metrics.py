"""Dwell alerts and before/after KPI calculations."""

import unittest
from datetime import timedelta

from models.alerts import (
    DEFAULT_THRESHOLDS, DwellThreshold, Severity, classify_dwell, evaluate_alerts,
    evaluate_box, threshold_for,
)
from models.metrics import (
    ProcessSnapshot, compare, mean_dwell_by_stage, scan_compliance_pct, snapshot_from_tracker,
)
from models.tracking import MAIN_FLOW, IncidentKind, Stage
from tests.helpers import OPERATOR, START, box_at_stage, make_tracker


class ThresholdTests(unittest.TestCase):
    def test_classification_boundaries_are_inclusive(self):
        t = DwellThreshold(timedelta(hours=2), timedelta(hours=4))
        self.assertIs(classify_dwell(timedelta(hours=1, minutes=59), t), Severity.OK)
        self.assertIs(classify_dwell(timedelta(hours=2), t), Severity.WARNING)
        self.assertIs(classify_dwell(timedelta(hours=4), t), Severity.CRITICAL)

    def test_invalid_threshold(self):
        with self.assertRaises(ValueError):
            DwellThreshold(timedelta(hours=4), timedelta(hours=2))

    def test_perishable_products_use_tighter_limits(self):
        dried = threshold_for(Stage.AWAITING_QC, "DON")
        sprouts = threshold_for(Stage.AWAITING_QC, "MSP")
        self.assertEqual(dried, DEFAULT_THRESHOLDS[Stage.AWAITING_QC])
        self.assertEqual(sprouts.warning, dried.warning / 2)

    def test_terminal_stages_have_no_threshold(self):
        self.assertIsNone(threshold_for(Stage.DISPATCHED, "DON"))


class AlertEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()

    def test_box_alert_escalates_over_time(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC], sku="DON")
        self.assertIsNone(evaluate_box(box, self.clock.advance(hours=1)))
        warning = evaluate_box(box, self.clock.advance(hours=1))
        self.assertIs(warning.severity, Severity.WARNING)
        self.assertEqual(warning.threshold, timedelta(hours=2))
        critical = evaluate_box(box, self.clock.advance(hours=2))
        self.assertIs(critical.severity, Severity.CRITICAL)
        self.assertIn("Awaiting QC", critical.message)

    def test_dispatched_box_never_alerts(self):
        box = box_at_stage(self.tracker, self.clock, MAIN_FLOW[1:])
        self.assertIsNone(evaluate_box(box, self.clock.advance(days=30)))

    def test_alerts_sorted_most_urgent_first(self):
        warn = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC], sku="DON")
        self.clock.advance(hours=1)
        crit = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC], sku="MSP")
        self.clock.advance(hours=2, minutes=15)   # DON 3.75 h (warn), MSP 2.25 h (crit, limit 2 h)
        alerts = evaluate_alerts(self.tracker.boxes, self.clock.now)
        self.assertEqual([a.box_id for a in alerts], [crit.box_id, warn.box_id])
        self.assertEqual([a.severity for a in alerts], [Severity.CRITICAL, Severity.WARNING])


def _snapshot(**overrides):
    values = dict(label="x", period_days=10, boxes_dispatched=400, tracking_incidents=20,
                  investigations=20, investigation_minutes=800, status_lookups=600,
                  mean_status_retrieval_seconds=240, mean_stage_dwell_hours=6,
                  quality_errors=4, dispatch_errors=6, traced_boxes=240, labour_hours=480)
    values.update(overrides)
    return ProcessSnapshot(**values)


class SnapshotTests(unittest.TestCase):
    def test_derived_kpis(self):
        s = _snapshot()
        self.assertAlmostEqual(s.throughput_per_day, 40)
        self.assertAlmostEqual(s.tracking_incident_rate_pct, 5)
        self.assertAlmostEqual(s.mean_investigation_minutes, 40)
        self.assertAlmostEqual(s.process_error_rate_pct, 2.5)
        self.assertAlmostEqual(s.traceability_coverage_pct, 60)
        # non-value: 800/60 = 13.333 h + 600*240/3600 = 40 h -> 53.333 h
        self.assertAlmostEqual(s.non_value_hours, 53.3333, places=4)
        self.assertAlmostEqual(s.labour_utilisation_pct, (480 - 53.3333) / 480 * 100, places=3)

    def test_zero_denominators(self):
        s = _snapshot(boxes_dispatched=0, traced_boxes=0, investigations=0, investigation_minutes=0)
        self.assertEqual(s.tracking_incident_rate_pct, 0)
        self.assertEqual(s.mean_investigation_minutes, 0)

    def test_validation(self):
        for bad in [dict(period_days=0), dict(labour_hours=0), dict(quality_errors=-1),
                    dict(traced_boxes=401)]:
            with self.subTest(**bad), self.assertRaises(ValueError):
                _snapshot(**bad)

    def test_compare_respects_direction(self):
        before = _snapshot()
        after = _snapshot(investigation_minutes=200, traced_boxes=400, tracking_incidents=5)
        rows = {r.kpi.key: r for r in compare(before, after)}
        inv = rows["mean_investigation_minutes"]          # 40 -> 10 min
        self.assertAlmostEqual(inv.change_pct, -75)
        self.assertAlmostEqual(inv.improvement_pct, 75)
        self.assertTrue(inv.improved)
        trace = rows["traceability_coverage_pct"]          # 60 -> 100 %
        self.assertAlmostEqual(trace.improvement_pct, 66.6667, places=3)
        self.assertTrue(trace.improved)
        self.assertFalse(rows["throughput_per_day"].improved)   # unchanged

    def test_compare_with_zero_baseline(self):
        rows = {r.kpi.key: r for r in compare(_snapshot(quality_errors=0, dispatch_errors=0),
                                              _snapshot(quality_errors=2, dispatch_errors=0))}
        err = rows["process_error_rate_pct"]
        self.assertIsNone(err.change_pct)
        self.assertFalse(err.improved)


class TrackerMetricsTests(unittest.TestCase):
    def test_snapshot_from_tracker(self):
        tracker, clock = make_tracker()
        box_a = box_at_stage(tracker, clock, MAIN_FLOW[1:])      # 5 stays of 30 min
        box_b = box_at_stage(tracker, clock, [Stage.AWAITING_QC])
        inc = tracker.open_incident(IncidentKind.MISPLACED, OPERATOR, box_id=box_b.box_id)
        clock.advance(minutes=12)
        tracker.close_incident(inc.incident_id, OPERATOR, "Found")
        tracker.open_incident(IncidentKind.DISPATCH_ERROR, OPERATOR, box_id=box_a.box_id,
                              note="Wrong customer")
        end = clock.advance(minutes=1)

        snap = snapshot_from_tracker(tracker, label="pilot", start=START, end=end,
                                     labour_hours=8, status_lookups=10,
                                     mean_status_retrieval_seconds=15)
        self.assertEqual(snap.boxes_dispatched, 1)
        self.assertEqual(snap.traced_boxes, 1)
        self.assertEqual(snap.tracking_incidents, 1)
        self.assertEqual(snap.investigations, 1)
        self.assertAlmostEqual(snap.investigation_minutes, 12)
        self.assertEqual(snap.dispatch_errors, 1)
        self.assertAlmostEqual(snap.mean_stage_dwell_hours, 0.5)   # every stay was 30 min

    def test_scan_compliance_and_dwell_by_stage(self):
        tracker, clock = make_tracker()
        box = box_at_stage(tracker, clock, [])                    # registration not counted
        clock.advance(minutes=30)
        tracker.scan(box.box_id, OPERATOR, "QC-STAGE", Stage.AWAITING_QC)   # scanned
        clock.advance(minutes=30)
        tracker.move(box.box_id, Stage.ON_HOLD, OPERATOR, "QUARANTINE", note="x")  # manual
        end = clock.advance(minutes=1)
        self.assertAlmostEqual(scan_compliance_pct(tracker.boxes, START, end), 50)
        dwell = mean_dwell_by_stage(tracker.boxes, START, end)
        self.assertAlmostEqual(dwell[Stage.IN_PRODUCTION], 0.5)
        self.assertAlmostEqual(dwell[Stage.AWAITING_QC], 0.5)
        self.assertNotIn(Stage.ON_HOLD, dwell)   # still open


if __name__ == "__main__":
    unittest.main()
