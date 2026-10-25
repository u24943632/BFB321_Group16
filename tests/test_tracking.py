"""Box IDs, state machine, roles, scan handling, incidents and audit trail."""

import dataclasses
import unittest
from datetime import date, datetime, timedelta

from models.products import UnknownProductError
from models.tracking import (
    MAIN_FLOW, SAST, BoxTracker, CaptureMethod, DamagedLabelError, IncidentKind,
    InvalidBoxIdError, InvalidTransitionError, PermissionDeniedError, ScanOutcome, Stage,
    UnknownBoxError, ValidationError, check_character, make_box_id, next_stages,
    parse_box_id, Role,
)
from tests.helpers import EXECUTIVE, OPERATOR, QC, SUPERVISOR, box_at_stage, make_tracker

TO_PACKED = [Stage.AWAITING_QC, Stage.QC_COMPLETE, Stage.PACKAGING_COMPLETE]


class BoxIdTests(unittest.TestCase):
    def test_format(self):
        box_id = make_box_id("bga", date(2026, 10, 25), 7)
        self.assertRegex(box_id, r"^SG-BGA-261025-0007-[0-9A-Z]$")

    def test_round_trip(self):
        box_id = make_box_id("MSP", date(2026, 1, 31), 1234)
        parsed = parse_box_id(f"  {box_id.lower()} \n")
        self.assertEqual((parsed.sku, parsed.production_date, parsed.sequence),
                         ("MSP", date(2026, 1, 31), 1234))
        self.assertEqual(str(parsed), box_id)

    def test_every_single_character_error_is_detected(self):
        box_id = make_box_id("PON", date(2026, 10, 25), 42)
        payload_positions = [i for i, ch in enumerate(box_id) if ch != "-"][2:]  # skip "SG"
        for pos in payload_positions:
            for replacement in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                if replacement == box_id[pos]:
                    continue
                corrupted = box_id[:pos] + replacement + box_id[pos + 1:]
                with self.subTest(corrupted=corrupted):
                    with self.assertRaises(InvalidBoxIdError):
                        parse_box_id(corrupted)

    def test_adjacent_digit_transposition_detected(self):
        box_id = make_box_id("DGA", date(2026, 10, 25), 12)   # ...-0012-X
        swapped = box_id.replace("0012", "0021")
        with self.assertRaises(DamagedLabelError):
            parse_box_id(swapped)

    def test_malformed_text(self):
        for text in ["", "hello", "SG-BG-261025-0001-A", "SG-BGA-261025-001-A", None]:
            with self.subTest(text=text), self.assertRaises(InvalidBoxIdError):
                parse_box_id(text)

    def test_invalid_date_rejected(self):
        payload = "BGA" + "261399" + "0001"
        with self.assertRaises(InvalidBoxIdError):
            parse_box_id(f"SG-BGA-261399-0001-{check_character(payload)}")

    def test_sequence_bounds(self):
        for seq in (0, 10_000):
            with self.subTest(seq=seq), self.assertRaises(ValidationError):
                make_box_id("BGA", date(2026, 10, 25), seq)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()

    def test_each_box_gets_unique_sequential_id(self):
        ids = [self.tracker.register_box("BGA", "B-001", 12, OPERATOR, "PROD").box_id
               for _ in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([parse_box_id(i).sequence for i in ids], [1, 2, 3])

    def test_sequence_is_per_product(self):
        a = self.tracker.register_box("BGA", "B-001", 12, OPERATOR, "PROD")
        b = self.tracker.register_box("DON", "B-002", 12, OPERATOR, "PROD")
        self.assertEqual(parse_box_id(a.box_id).sequence, 1)
        self.assertEqual(parse_box_id(b.box_id).sequence, 1)

    def test_initial_state_and_history(self):
        box = self.tracker.register_box("GST", "gst-2610-a", 24, OPERATOR, "prod")
        self.assertIs(box.stage, Stage.IN_PRODUCTION)
        self.assertEqual(box.batch_no, "GST-2610-A")
        self.assertEqual(box.location, "PROD")
        self.assertEqual(len(box.history), 1)
        self.assertIsNone(box.history[0].from_stage)
        self.assertEqual(box.history[0].at, self.clock.now)

    def test_validation(self):
        cases = [
            dict(sku="XXX", batch_no="B-001", quantity=1, location="PROD"),
            dict(sku="BGA", batch_no="!", quantity=1, location="PROD"),
            dict(sku="BGA", batch_no="B-001", quantity=0, location="PROD"),
            dict(sku="BGA", batch_no="B-001", quantity=True, location="PROD"),
            dict(sku="BGA", batch_no="B-001", quantity=1, location="CAR-PARK"),
        ]
        for kwargs in cases:
            with self.subTest(**kwargs), self.assertRaises((ValidationError, UnknownProductError)):
                self.tracker.register_box(user=OPERATOR, **kwargs)

    def test_qc_and_executive_cannot_register(self):
        for user in (QC, EXECUTIVE):
            with self.subTest(user=user.role), self.assertRaises(PermissionDeniedError):
                self.tracker.register_box("BGA", "B-001", 1, user, "PROD")

    def test_naive_timestamps_rejected(self):
        with self.assertRaises(ValidationError):
            self.tracker.register_box("BGA", "B-001", 1, OPERATOR, "PROD",
                                      at=datetime(2026, 10, 25, 9, 0))

    def test_lookup_is_case_insensitive(self):
        box = self.tracker.register_box("BGA", "B-001", 1, OPERATOR, "PROD")
        self.assertIs(self.tracker.get(box.box_id.lower()), box)
        with self.assertRaises(UnknownBoxError):
            self.tracker.get("SG-BGA-261025-9999-0")


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()

    def test_full_happy_path(self):
        box = box_at_stage(self.tracker, self.clock, MAIN_FLOW[1:])
        self.assertIs(box.stage, Stage.DISPATCHED)
        self.assertEqual([e.to_stage for e in box.history], list(MAIN_FLOW))
        self.assertTrue(box.has_complete_trace())

    def test_cannot_skip_to_ready_for_dispatch(self):
        for path in ([], [Stage.AWAITING_QC], [Stage.AWAITING_QC, Stage.QC_COMPLETE]):
            with self.subTest(path=path):
                box = box_at_stage(self.tracker, self.clock, path)
                with self.assertRaises(InvalidTransitionError):
                    self.tracker.move(box.box_id, Stage.READY_FOR_DISPATCH, SUPERVISOR,
                                      "DSP-STG")
                self.assertIs(box.stage, path[-1] if path else Stage.IN_PRODUCTION)

    def test_cannot_go_backwards(self):
        box = box_at_stage(self.tracker, self.clock, TO_PACKED)
        with self.assertRaises(InvalidTransitionError):
            self.tracker.move(box.box_id, Stage.AWAITING_QC, SUPERVISOR, "QC-STAGE")

    def test_same_stage_and_terminal_moves_rejected(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        with self.assertRaises(InvalidTransitionError):
            self.tracker.move(box.box_id, Stage.AWAITING_QC, OPERATOR, "QC-STAGE")
        done = box_at_stage(self.tracker, self.clock, MAIN_FLOW[1:])
        with self.assertRaises(InvalidTransitionError):
            self.tracker.move(done.box_id, Stage.ON_HOLD, SUPERVISOR, "QUARANTINE", note="x")

    def test_only_qc_roles_can_sign_off_qc(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        with self.assertRaises(PermissionDeniedError):
            self.tracker.move(box.box_id, Stage.QC_COMPLETE, OPERATOR, "QC-BAY")
        self.tracker.move(box.box_id, Stage.QC_COMPLETE, QC, "QC-BAY")
        self.assertIs(box.stage, Stage.QC_COMPLETE)

    def test_executive_is_read_only(self):
        box = box_at_stage(self.tracker, self.clock, [])
        self.assertEqual(next_stages(box, Role.EXECUTIVE), [])
        with self.assertRaises(PermissionDeniedError):
            self.tracker.move(box.box_id, Stage.AWAITING_QC, EXECUTIVE, "QC-STAGE")

    def test_hold_requires_reason(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        with self.assertRaises(ValidationError):
            self.tracker.move(box.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="  ")

    def test_hold_release_limited_to_held_stage_or_earlier(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        self.clock.advance(minutes=10)
        self.tracker.move(box.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="Seal failure")
        self.assertIs(box.held_from, Stage.AWAITING_QC)
        # QC failure cannot be released straight to QC complete or beyond
        for target in (Stage.QC_COMPLETE, Stage.READY_FOR_DISPATCH):
            with self.subTest(target=target), self.assertRaises(InvalidTransitionError):
                self.tracker.move(box.box_id, target, SUPERVISOR, "QC-BAY")
        # operators cannot release a hold
        with self.assertRaises(PermissionDeniedError):
            self.tracker.move(box.box_id, Stage.AWAITING_QC, OPERATOR, "QC-STAGE")
        self.assertEqual(next_stages(box, Role.SUPERVISOR),
                         [Stage.IN_PRODUCTION, Stage.AWAITING_QC, Stage.REJECTED])
        self.tracker.move(box.box_id, Stage.AWAITING_QC, SUPERVISOR, "QC-STAGE")
        self.assertIs(box.stage, Stage.AWAITING_QC)

    def test_resume_after_hold_keeps_prerequisites(self):
        box = box_at_stage(self.tracker, self.clock, TO_PACKED)
        self.tracker.move(box.box_id, Stage.ON_HOLD, OPERATOR, "QUARANTINE", note="Dented box")
        self.clock.advance(minutes=20)
        self.tracker.move(box.box_id, Stage.PACKAGING_COMPLETE, SUPERVISOR, "PACK")
        self.tracker.move(box.box_id, Stage.READY_FOR_DISPATCH, OPERATOR, "DSP-STG")
        self.assertIs(box.stage, Stage.READY_FOR_DISPATCH)

    def test_rework_requires_qc_again(self):
        box = box_at_stage(self.tracker, self.clock, TO_PACKED)
        self.tracker.move(box.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="Underweight")
        self.tracker.move(box.box_id, Stage.IN_PRODUCTION, SUPERVISOR, "PROD")
        self.assertEqual(box.stages_this_cycle(), {Stage.IN_PRODUCTION})
        self.assertEqual(next_stages(box, Role.SUPERVISOR), [Stage.AWAITING_QC, Stage.ON_HOLD])

    def test_reject_is_supervisor_only_and_terminal(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        self.tracker.move(box.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="Mould")
        with self.assertRaises(PermissionDeniedError):
            self.tracker.move(box.box_id, Stage.REJECTED, QC, "WASTE", note="Mould")
        self.tracker.move(box.box_id, Stage.REJECTED, SUPERVISOR, "WASTE", note="Mould")
        self.assertTrue(box.is_terminal)
        self.assertEqual(next_stages(box, Role.SUPERVISOR), [])

    def test_timestamps_cannot_go_backwards(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        with self.assertRaises(ValidationError):
            self.tracker.move(box.box_id, Stage.QC_COMPLETE, QC, "QC-BAY",
                              at=box.stage_entered_at - timedelta(minutes=1))

    def test_dwell_and_occupancies(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        self.clock.advance(hours=3)
        self.assertEqual(box.dwell(self.clock.now), timedelta(hours=3))
        occ = box.occupancies()
        self.assertEqual([o.stage for o in occ], [Stage.IN_PRODUCTION, Stage.AWAITING_QC])
        self.assertEqual(occ[0].duration(), timedelta(minutes=30))
        self.assertIsNone(occ[1].left_at)

    def test_broken_history_is_not_a_complete_trace(self):
        box = box_at_stage(self.tracker, self.clock, MAIN_FLOW[1:])
        del box.history[2]   # e.g. a paper record lost the QC sign-off
        self.assertFalse(box.has_complete_trace())


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()
        self.box = box_at_stage(self.tracker, self.clock, [])

    def test_accepted_scan_records_scan_method(self):
        self.clock.advance(minutes=5)
        result = self.tracker.scan(self.box.box_id, OPERATOR, "QC-STAGE", Stage.AWAITING_QC)
        self.assertIs(result.outcome, ScanOutcome.ACCEPTED)
        self.assertIs(self.box.history[-1].method, CaptureMethod.SCAN)
        self.assertEqual(self.box.location, "QC-STAGE")

    def test_duplicate_scan_is_ignored(self):
        self.clock.advance(minutes=5)
        self.tracker.scan(self.box.box_id, OPERATOR, "QC-STAGE", Stage.AWAITING_QC)
        self.clock.advance(seconds=3)
        result = self.tracker.scan(self.box.box_id, OPERATOR, "QC-STAGE", Stage.AWAITING_QC)
        self.assertIs(result.outcome, ScanOutcome.DUPLICATE_IGNORED)
        self.assertTrue(result.ok)
        self.assertEqual(len(self.box.history), 2)

    def test_damaged_label_opens_incident(self):
        damaged = self.box.box_id[:-1] + ("0" if self.box.box_id[-1] != "0" else "1")
        result = self.tracker.scan(damaged, OPERATOR, "PROD", Stage.AWAITING_QC)
        self.assertIs(result.outcome, ScanOutcome.DAMAGED)
        self.assertIs(result.incident.kind, IncidentKind.DAMAGED_LABEL)
        self.assertIs(self.box.stage, Stage.IN_PRODUCTION)

    def test_unregistered_label(self):
        unknown = make_box_id("SDT", date(2026, 10, 25), 500)
        result = self.tracker.scan(unknown, OPERATOR, "PROD", Stage.AWAITING_QC)
        self.assertIs(result.outcome, ScanOutcome.UNKNOWN)
        self.assertIs(result.incident.kind, IncidentKind.UNKNOWN_LABEL)

    def test_same_label_two_places_at_once_is_suspect_duplicate(self):
        self.clock.advance(minutes=5)
        self.tracker.scan(self.box.box_id, OPERATOR, "QC-STAGE", Stage.AWAITING_QC)
        self.clock.advance(seconds=20)
        result = self.tracker.scan(self.box.box_id, OPERATOR, "PACK")
        self.assertIs(result.outcome, ScanOutcome.SUSPECT_DUPLICATE_LABEL)
        self.assertIs(result.incident.kind, IncidentKind.DUPLICATE_LABEL)

    def test_scan_of_dispatched_box_is_suspect_duplicate(self):
        done = box_at_stage(self.tracker, self.clock, MAIN_FLOW[1:])
        self.clock.advance(hours=1)
        result = self.tracker.scan(done.box_id, OPERATOR, "PACK")
        self.assertIs(result.outcome, ScanOutcome.SUSPECT_DUPLICATE_LABEL)

    def test_invalid_move_via_scan_is_rejected_not_raised(self):
        self.clock.advance(minutes=5)
        result = self.tracker.scan(self.box.box_id, OPERATOR, "DSP-STG",
                                   Stage.READY_FOR_DISPATCH)
        self.assertIs(result.outcome, ScanOutcome.REJECTED)
        self.assertFalse(result.ok)
        self.assertIn("cannot move", result.message)

    def test_location_check_in(self):
        self.clock.advance(minutes=5)
        result = self.tracker.scan(self.box.box_id, OPERATOR, "QUARANTINE")
        self.assertIs(result.outcome, ScanOutcome.ACCEPTED)
        self.assertEqual(self.box.location, "QUARANTINE")
        self.assertIs(self.box.stage, Stage.IN_PRODUCTION)

    def test_executive_cannot_scan(self):
        with self.assertRaises(PermissionDeniedError):
            self.tracker.scan(self.box.box_id, EXECUTIVE, "PROD")


class IncidentAndLabelTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()
        self.box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])

    def test_misplaced_box_investigation_time(self):
        inc = self.tracker.open_incident(IncidentKind.MISPLACED, OPERATOR,
                                         box_id=self.box.box_id, note="Not on QC rack")
        self.assertTrue(inc.is_open)
        self.assertIsNone(inc.investigation_time)
        self.clock.advance(minutes=17)
        self.tracker.close_incident(inc.incident_id, SUPERVISOR, "Found behind pallet",
                                    found_location="PACK")
        self.assertEqual(inc.investigation_time, timedelta(minutes=17))
        self.assertEqual(self.box.location, "PACK")
        self.assertEqual(self.tracker.open_incidents(), [])

    def test_close_needs_resolution_and_only_once(self):
        inc = self.tracker.open_incident(IncidentKind.MISPLACED, OPERATOR, box_id=self.box.box_id)
        with self.assertRaises(ValidationError):
            self.tracker.close_incident(inc.incident_id, OPERATOR, "")
        self.tracker.close_incident(inc.incident_id, OPERATOR, "Found")
        with self.assertRaises(ValidationError):
            self.tracker.close_incident(inc.incident_id, OPERATOR, "Again")

    def test_damaged_scan_incident_can_be_linked_to_box(self):
        damaged = self.box.box_id[:-1] + ("0" if self.box.box_id[-1] != "0" else "1")
        incident = self.tracker.scan(damaged, OPERATOR, "QC-STAGE").incident
        self.assertIsNone(incident.box_id)
        with self.assertRaises(PermissionDeniedError):
            self.tracker.link_incident(incident.incident_id, self.box.box_id, EXECUTIVE)
        self.tracker.link_incident(incident.incident_id, self.box.box_id.lower(), OPERATOR)
        self.assertEqual(incident.box_id, self.box.box_id)
        self.assertEqual(self.tracker.audit.for_box(self.box.box_id)[-1].action, "INCIDENT_LINK")
        with self.assertRaises(ValidationError):   # cannot re-link
            self.tracker.link_incident(incident.incident_id, self.box.box_id, OPERATOR)

    def test_reprint_increments_label_version(self):
        self.tracker.reprint_label(self.box.box_id, OPERATOR, "Label wet and smudged")
        self.assertEqual(self.box.label_version, 2)
        with self.assertRaises(PermissionDeniedError):
            self.tracker.reprint_label(self.box.box_id, QC, "x")
        with self.assertRaises(ValidationError):
            self.tracker.reprint_label(self.box.box_id, OPERATOR, " ")


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self.tracker, self.clock = make_tracker()

    def test_records_accepted_and_rejected_actions(self):
        box = box_at_stage(self.tracker, self.clock, [Stage.AWAITING_QC])
        with self.assertRaises(PermissionDeniedError):
            self.tracker.move(box.box_id, Stage.QC_COMPLETE, OPERATOR, "QC-BAY")
        entries = self.tracker.audit.for_box(box.box_id)
        self.assertEqual([(e.action, e.outcome) for e in entries],
                         [("REGISTER", "OK"), ("MOVE", "OK"), ("MOVE", "REJECTED")])
        self.assertEqual(entries[-1].username, OPERATOR.username)

    def test_chain_verifies_and_detects_tampering(self):
        box_at_stage(self.tracker, self.clock, TO_PACKED)
        self.assertTrue(self.tracker.audit.verify())
        log = self.tracker.audit._entries
        log[1] = dataclasses.replace(log[1], username="someone.else")
        self.assertFalse(self.tracker.audit.verify())

    def test_deleting_an_entry_is_detected(self):
        box_at_stage(self.tracker, self.clock, TO_PACKED)
        del self.tracker.audit._entries[1]
        self.assertFalse(self.tracker.audit.verify())


if __name__ == "__main__":
    unittest.main()
