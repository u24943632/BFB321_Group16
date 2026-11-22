"""Run the tracking rules against MySQL.

The process rules live in :class:`models.tracking.BoxTracker`, which works in
memory. This module connects it to the database:

1. :func:`load_tracker` reads every box, incident and audit entry into a
   tracker (``BoxTracker.restore``).
2. A route runs one command on it (register, move, open incident ...).
3. :func:`save_changes` compares the tracker with a snapshot taken before the
   command and writes only the new or changed rows.

:class:`TrackerSession` wraps the three steps in one transaction. A command
that breaks a rule still commits, because the tracker records the rejected
attempt in the audit trail before raising.

Stock on hand (``reorder_policies.on_hand_qty``) follows the boxes: a newly
registered box adds one finished box to stock, and a box that is dispatched
or rejected (written off) removes one. This keeps the reorder advice in step
with what happens on the floor.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from models import repository
from models.products import catalogue_from_rows
from models.tracking import BoxTracker, Stage, TrackingError, User

log = logging.getLogger(__name__)


@dataclass
class TrackerSnapshot:
    """What was already stored before a command ran."""

    # box_id -> (event count, location, location time, label version, batch, quantity)
    boxes: dict[str, tuple] = field(default_factory=dict)
    incidents: dict[str, tuple] = field(default_factory=dict)  # incident_id -> (box_id, closed_at)
    audit_count: int = 0


def take_snapshot(tracker: BoxTracker) -> TrackerSnapshot:
    return TrackerSnapshot(
        boxes={b.box_id: (len(b.history), b.location, b.location_updated_at, b.label_version,
                          b.batch_no, b.quantity)
               for b in tracker.boxes},
        incidents={i.incident_id: (i.box_id, i.closed_at) for i in tracker.incidents},
        audit_count=len(tracker.audit),
    )


def load_tracker(conn, clock=None) -> BoxTracker:
    """Build a tracker holding everything currently in the database."""
    locations = {r["location_code"]: r["location_name"]
                 for r in repository.fetch_locations(conn) if r["is_active"]}
    catalogue = catalogue_from_rows(repository.fetch_products(conn))
    tracker = BoxTracker(clock=clock, locations=locations, catalogue=catalogue)
    tracker.restore(repository.fetch_boxes(conn), repository.fetch_incidents(conn),
                    repository.fetch_audit_entries(conn))
    if not tracker.audit.verify():
        log.warning("Audit chain verification failed: an audit_log row was edited or deleted.")
    return tracker


def save_changes(conn, tracker: BoxTracker, before: TrackerSnapshot,
                 user_ids: dict[str, int]) -> None:
    """Write every box, event, incident and audit entry added or changed since ``before``.

    Boxes and events go first because incidents reference boxes.
    """
    for box in tracker.boxes:
        stored = before.boxes.get(box.box_id)
        if stored is None:
            repository.insert_box(conn, box, user_ids[box.history[0].username])
            new_events = box.history
        else:
            new_events = box.history[stored[0]:]
            if new_events or stored[1:] != (box.location, box.location_updated_at,
                                             box.label_version, box.batch_no, box.quantity):
                repository.update_box_state(conn, box)
        for event in new_events:
            repository.insert_status_event(conn, event, user_ids[event.username])

    for incident in tracker.incidents:
        stored = before.incidents.get(incident.incident_id)
        if stored is None:
            repository.insert_incident(conn, incident, user_ids)
        elif stored != (incident.box_id, incident.closed_at):
            repository.update_incident(conn, incident, user_ids)

    for entry in list(tracker.audit)[before.audit_count:]:
        repository.insert_audit_entry(conn, entry, user_ids[entry.username])


#: Stages that take a box out of the warehouse stock.
LEAVES_STOCK = frozenset({Stage.DISPATCHED, Stage.REJECTED})


def stock_changes(tracker: BoxTracker, before: TrackerSnapshot) -> Counter:
    """Boxes added to (+) or removed from (-) stock per SKU since ``before``."""
    deltas: Counter = Counter()
    for box in tracker.boxes:
        stored = before.boxes.get(box.box_id)
        for event in box.history[stored[0] if stored else 0:]:
            if event.from_stage is None:
                deltas[box.sku] += 1
            elif event.to_stage in LEAVES_STOCK:
                deltas[box.sku] -= 1
    return deltas


class UnknownUserError(TrackingError):
    """The request names a user that does not exist or is inactive."""


class TrackerSession:
    """One write transaction around a tracker command.

    Usage::

        with TrackerSession(conn) as session:
            box = session.tracker.register_box(...)

    On leaving the block the changes are committed - also when the command
    raised a :class:`TrackingError`, so the rejected attempt stays in the
    audit trail. Any other error rolls everything back.
    """

    def __init__(self, conn, clock=None) -> None:
        self.conn = conn
        self._clock = clock
        self.tracker: Optional[BoxTracker] = None
        self._before: Optional[TrackerSnapshot] = None

    def __enter__(self) -> "TrackerSession":
        self.conn.begin()
        repository.lock_audit_log(self.conn)  # serialise writers on the audit chain
        self.tracker = load_tracker(self.conn, self._clock)
        self._before = take_snapshot(self.tracker)
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc_type is None or issubclass(exc_type, TrackingError):
            try:
                save_changes(self.conn, self.tracker, self._before,
                             repository.fetch_user_ids(self.conn))
                for sku, delta in stock_changes(self.tracker, self._before).items():
                    if delta:
                        repository.adjust_on_hand(self.conn, sku, delta)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        else:
            self.conn.rollback()
        return False  # never swallow the exception

    def user(self, username: str) -> User:
        """The active user making this request."""
        user = repository.find_active_user(self.conn, (username or "").strip())
        if user is None:
            raise UnknownUserError(f"No active user '{username}'.")
        return user
