"""Shared fixtures: a controllable clock and one user per role."""

from datetime import datetime, timedelta

from models.tracking import SAST, BoxTracker, Role, User

START = datetime(2026, 10, 25, 8, 0, tzinfo=SAST)

OPERATOR = User("thabo.m", Role.OPERATOR)
QC = User("lerato.k", Role.QC_INSPECTOR)
SUPERVISOR = User("johan.v", Role.SUPERVISOR)
EXECUTIVE = User("naledi.d", Role.EXECUTIVE)


class FakeClock:
    def __init__(self, start: datetime = START):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


def make_tracker():
    clock = FakeClock()
    return BoxTracker(clock=clock), clock


def box_at_stage(tracker, clock, stage_path, sku="BGA"):
    """Register a box and walk it through ``stage_path`` 30 min apart."""
    from models.tracking import STAGE_DEFAULT_LOCATIONS, Stage

    box = tracker.register_box(sku, "BATCH-01", 12, OPERATOR, "PROD")
    for stage in stage_path:
        clock.advance(minutes=30)
        user = QC if stage is Stage.QC_COMPLETE else (
            SUPERVISOR if stage in (Stage.REJECTED,) else OPERATOR)
        note = "test" if stage in (Stage.ON_HOLD, Stage.REJECTED) else ""
        tracker.move(box.box_id, stage, user, STAGE_DEFAULT_LOCATIONS[stage][0], note=note)
    return box
