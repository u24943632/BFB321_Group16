"""Process KPIs for the before/after evaluation.

A :class:`ProcessSnapshot` holds the raw counts for one measurement period.
The *baseline* snapshot is filled in by hand from the time-and-motion study
of the current (paper/manual) process; the *after* snapshot can be computed
from tracker data with :func:`snapshot_from_tracker`. :func:`compare` then
reports the change in each KPI, taking into account whether higher or lower
is better.

KPI formulas (all per measurement period):

* throughput_per_day          = boxes_dispatched / period_days
* tracking_incident_rate_pct  = tracking_incidents / boxes_dispatched x 100
  (lost, misplaced, mislabelled, damaged, duplicate or unknown labels)
* mean_investigation_minutes  = investigation_minutes / investigations
* mean_status_retrieval_seconds  (measured directly - stopwatch or app logs)
* mean_stage_dwell_hours      = mean time a box spends in one stage
* process_error_rate_pct      = (quality_errors + dispatch_errors) / boxes_dispatched x 100
* traceability_coverage_pct   = traced_boxes / boxes_dispatched x 100
  (boxes with an unbroken status history from production to dispatch)
* labour_utilisation_pct      = (labour_hours - non_value_hours) / labour_hours x 100
  where non_value_hours = investigation time + time spent finding statuses
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Iterable, Optional

from .tracking import (
    MAIN_FLOW, TRACKING_INCIDENT_KINDS, BoxTracker, Box, CaptureMethod, IncidentKind, Stage,
)


@dataclass(frozen=True)
class ProcessSnapshot:
    label: str
    period_days: float
    boxes_dispatched: int
    tracking_incidents: int
    investigations: int
    investigation_minutes: float
    status_lookups: int
    mean_status_retrieval_seconds: float
    mean_stage_dwell_hours: float
    quality_errors: int
    dispatch_errors: int
    traced_boxes: int
    labour_hours: float

    def __post_init__(self) -> None:
        if self.period_days <= 0:
            raise ValueError("period_days must be positive.")
        if self.labour_hours <= 0:
            raise ValueError("labour_hours must be positive.")
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{f.name} cannot be negative.")
        if self.traced_boxes > self.boxes_dispatched:
            raise ValueError("traced_boxes cannot exceed boxes_dispatched.")

    @staticmethod
    def _pct(part: float, whole: float) -> float:
        return part / whole * 100 if whole else 0.0

    @property
    def throughput_per_day(self) -> float:
        return self.boxes_dispatched / self.period_days

    @property
    def tracking_incident_rate_pct(self) -> float:
        return self._pct(self.tracking_incidents, self.boxes_dispatched)

    @property
    def mean_investigation_minutes(self) -> float:
        return self.investigation_minutes / self.investigations if self.investigations else 0.0

    @property
    def process_error_rate_pct(self) -> float:
        return self._pct(self.quality_errors + self.dispatch_errors, self.boxes_dispatched)

    @property
    def traceability_coverage_pct(self) -> float:
        return self._pct(self.traced_boxes, self.boxes_dispatched)

    @property
    def non_value_hours(self) -> float:
        lookups = self.status_lookups * self.mean_status_retrieval_seconds / 3600
        return self.investigation_minutes / 60 + lookups

    @property
    def labour_utilisation_pct(self) -> float:
        return max(0.0, self._pct(self.labour_hours - self.non_value_hours, self.labour_hours))

    def kpis(self) -> dict[str, float]:
        return {k.key: getattr(self, k.key) for k in KPIS}


@dataclass(frozen=True)
class KpiDefinition:
    key: str
    label: str
    unit: str
    higher_is_better: bool


KPIS: tuple[KpiDefinition, ...] = (
    KpiDefinition("throughput_per_day", "Box throughput", "boxes/day", True),
    KpiDefinition("tracking_incident_rate_pct", "Lost / misplaced / mislabelled labels",
                  "% of boxes", False),
    KpiDefinition("mean_investigation_minutes", "Investigation time per incident", "min", False),
    KpiDefinition("mean_status_retrieval_seconds", "Status retrieval time", "s", False),
    KpiDefinition("mean_stage_dwell_hours", "Stage dwell time (mean)", "h", False),
    KpiDefinition("process_error_rate_pct", "Quality & dispatch errors", "% of boxes", False),
    KpiDefinition("traceability_coverage_pct", "Traceability coverage", "% of boxes", True),
    KpiDefinition("labour_utilisation_pct", "Labour utilisation", "% productive", True),
)


@dataclass(frozen=True)
class KpiComparison:
    kpi: KpiDefinition
    baseline: float
    current: float
    change_pct: Optional[float]       # raw % change; None if baseline is 0
    improvement_pct: Optional[float]  # positive = better, whichever direction is good

    @property
    def improved(self) -> bool:
        if self.kpi.higher_is_better:
            return self.current > self.baseline
        return self.current < self.baseline


def compare(baseline: ProcessSnapshot, current: ProcessSnapshot) -> list[KpiComparison]:
    """Before/after comparison for every KPI in :data:`KPIS`."""
    result = []
    for kpi in KPIS:
        before, after = getattr(baseline, kpi.key), getattr(current, kpi.key)
        change = (after - before) / before * 100 if before else None
        improvement = None if change is None else (change if kpi.higher_is_better else -change)
        result.append(KpiComparison(kpi, before, after, change, improvement))
    return result


def scan_compliance_pct(boxes: Iterable[Box], start: datetime, end: datetime) -> float:
    """Share of stage changes in [start, end) captured by QR scan, not typed in.

    Registration events are excluded (labels are printed at that moment).
    """
    events = [e for b in boxes for e in b.history
              if e.from_stage is not None and start <= e.at < end]
    if not events:
        return 0.0
    return sum(e.method is CaptureMethod.SCAN for e in events) / len(events) * 100


def mean_dwell_by_stage(boxes: Iterable[Box], start: datetime, end: datetime) -> dict[Stage, float]:
    """Mean completed dwell (hours) per working stage, for stays ending in [start, end)."""
    totals: dict[Stage, list[float]] = {}
    for box in boxes:
        for occ in box.occupancies():
            if occ.left_at is None or not start <= occ.left_at < end:
                continue
            totals.setdefault(occ.stage, []).append(occ.duration().total_seconds() / 3600)
    return {s: sum(v) / len(v) for s, v in totals.items()}


def snapshot_from_tracker(tracker: BoxTracker, *, label: str, start: datetime, end: datetime,
                          labour_hours: float, status_lookups: int,
                          mean_status_retrieval_seconds: float) -> ProcessSnapshot:
    """Build an *after* snapshot from tracker records for the period [start, end).

    Labour hours and status-lookup timings come from rosters / app logs,
    so they are passed in rather than inferred.
    """
    boxes = tracker.boxes
    dispatched = [b for b in boxes
                  if any(e.to_stage is Stage.DISPATCHED and start <= e.at < end for e in b.history)]
    incidents = [i for i in tracker.incidents if start <= i.opened_at < end]
    tracking = [i for i in incidents if i.kind in TRACKING_INCIDENT_KINDS]
    investigated = [i for i in tracking if i.investigation_time is not None]

    stays = [occ.duration().total_seconds() / 3600
             for b in boxes for occ in b.occupancies()
             if occ.stage in MAIN_FLOW[:-1] and occ.left_at is not None
             and start <= occ.left_at < end]

    return ProcessSnapshot(
        label=label,
        period_days=(end - start).total_seconds() / 86400,
        boxes_dispatched=len(dispatched),
        tracking_incidents=len(tracking),
        investigations=len(investigated),
        investigation_minutes=sum(i.investigation_time.total_seconds() / 60 for i in investigated),
        status_lookups=status_lookups,
        mean_status_retrieval_seconds=mean_status_retrieval_seconds,
        mean_stage_dwell_hours=sum(stays) / len(stays) if stays else 0.0,
        quality_errors=sum(i.kind is IncidentKind.QUALITY_ERROR for i in incidents),
        dispatch_errors=sum(i.kind is IncidentKind.DISPATCH_ERROR for i in incidents),
        traced_boxes=sum(b.has_complete_trace() for b in dispatched),
        labour_hours=labour_hours,
    )
