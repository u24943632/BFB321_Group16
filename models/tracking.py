"""Box-level tracking for Simply Garlic.

This module is the single source of truth for the process rules:

* **Unique box IDs** - every physical box/batch gets its own ID
  (``SG-<SKU>-<YYMMDD>-<SEQ>-<CHECK>``), not just a product-level QR code.
  The final character is a Luhn mod-36 check character, so a damaged or
  mistyped label is detected instead of silently matching the wrong box.
* **Stage state machine** - boxes may only move along
  ``ALLOWED_TRANSITIONS`` and must have passed the ``PREREQUISITES`` of the
  target stage in the current cycle. A box can therefore never become
  *Ready for dispatch* without *QC complete* and *Packaging complete*.
* **Role-based access** - ``TRANSITION_ROLES`` limits who may make each move
  (e.g. only QC inspectors/supervisors sign off QC; executives are read-only).
* **Complete status history** - every stage change is an immutable
  :class:`StatusEvent` with timestamp, user, role, location and capture method.
* **Scan handling** - duplicate scans are ignored idempotently; damaged,
  unknown and suspected-duplicate labels raise incidents.
* **Audit trail** - every action, including rejected attempts, is written to a
  hash-chained :class:`AuditLog` so tampering is detectable.

It is deliberately framework-free (no Flask, no database). An API or
persistence layer can wrap :class:`BoxTracker` later without changing the rules.
All timestamps must be timezone-aware; the default clock uses SAST (UTC+2).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Iterator, Optional

from .products import get_product

SAST = timezone(timedelta(hours=2), "SAST")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class TrackingError(Exception):
    """Base class for all tracking rule violations."""


class ValidationError(TrackingError, ValueError):
    """Input data is missing or malformed."""


class InvalidBoxIdError(TrackingError, ValueError):
    """Text is not a well-formed Simply Garlic box ID."""


class DamagedLabelError(InvalidBoxIdError):
    """Box ID is well-formed but its check character does not match.

    Almost always a damaged/partially read QR code or a typing mistake.
    """


class UnknownBoxError(TrackingError, KeyError):
    """No box is registered under this ID."""


class InvalidTransitionError(TrackingError):
    """The requested stage change breaks the process rules."""


class PermissionDeniedError(TrackingError):
    """The user's role may not perform this action."""


# --------------------------------------------------------------------------
# Stages, transitions and roles
# --------------------------------------------------------------------------

class Stage(str, Enum):
    IN_PRODUCTION = "in_production"
    AWAITING_QC = "awaiting_qc"
    QC_COMPLETE = "qc_complete"
    PACKAGING_COMPLETE = "packaging_complete"
    READY_FOR_DISPATCH = "ready_for_dispatch"
    DISPATCHED = "dispatched"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return STAGE_LABELS[self]


STAGE_LABELS = {
    Stage.IN_PRODUCTION: "In production",
    Stage.AWAITING_QC: "Awaiting QC",
    Stage.QC_COMPLETE: "QC complete",
    Stage.PACKAGING_COMPLETE: "Packaging complete",
    Stage.READY_FOR_DISPATCH: "Ready for dispatch",
    Stage.DISPATCHED: "Dispatched",
    Stage.ON_HOLD: "On hold",
    Stage.REJECTED: "Rejected",
}

#: The normal path every box follows, in order.
MAIN_FLOW: tuple[Stage, ...] = (
    Stage.IN_PRODUCTION,
    Stage.AWAITING_QC,
    Stage.QC_COMPLETE,
    Stage.PACKAGING_COMPLETE,
    Stage.READY_FOR_DISPATCH,
    Stage.DISPATCHED,
)

TERMINAL_STAGES = frozenset({Stage.DISPATCHED, Stage.REJECTED})

#: Graph of permitted moves. Any working stage can be put ON_HOLD (QC failure,
#: damage, investigation). Releases from hold are further limited by
#: :func:`validate_transition` to the held-from stage or earlier.
ALLOWED_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.IN_PRODUCTION: frozenset({Stage.AWAITING_QC, Stage.ON_HOLD}),
    Stage.AWAITING_QC: frozenset({Stage.QC_COMPLETE, Stage.ON_HOLD}),
    Stage.QC_COMPLETE: frozenset({Stage.PACKAGING_COMPLETE, Stage.ON_HOLD}),
    Stage.PACKAGING_COMPLETE: frozenset({Stage.READY_FOR_DISPATCH, Stage.ON_HOLD}),
    Stage.READY_FOR_DISPATCH: frozenset({Stage.DISPATCHED, Stage.ON_HOLD}),
    Stage.ON_HOLD: frozenset(set(MAIN_FLOW[:-1]) | {Stage.REJECTED}),
    Stage.DISPATCHED: frozenset(),
    Stage.REJECTED: frozenset(),
}

#: Stages that must have been reached in the current cycle before entering
#: the key stage. A cycle restarts when a box re-enters production (rework)
#: or awaiting QC (re-inspection).
PREREQUISITES: dict[Stage, tuple[Stage, ...]] = {
    Stage.QC_COMPLETE: (Stage.AWAITING_QC,),
    Stage.PACKAGING_COMPLETE: (Stage.QC_COMPLETE,),
    Stage.READY_FOR_DISPATCH: (Stage.QC_COMPLETE, Stage.PACKAGING_COMPLETE),
    Stage.DISPATCHED: (Stage.QC_COMPLETE, Stage.PACKAGING_COMPLETE, Stage.READY_FOR_DISPATCH),
}

_CYCLE_RESET_STAGES = frozenset({Stage.IN_PRODUCTION, Stage.AWAITING_QC})


class Role(str, Enum):
    OPERATOR = "operator"
    QC_INSPECTOR = "qc_inspector"
    SUPERVISOR = "supervisor"
    EXECUTIVE = "executive"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title().replace("Qc", "QC")


#: Roles that work on the floor and may write. Executives are read-only.
FLOOR_ROLES = frozenset({Role.OPERATOR, Role.QC_INSPECTOR, Role.SUPERVISOR})

#: Who may move a box *into* each stage (for moves not starting from ON_HOLD).
TRANSITION_ROLES: dict[Stage, frozenset[Role]] = {
    Stage.IN_PRODUCTION: frozenset({Role.SUPERVISOR}),
    Stage.AWAITING_QC: frozenset({Role.OPERATOR, Role.QC_INSPECTOR, Role.SUPERVISOR}),
    Stage.QC_COMPLETE: frozenset({Role.QC_INSPECTOR, Role.SUPERVISOR}),
    Stage.PACKAGING_COMPLETE: frozenset({Role.OPERATOR, Role.SUPERVISOR}),
    Stage.READY_FOR_DISPATCH: frozenset({Role.OPERATOR, Role.SUPERVISOR}),
    Stage.DISPATCHED: frozenset({Role.OPERATOR, Role.SUPERVISOR}),
    Stage.ON_HOLD: FLOOR_ROLES,  # anyone on the floor can stop a suspect box
    Stage.REJECTED: frozenset({Role.SUPERVISOR}),
}

#: Only supervisors may release a box from hold (or reject it).
HOLD_RELEASE_ROLES = frozenset({Role.SUPERVISOR})

REGISTER_ROLES = frozenset({Role.OPERATOR, Role.SUPERVISOR})
REPRINT_ROLES = frozenset({Role.OPERATOR, Role.SUPERVISOR})

#: Stages that require a written reason.
NOTE_REQUIRED_STAGES = frozenset({Stage.ON_HOLD, Stage.REJECTED})


# --------------------------------------------------------------------------
# Physical locations
# --------------------------------------------------------------------------

#: One standard location per stage keeps the process uniform. Every station
#: has a fixed location QR tag. Location is captured by scanning
#: that tag with the box, so "location" means *last confirmed scan location*,
#: never an inferred position.
LOCATIONS: dict[str, str] = {
    "PROD": "Production",
    "QC-STAGE": "QC staging rack",
    "QC-BAY": "QC bay",
    "PACK": "Packing station",
    "DSP-STG": "Dispatch staging",
    "DSP-DOCK": "Dispatch dock",
    "QUARANTINE": "Quarantine cage",
    "WASTE": "Waste / write-off area",
}

#: The standard location for a box once it enters each stage.
STAGE_DEFAULT_LOCATIONS: dict[Stage, tuple[str, ...]] = {
    Stage.IN_PRODUCTION: ("PROD",),
    Stage.AWAITING_QC: ("QC-STAGE",),
    Stage.QC_COMPLETE: ("QC-BAY",),
    Stage.PACKAGING_COMPLETE: ("PACK",),
    Stage.READY_FOR_DISPATCH: ("DSP-STG",),
    Stage.DISPATCHED: ("DSP-DOCK",),
    Stage.ON_HOLD: ("QUARANTINE",),
    Stage.REJECTED: ("WASTE",),
}


# --------------------------------------------------------------------------
# Box IDs
# --------------------------------------------------------------------------

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BOX_ID_PATTERN = re.compile(r"^SG-([A-Z]{3})-(\d{6})-(\d{4})-([0-9A-Z])$")
BATCH_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")
MAX_SEQUENCE = 9999


def check_character(payload: str) -> str:
    """Return the Luhn mod-36 check character for ``payload``.

    Detects every single-character error and most adjacent transpositions,
    which covers the typical damaged-label and manual-entry mistakes.
    """
    n = len(_ALPHABET)
    factor = 2
    total = 0
    for ch in reversed(payload):
        addend = factor * _ALPHABET.index(ch)
        factor = 1 if factor == 2 else 2
        total += addend // n + addend % n
    return _ALPHABET[(n - total % n) % n]


@dataclass(frozen=True)
class BoxId:
    sku: str
    production_date: date
    sequence: int

    def __str__(self) -> str:
        return make_box_id(self.sku, self.production_date, self.sequence)


def make_box_id(sku: str, production_date: date, sequence: int) -> str:
    """Build a box ID, e.g. ``SG-BGA-261025-0007-K``.

    Args:
        sku: three-letter product code.
        production_date: day the box was registered (SAST).
        sequence: 1..9999, unique per SKU per day.
    """
    sku = sku.upper()
    if not re.fullmatch(r"[A-Z]{3}", sku):
        raise ValidationError(f"SKU must be three letters, got '{sku}'.")
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise ValidationError(f"Sequence must be 1-{MAX_SEQUENCE}, got {sequence}.")
    yymmdd = production_date.strftime("%y%m%d")
    seq = f"{sequence:04d}"
    return f"SG-{sku}-{yymmdd}-{seq}-{check_character(sku + yymmdd + seq)}"


def parse_box_id(raw: str) -> BoxId:
    """Parse and verify scanned or typed text as a box ID.

    Leading/trailing whitespace and lower case are tolerated (scanner and
    keyboard quirks).

    Raises:
        InvalidBoxIdError: text is not in ``SG-XXX-YYMMDD-NNNN-C`` form.
        DamagedLabelError: form is right but the check character is wrong.
    """
    text = (raw or "").strip().upper()
    match = BOX_ID_PATTERN.match(text)
    if not match:
        raise InvalidBoxIdError(
            f"'{(raw or '').strip()}' is not a box ID (expected SG-XXX-YYMMDD-NNNN-C)."
        )
    sku, yymmdd, seq, check = match.groups()
    if check_character(sku + yymmdd + seq) != check:
        raise DamagedLabelError(
            f"'{text}' failed its check character - the label is damaged or was mistyped."
        )
    try:
        production_date = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        raise InvalidBoxIdError(f"'{text}' contains an invalid date.") from None
    return BoxId(sku, production_date, int(seq))


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

class CaptureMethod(str, Enum):
    SCAN = "scan"      # QR scanned at a station: counts towards scan compliance
    MANUAL = "manual"  # typed / selected by hand


@dataclass(frozen=True)
class User:
    username: str
    role: Role

    def __post_init__(self) -> None:
        if not self.username.strip():
            raise ValidationError("Username is required.")
        object.__setattr__(self, "role", Role(self.role))


@dataclass(frozen=True)
class StatusEvent:
    """One immutable entry in a box's status history."""

    box_id: str
    from_stage: Optional[Stage]
    to_stage: Stage
    at: datetime
    username: str
    role: Role
    location: str
    method: CaptureMethod
    note: str = ""


@dataclass(frozen=True)
class StageOccupancy:
    """A period a box spent in one stage. ``left_at`` is None while ongoing."""

    stage: Stage
    entered_at: datetime
    left_at: Optional[datetime]

    def duration(self, now: Optional[datetime] = None) -> Optional[timedelta]:
        end = self.left_at or now
        return None if end is None else end - self.entered_at


@dataclass
class Box:
    box_id: str
    sku: str
    batch_no: str
    quantity: int
    created_at: datetime
    history: list[StatusEvent] = field(default_factory=list)
    location: str = ""
    location_updated_at: Optional[datetime] = None
    label_version: int = 1

    @property
    def stage(self) -> Stage:
        return self.history[-1].to_stage

    @property
    def stage_entered_at(self) -> datetime:
        return self.history[-1].at

    @property
    def held_from(self) -> Optional[Stage]:
        """Stage the box was in when it was put on hold (None if not on hold)."""
        return self.history[-1].from_stage if self.stage is Stage.ON_HOLD else None

    @property
    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    def dwell(self, now: datetime) -> timedelta:
        """Time spent in the current stage as at ``now``."""
        return max(now - self.stage_entered_at, timedelta(0))

    def stages_this_cycle(self) -> set[Stage]:
        """Stages reached since the last rework / re-inspection."""
        reached: set[Stage] = set()
        for event in self.history:
            if event.to_stage in _CYCLE_RESET_STAGES:
                reached = set()
            reached.add(event.to_stage)
        return reached

    def occupancies(self) -> list[StageOccupancy]:
        """Every stage period, oldest first; the last one is open unless terminal."""
        result = []
        for i, event in enumerate(self.history):
            nxt = self.history[i + 1].at if i + 1 < len(self.history) else None
            result.append(StageOccupancy(event.to_stage, event.at, nxt))
        return result

    def has_complete_trace(self) -> bool:
        """True if the history is an unbroken chain from production.

        Checks the first event is registration into production, each event
        starts where the previous ended, timestamps never go backwards, and
        every main-flow stage up to the furthest one reached is recorded.
        """
        if not self.history or self.history[0].from_stage is not None:
            return False
        if self.history[0].to_stage is not Stage.IN_PRODUCTION:
            return False
        for prev, cur in zip(self.history, self.history[1:]):
            if cur.from_stage is not prev.to_stage or cur.at < prev.at:
                return False
        reached = {e.to_stage for e in self.history}
        furthest = max((MAIN_FLOW.index(s) for s in reached if s in MAIN_FLOW), default=0)
        return all(stage in reached for stage in MAIN_FLOW[: furthest + 1])


class IncidentKind(str, Enum):
    MISPLACED = "misplaced"
    MISLABELLED = "mislabelled"
    DAMAGED_LABEL = "damaged_label"
    DUPLICATE_LABEL = "duplicate_label"
    UNKNOWN_LABEL = "unknown_label"
    QUALITY_ERROR = "quality_error"
    DISPATCH_ERROR = "dispatch_error"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


#: Incidents counted in the "lost / misplaced / mislabelled label" KPI.
TRACKING_INCIDENT_KINDS = frozenset({
    IncidentKind.MISPLACED,
    IncidentKind.MISLABELLED,
    IncidentKind.DAMAGED_LABEL,
    IncidentKind.DUPLICATE_LABEL,
    IncidentKind.UNKNOWN_LABEL,
})


@dataclass
class Incident:
    incident_id: str
    kind: IncidentKind
    opened_at: datetime
    opened_by: str
    box_id: Optional[str] = None
    note: str = ""
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    resolution: str = ""

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def investigation_time(self) -> Optional[timedelta]:
        """Open-to-close time; None while still open."""
        return None if self.closed_at is None else self.closed_at - self.opened_at


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AuditEntry:
    seq: int
    at: datetime
    username: str
    role: str
    action: str
    outcome: str  # "OK" or "REJECTED"
    box_id: Optional[str]
    detail: str
    prev_hash: str
    hash: str

    def payload(self) -> dict:
        return {
            "seq": self.seq,
            "at": self.at.isoformat(),
            "username": self.username,
            "role": self.role,
            "action": self.action,
            "outcome": self.outcome,
            "box_id": self.box_id,
            "detail": self.detail,
            "prev_hash": self.prev_hash,
        }


class AuditLog:
    """Append-only, hash-chained log of every action (accepted or rejected).

    Each entry stores the SHA-256 of its own content plus the previous hash,
    so editing or deleting any past entry breaks :meth:`verify`.
    """

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    @staticmethod
    def _digest(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def record(self, *, at: datetime, user: User, action: str, outcome: str = "OK",
               box_id: Optional[str] = None, detail: str = "") -> AuditEntry:
        prev = self._entries[-1].hash if self._entries else self.GENESIS
        draft = AuditEntry(len(self._entries) + 1, at, user.username, user.role.value,
                           action, outcome, box_id, detail, prev, "")
        entry = AuditEntry(**{**draft.__dict__, "hash": self._digest(draft.payload())})
        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        prev = self.GENESIS
        for i, entry in enumerate(self._entries, start=1):
            if entry.seq != i or entry.prev_hash != prev:
                return False
            if entry.hash != self._digest(entry.payload()):
                return False
            prev = entry.hash
        return True

    def for_box(self, box_id: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.box_id == box_id]

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def validate_transition(box: Box, to_stage: Stage, role: Role) -> None:
    """Raise if ``role`` may not move ``box`` to ``to_stage``; return None if OK.

    Checks, in order: not already there, not finished, permitted by the graph,
    hold releases go no further than the held-from stage, prerequisites met in
    this cycle, and the role is authorised.
    """
    to_stage = Stage(to_stage)
    role = Role(role)
    current = box.stage
    if to_stage is current:
        raise InvalidTransitionError(f"{box.box_id} is already '{current.label}'.")
    if current in TERMINAL_STAGES:
        raise InvalidTransitionError(
            f"{box.box_id} is '{current.label}'; no further moves are allowed.")
    if to_stage not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(s.label for s in MAIN_FLOW + (Stage.ON_HOLD,)
                            if s in ALLOWED_TRANSITIONS[current])
        raise InvalidTransitionError(
            f"{box.box_id} cannot move from '{current.label}' to '{to_stage.label}'. "
            f"Allowed next: {allowed}.")
    if current is Stage.ON_HOLD and to_stage is not Stage.REJECTED:
        held_from = box.held_from
        if MAIN_FLOW.index(to_stage) > MAIN_FLOW.index(held_from):
            raise InvalidTransitionError(
                f"{box.box_id} was held at '{held_from.label}' and can only be released "
                f"to that stage or an earlier one.")
    reached = box.stages_this_cycle()
    missing = [s for s in PREREQUISITES.get(to_stage, ()) if s not in reached]
    if missing:
        raise InvalidTransitionError(
            f"{box.box_id} cannot be '{to_stage.label}' before completing: "
            + ", ".join(s.label for s in missing) + ".")
    allowed_roles = HOLD_RELEASE_ROLES if current is Stage.ON_HOLD else TRANSITION_ROLES[to_stage]
    if role not in allowed_roles:
        raise PermissionDeniedError(
            f"A {role.label} may not move a box from '{current.label}' to '{to_stage.label}'.")


def next_stages(box: Box, role: Role) -> list[Stage]:
    """Stages ``role`` may move ``box`` to right now, in process order."""
    options = []
    for stage in MAIN_FLOW + (Stage.ON_HOLD, Stage.REJECTED):
        try:
            validate_transition(box, stage, role)
        except TrackingError:
            continue
        options.append(stage)
    return options


# --------------------------------------------------------------------------
# Tracker
# --------------------------------------------------------------------------

class ScanOutcome(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE_IGNORED = "duplicate_ignored"
    DAMAGED = "damaged"
    UNKNOWN = "unknown"
    SUSPECT_DUPLICATE_LABEL = "suspect_duplicate_label"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ScanResult:
    outcome: ScanOutcome
    message: str
    box: Optional[Box] = None
    incident: Optional[Incident] = None

    @property
    def ok(self) -> bool:
        return self.outcome in (ScanOutcome.ACCEPTED, ScanOutcome.DUPLICATE_IGNORED)


class BoxTracker:
    """In-memory register of boxes, incidents and the audit trail.

    Args:
        clock: returns the current aware datetime; inject a fixed clock in tests.
        min_relocation_time: if the same label is scanned at two different
            locations faster than this, a duplicate label is suspected.
        locations: valid location codes (defaults to :data:`LOCATIONS`).
    """

    def __init__(self, clock: Optional[Callable[[], datetime]] = None,
                 min_relocation_time: timedelta = timedelta(seconds=60),
                 locations: Optional[dict[str, str]] = None) -> None:
        self._clock = clock or (lambda: datetime.now(SAST))
        self.min_relocation_time = min_relocation_time
        self.locations = dict(locations or LOCATIONS)
        self._boxes: dict[str, Box] = {}
        self._sequences: dict[tuple[str, date], int] = defaultdict(int)
        self._incidents: dict[str, Incident] = {}
        self.audit = AuditLog()

    # -- helpers -----------------------------------------------------------

    def _now(self, at: Optional[datetime]) -> datetime:
        at = at or self._clock()
        if at.tzinfo is None:
            raise ValidationError("Timestamps must be timezone-aware.")
        return at

    def _require_role(self, user: User, roles: frozenset[Role], what: str,
                      at: datetime, action: str, box_id: Optional[str] = None) -> None:
        if user.role not in roles:
            message = f"A {user.role.label} may not {what}."
            self.audit.record(at=at, user=user, action=action, outcome="REJECTED",
                              box_id=box_id, detail=message)
            raise PermissionDeniedError(message)

    def _check_location(self, location: str) -> str:
        code = (location or "").strip().upper()
        if code not in self.locations:
            raise ValidationError(
                f"Unknown location '{location}'. Scan the station's location tag.")
        return code

    # -- queries -----------------------------------------------------------

    @property
    def boxes(self) -> list[Box]:
        return list(self._boxes.values())

    @property
    def incidents(self) -> list[Incident]:
        return list(self._incidents.values())

    def get(self, box_id: str) -> Box:
        """Status lookup by box ID (case/whitespace tolerant)."""
        key = (box_id or "").strip().upper()
        try:
            return self._boxes[key]
        except KeyError:
            raise UnknownBoxError(f"No box registered as '{box_id}'.") from None

    def boxes_in_stage(self, stage: Stage) -> list[Box]:
        return [b for b in self._boxes.values() if b.stage is Stage(stage)]

    def open_incidents(self) -> list[Incident]:
        return [i for i in self._incidents.values() if i.is_open]

    # -- commands ----------------------------------------------------------

    def register_box(self, sku: str, batch_no: str, quantity: int, user: User,
                     location: str, *, at: Optional[datetime] = None,
                     method: CaptureMethod = CaptureMethod.MANUAL) -> Box:
        """Create a box with a new unique ID, starting In production."""
        at = self._now(at)
        self._require_role(user, REGISTER_ROLES, "register boxes", at, "REGISTER")
        product = get_product(sku)
        batch = (batch_no or "").strip().upper()
        if not BATCH_PATTERN.match(batch):
            raise ValidationError(
                "Batch number must be 3-20 letters, digits or dashes, e.g. BGA-2610-A.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValidationError("Quantity must be a whole number of at least 1.")
        location = self._check_location(location)

        production_date = at.astimezone(SAST).date()
        key = (product.sku, production_date)
        if self._sequences[key] >= MAX_SEQUENCE:
            raise ValidationError(f"Daily box limit reached for {product.sku}.")
        self._sequences[key] += 1
        box_id = make_box_id(product.sku, production_date, self._sequences[key])

        event = StatusEvent(box_id, None, Stage.IN_PRODUCTION, at, user.username,
                            user.role, location, CaptureMethod(method), "Box registered")
        box = Box(box_id, product.sku, batch, quantity, at, [event], location, at)
        self._boxes[box_id] = box
        self.audit.record(at=at, user=user, action="REGISTER", box_id=box_id,
                          detail=f"{product.name}, batch {batch}, qty {quantity} at {location}")
        return box

    def move(self, box_id: str, to_stage: Stage, user: User, location: str, *,
             at: Optional[datetime] = None, method: CaptureMethod = CaptureMethod.MANUAL,
             note: str = "") -> StatusEvent:
        """Move a box to a new stage, enforcing every process rule.

        Rejected attempts are audited before the error is raised.
        """
        at = self._now(at)
        box = self.get(box_id)
        to_stage = Stage(to_stage)
        location = self._check_location(location)
        note = (note or "").strip()
        if to_stage in NOTE_REQUIRED_STAGES and not note:
            raise ValidationError(f"A reason is required to mark a box '{to_stage.label}'.")
        if at < box.stage_entered_at:
            raise ValidationError("Timestamp is earlier than the box's last status change.")
        try:
            validate_transition(box, to_stage, user.role)
        except TrackingError as exc:
            self.audit.record(at=at, user=user, action="MOVE", outcome="REJECTED",
                              box_id=box.box_id, detail=str(exc))
            raise

        event = StatusEvent(box.box_id, box.stage, to_stage, at, user.username, user.role,
                            location, CaptureMethod(method), note)
        box.history.append(event)
        box.location, box.location_updated_at = location, at
        self.audit.record(at=at, user=user, action="MOVE", box_id=box.box_id,
                          detail=f"{event.from_stage.label} -> {to_stage.label} at {location}"
                                 f" ({event.method.value})" + (f": {note}" if note else ""))
        return event

    def record_location(self, box_id: str, user: User, location: str, *,
                        at: Optional[datetime] = None) -> Box:
        """Location check-in scan without a stage change (e.g. moved racks)."""
        at = self._now(at)
        box = self.get(box_id)
        self._require_role(user, FLOOR_ROLES, "record locations", at, "LOCATION", box.box_id)
        location = self._check_location(location)
        previous = box.location
        box.location, box.location_updated_at = location, at
        self.audit.record(at=at, user=user, action="LOCATION", box_id=box.box_id,
                          detail=f"{previous} -> {location}")
        return box

    def scan(self, raw_code: str, user: User, location: str,
             to_stage: Optional[Stage] = None, *, at: Optional[datetime] = None) -> ScanResult:
        """Handle a QR scan at a station.

        * damaged / unreadable code -> DAMAGED, opens a damaged-label incident
        * valid code not registered -> UNKNOWN, opens an unknown-label incident
        * label of a dispatched/rejected box, or the same label at two
          locations within ``min_relocation_time`` -> SUSPECT_DUPLICATE_LABEL
        * box already in ``to_stage`` -> DUPLICATE_IGNORED (idempotent)
        * ``to_stage`` None -> location check-in only
        * otherwise the move is attempted; rule violations -> REJECTED
        """
        at = self._now(at)
        self._require_role(user, FLOOR_ROLES, "scan boxes", at, "SCAN")
        location = self._check_location(location)

        try:
            box_id = str(parse_box_id(raw_code))
        except InvalidBoxIdError as exc:
            self.audit.record(at=at, user=user, action="SCAN", outcome="REJECTED",
                              detail=str(exc))
            incident = self.open_incident(IncidentKind.DAMAGED_LABEL, user, at=at,
                                          note=f"Unreadable scan at {location}: {raw_code!r}")
            return ScanResult(ScanOutcome.DAMAGED,
                              f"{exc} Type the printed ID and request a label reprint.",
                              incident=incident)

        box = self._boxes.get(box_id)
        if box is None:
            self.audit.record(at=at, user=user, action="SCAN", outcome="REJECTED",
                              box_id=box_id, detail="Label not registered")
            incident = self.open_incident(IncidentKind.UNKNOWN_LABEL, user, box_id=None, at=at,
                                          note=f"Unregistered label {box_id} at {location}")
            return ScanResult(ScanOutcome.UNKNOWN,
                              f"{box_id} is not registered. Quarantine the box and tell a supervisor.",
                              incident=incident)

        suspect = None
        if box.is_terminal:
            suspect = f"{box_id} is already '{box.stage.label}' but was scanned at {location}."
        elif (box.location_updated_at is not None and location != box.location
              and at - box.location_updated_at < self.min_relocation_time):
            suspect = (f"{box_id} was scanned at {box.location} and {location} "
                       f"within {int(self.min_relocation_time.total_seconds())} s.")
        if suspect:
            self.audit.record(at=at, user=user, action="SCAN", outcome="REJECTED",
                              box_id=box_id, detail=suspect)
            incident = self.open_incident(IncidentKind.DUPLICATE_LABEL, user, box_id=box_id,
                                          at=at, note=suspect)
            return ScanResult(ScanOutcome.SUSPECT_DUPLICATE_LABEL,
                              suspect + " Possible duplicate label - hold both boxes.",
                              box=box, incident=incident)

        if to_stage is None:
            self.record_location(box_id, user, location, at=at)
            return ScanResult(ScanOutcome.ACCEPTED, f"{box_id} located at {location}.", box=box)

        to_stage = Stage(to_stage)
        if to_stage is box.stage:
            self.audit.record(at=at, user=user, action="SCAN", box_id=box_id,
                              detail=f"Duplicate scan ignored (already {to_stage.label})")
            return ScanResult(ScanOutcome.DUPLICATE_IGNORED,
                              f"{box_id} is already '{to_stage.label}' - no change.", box=box)
        try:
            self.move(box_id, to_stage, user, location, at=at, method=CaptureMethod.SCAN)
        except (InvalidTransitionError, PermissionDeniedError, ValidationError) as exc:
            return ScanResult(ScanOutcome.REJECTED, str(exc), box=box)
        return ScanResult(ScanOutcome.ACCEPTED, f"{box_id} -> {to_stage.label}.", box=box)

    def reprint_label(self, box_id: str, user: User, reason: str, *,
                      at: Optional[datetime] = None) -> Box:
        """Reprint a damaged label. The ID stays the same; the version increments
        so the old physical label can be identified and destroyed."""
        at = self._now(at)
        box = self.get(box_id)
        self._require_role(user, REPRINT_ROLES, "reprint labels", at, "REPRINT", box.box_id)
        if not (reason or "").strip():
            raise ValidationError("A reason is required to reprint a label.")
        box.label_version += 1
        self.audit.record(at=at, user=user, action="REPRINT", box_id=box.box_id,
                          detail=f"Label v{box.label_version}: {reason.strip()}")
        return box

    def open_incident(self, kind: IncidentKind, user: User, *, box_id: Optional[str] = None,
                      note: str = "", at: Optional[datetime] = None) -> Incident:
        """Log an exception (misplaced box, QC/dispatch error, label problem)."""
        at = self._now(at)
        self._require_role(user, FLOOR_ROLES, "log incidents", at, "INCIDENT", box_id)
        if box_id is not None:
            box_id = self.get(box_id).box_id
        incident = Incident(f"INC-{len(self._incidents) + 1:04d}", IncidentKind(kind), at,
                            user.username, box_id, (note or "").strip())
        self._incidents[incident.incident_id] = incident
        self.audit.record(at=at, user=user, action="INCIDENT_OPEN", box_id=box_id,
                          detail=f"{incident.incident_id} {incident.kind.label}: {incident.note}")
        return incident

    def link_incident(self, incident_id: str, box_id: str, user: User, *,
                      at: Optional[datetime] = None) -> Incident:
        """Attach a box to an incident that was opened without one.

        A damaged-label scan cannot tell which box it was; once the operator
        types the ID printed under the QR code, the incident is linked to that
        box so it shows in the box's history and can be closed there.
        """
        at = self._now(at)
        self._require_role(user, FLOOR_ROLES, "update incidents", at, "INCIDENT_LINK")
        try:
            incident = self._incidents[incident_id]
        except KeyError:
            raise ValidationError(f"No incident '{incident_id}'.") from None
        if incident.box_id is not None:
            raise ValidationError(f"{incident_id} is already linked to {incident.box_id}.")
        box = self.get(box_id)
        incident.box_id = box.box_id
        self.audit.record(at=at, user=user, action="INCIDENT_LINK", box_id=box.box_id,
                          detail=f"{incident_id} linked to {box.box_id}")
        return incident

    def close_incident(self, incident_id: str, user: User, resolution: str, *,
                       at: Optional[datetime] = None,
                       found_location: Optional[str] = None) -> Incident:
        """Close an incident. ``found_location`` updates the box's location
        (e.g. where a misplaced box was found)."""
        at = self._now(at)
        self._require_role(user, FLOOR_ROLES, "close incidents", at, "INCIDENT_CLOSE")
        try:
            incident = self._incidents[incident_id]
        except KeyError:
            raise ValidationError(f"No incident '{incident_id}'.") from None
        if not incident.is_open:
            raise ValidationError(f"{incident_id} is already closed.")
        if not (resolution or "").strip():
            raise ValidationError("A resolution is required to close an incident.")
        if at < incident.opened_at:
            raise ValidationError("Close time is earlier than open time.")
        if found_location:
            location = self._check_location(found_location)
            if incident.box_id:
                box = self._boxes[incident.box_id]
                box.location, box.location_updated_at = location, at
        incident.closed_at, incident.closed_by = at, user.username
        incident.resolution = resolution.strip()
        self.audit.record(at=at, user=user, action="INCIDENT_CLOSE", box_id=incident.box_id,
                          detail=f"{incident_id}: {incident.resolution}")
        return incident
