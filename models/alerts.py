"""Stage-dwell exception alerts.

A box that sits in one stage longer than its threshold raises an alert:

* ``WARNING`` once dwell >= the warning threshold (supervisor should look)
* ``CRITICAL`` once dwell >= the critical threshold (escalate / investigate)

Thresholds are per stage. Perishable products (fresh pastes, sprouts) use
``PERISHABLE_FACTOR`` x the normal thresholds because cold-chain time matters
more for them. Finished boxes (dispatched/rejected) never alert.

The default values are starting points for the pilot; replace them with the
P90 dwell times measured during the baseline study.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Iterable, Mapping, Optional

from .products import get_product
from .tracking import Box, Stage, TERMINAL_STAGES


class Severity(IntEnum):
    OK = 0
    WARNING = 1
    CRITICAL = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()


@dataclass(frozen=True)
class DwellThreshold:
    warning: timedelta
    critical: timedelta

    def __post_init__(self) -> None:
        if not timedelta(0) < self.warning < self.critical:
            raise ValueError("Thresholds must satisfy 0 < warning < critical.")

    def scaled(self, factor: float) -> "DwellThreshold":
        return DwellThreshold(self.warning * factor, self.critical * factor)


def _hours(warning: float, critical: float) -> DwellThreshold:
    return DwellThreshold(timedelta(hours=warning), timedelta(hours=critical))


DEFAULT_THRESHOLDS: dict[Stage, DwellThreshold] = {
    Stage.IN_PRODUCTION: _hours(4, 8),
    Stage.AWAITING_QC: _hours(2, 4),
    Stage.QC_COMPLETE: _hours(2, 4),
    Stage.PACKAGING_COMPLETE: _hours(4, 8),
    Stage.READY_FOR_DISPATCH: _hours(12, 24),
    Stage.ON_HOLD: _hours(24, 48),
}

PERISHABLE_FACTOR = 0.5


@dataclass(frozen=True)
class Alert:
    box_id: str
    sku: str
    stage: Stage
    severity: Severity
    dwell: timedelta
    threshold: timedelta
    location: str

    @property
    def message(self) -> str:
        hours = self.dwell.total_seconds() / 3600
        limit = self.threshold.total_seconds() / 3600
        return (f"{self.box_id} has been '{self.stage.label}' for {hours:.1f} h "
                f"({self.severity.label.lower()} limit {limit:g} h) at {self.location}.")


def classify_dwell(dwell: timedelta, threshold: DwellThreshold) -> Severity:
    """Severity for a dwell time against one threshold pair (limits are inclusive)."""
    if dwell >= threshold.critical:
        return Severity.CRITICAL
    if dwell >= threshold.warning:
        return Severity.WARNING
    return Severity.OK


def threshold_for(stage: Stage, sku: str,
                  thresholds: Mapping[Stage, DwellThreshold] = DEFAULT_THRESHOLDS,
                  perishable_factor: float = PERISHABLE_FACTOR) -> Optional[DwellThreshold]:
    """Threshold for a stage/product, or None if the stage never alerts."""
    base = thresholds.get(Stage(stage))
    if base is None:
        return None
    return base.scaled(perishable_factor) if get_product(sku).perishable else base


def evaluate_box(box: Box, now: datetime,
                 thresholds: Mapping[Stage, DwellThreshold] = DEFAULT_THRESHOLDS) -> Optional[Alert]:
    """Alert for one box, or None if it is within limits or finished."""
    if box.stage in TERMINAL_STAGES:
        return None
    threshold = threshold_for(box.stage, box.sku, thresholds)
    if threshold is None:
        return None
    dwell = box.dwell(now)
    severity = classify_dwell(dwell, threshold)
    if severity is Severity.OK:
        return None
    limit = threshold.critical if severity is Severity.CRITICAL else threshold.warning
    return Alert(box.box_id, box.sku, box.stage, severity, dwell, limit, box.location)


def evaluate_alerts(boxes: Iterable[Box], now: datetime,
                    thresholds: Mapping[Stage, DwellThreshold] = DEFAULT_THRESHOLDS) -> list[Alert]:
    """All alerts, most urgent first (critical before warning, longest dwell first)."""
    alerts = [a for a in (evaluate_box(b, now, thresholds) for b in boxes) if a]
    return sorted(alerts, key=lambda a: (-a.severity, -a.dwell.total_seconds()))
