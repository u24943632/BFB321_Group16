# Simply Garlic box tracking: process rules

These rules are enforced in code by `models/tracking.py` and `models/alerts.py`.
The browser loads the same rules from `web/assets/js/demo-data.js`, which is
generated from the Python model. `tests/test_js_parity.py` checks that the two match.

## 1. Unique identity per box

- Every physical box or batch gets its own ID at registration. A QR code per
  product is not enough, because it can't tell two boxes of the same product apart.
- Format: `SG-<SKU>-<YYMMDD>-<SEQ>-<CHECK>`, for example `SG-BGA-261025-0007-K`.
  - `SKU`: three-letter product code (see `models/products.py`).
  - `YYMMDD`: registration date (SAST). `SEQ`: 0001–9999 per product per day.
  - `CHECK`: Luhn mod-36 check character. It catches every single-character
    error and most swapped neighbouring characters, so a damaged or mistyped
    label is rejected instead of matching the wrong box.
- The ID is printed as a QR code **and** as text, so a box can still be
  identified when the QR code is damaged.

## 2. Stages and valid transitions

```
In production → Awaiting QC → QC complete → Packaging complete → Ready for dispatch → Dispatched
        └──────────── any working stage → On hold → (release) or Rejected ────────────┘
```

| Rule | Enforcement |
|---|---|
| Boxes only move forward one stage at a time | `ALLOWED_TRANSITIONS` |
| *Ready for dispatch* requires *QC complete* **and** *Packaging complete* in the current cycle | `PREREQUISITES` |
| A box released from hold can only go back to the stage it was held at, or an earlier one | `validate_transition` |
| Rework (back to production) or re-inspection (back to awaiting QC) restarts the cycle, so QC must be passed again | `stages_this_cycle` |
| *On hold* and *Rejected* need a written reason | `NOTE_REQUIRED_STAGES` |
| Dispatched and Rejected are final | `TERMINAL_STAGES` |
| Timestamps can never go backwards | `BoxTracker.move` |

Every change is stored as an immutable `StatusEvent` (from, to, time, user,
role, location, capture method, note). The full history is kept for every box.

## 3. User access

| Action | Operator | QC inspector | Supervisor | Executive |
|---|:-:|:-:|:-:|:-:|
| Register box / print label | ✔ | | ✔ | |
| Move to Awaiting QC | ✔ | ✔ | ✔ | |
| Sign off QC complete | | ✔ | ✔ | |
| Packaging complete / Ready / Dispatched | ✔ | | ✔ | |
| Put on hold | ✔ | ✔ | ✔ | |
| Release from hold / Reject / Rework | | | ✔ | |
| Log or close incidents, location check-in | ✔ | ✔ | ✔ | |
| Reprint label | ✔ | | ✔ | |
| View dashboards, history, KPIs | ✔ | ✔ | ✔ | ✔ |

Executives are read-only. In production, users sign in with their own accounts
(no shared logins), so every audit entry can be traced to a person.
(The demo frontend uses a role switcher instead of real sign-in.)

## 4. Exception-alert thresholds

A box raises an alert when it stays in one stage too long. Limits are inclusive.

| Stage | Warning | Critical |
|---|---|---|
| In production | 4 h | 8 h |
| Awaiting QC | 2 h | 4 h |
| QC complete | 2 h | 4 h |
| Packaging complete | 4 h | 8 h |
| Ready for dispatch | 12 h | 24 h |
| On hold | 24 h | 48 h |

Perishable products (garlic & ginger tubs, ginger squeeze tubes, ginger tubs
and mixed sprouts) use **half** these limits. Warnings go to the shift
supervisor. A critical alert means the box should be found and actioned within
30 minutes. **These are starting values.** After the baseline study, set each
warning limit to the measured 90th-percentile dwell time for that stage.

## 5. Scan compliance

- Every stage change should be made by scanning the box QR code at the
  station. Typing an ID in is allowed as a fallback, but it is recorded as
  `manual`.
- Scan compliance = scanned stage changes ÷ all stage changes. **Target ≥ 95%.**
  If it is below 90% for a shift, the supervisor reviews why (damaged labels,
  scanner faults, or the scan step being skipped).
- Scanning a box into the stage it is already in is ignored and logged. It is
  never counted twice.

## 6. Duplicate and damaged QR handling

| Situation | System response | Operator action |
|---|---|---|
| QR unreadable or check character wrong | Scan rejected, *damaged label* incident opened | Type the printed ID; supervisor/operator reprints the label (version +1) and destroys the old one |
| Valid ID that was never registered | Scan rejected, *unknown label* incident | Quarantine the box and tell the supervisor |
| Label of a dispatched/rejected box scanned again | *Duplicate label* incident | Hold both boxes and investigate |
| Same label scanned at two locations less than 60 s apart | *Duplicate label* incident | Hold both boxes and investigate |
| Same box scanned twice at the same stage | Ignored (idempotent) | None |

Reprinting keeps the same box ID and increases `label_version`, so the history
stays continuous.

## 7. Audit trail

- Every action is written to an append-only audit log, **including rejected
  attempts** (wrong role, invalid move). Each entry records time, user, role,
  action, box and outcome.
- Each entry contains the SHA-256 hash of the previous entry. If any past entry
  is edited or deleted, `AuditLog.verify()` fails.
- Retention: keep for at least the product shelf life plus 12 months, so a
  customer complaint or recall can be traced back.

## 8. Physical location capture

The system claims **last confirmed scan location**, not live position:

- Every station, rack and dock has a fixed location QR tag (`LOCATIONS` in
  `models/tracking.py`). There is one standard location per stage: production,
  QC staging rack, QC bay, packing station, dispatch staging, dispatch dock,
  quarantine and waste.
- A box's location is updated only when it is scanned together with a location
  tag: on every stage change, on a location check-in scan (box moved without
  changing stage), or when a misplaced box is found.
- The UI always shows the location **with its timestamp**, so users can see
  how old the information is.
- Live tracking (RFID or BLE) is out of scope. If it is added later, it can
  feed the same `record_location` call.

## 9. Evaluation method (before vs after)

1. **Baseline (2 weeks, current process):** count boxes dispatched, record
   every search for a missing or mislabelled box with a stopwatch
   (investigation time), time 30+ random "where is box X?" questions
   (status retrieval), take stage dwell times from paper records, log
   quality/dispatch errors, and note how many dispatched boxes have a complete
   paper trail (traceability). Record rostered labour hours.
2. **Pilot (2 weeks, tracker):** the same measures, taken from the tracker data
   (`metrics.snapshot_from_tracker`).
3. **Compare** with `metrics.compare()`. It reports % improvement per KPI and
   accounts for whether higher or lower is better. Present investigation time,
   tracking incidents, traceability coverage, stage dwell time and labour
   utilisation as the headline results.

The baseline figures in `scripts/build_demo_data.py` are **samples**. Replace
them with the measured study.
