"""The browser store (web/assets/js/store.js) must apply the same rules as Python.

Runs store.js in Node with the generated rule data and compares its
next-stage options and box-ID checks against models.tracking for every
stage/role combination. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

from models.tracking import (
    MAIN_FLOW, Role, Stage, make_box_id, next_stages, parse_box_id, InvalidBoxIdError,
)
from tests.helpers import box_at_stage, make_tracker, QC, SUPERVISOR

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
global.window = { SG_DATA: input.data, localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
global.document = { dispatchEvent() {} };
global.CustomEvent = class {};
eval(fs.readFileSync(input.store, 'utf8'));
const SG = window.SG;
const out = {
  next: input.boxes.map((b) => Object.fromEntries(input.roles.map((r) => [r, SG.nextStages(b, r)]))),
  ids: input.ids.map((id) => SG.parseBoxId(id).ok),
  made: SG.makeBoxId('BGA', new Date(2026, 9, 25), 7),
};
process.stdout.write(JSON.stringify(out));
"""


def _box_json(box):
    return {"box_id": box.box_id, "sku": box.sku, "history": [
        {"from": e.from_stage.value if e.from_stage else None, "to": e.to_stage.value,
         "at": e.at.isoformat()} for e in box.history]}


@unittest.skipUnless(NODE, "Node.js not installed")
class JsParityTests(unittest.TestCase):
    def test_rules_and_ids_match_python(self):
        from build_demo_data import export
        from models.tracking import SAST
        from datetime import datetime
        data = export(datetime(2026, 10, 25, 12, 0, tzinfo=SAST), seed=1)

        tracker, clock = make_tracker()
        boxes = [box_at_stage(tracker, clock, list(MAIN_FLOW[1:i])) for i in range(1, len(MAIN_FLOW) + 1)]
        held = box_at_stage(tracker, clock, [Stage.AWAITING_QC, Stage.QC_COMPLETE])
        tracker.move(held.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="x")
        reworked = box_at_stage(tracker, clock, [Stage.AWAITING_QC])
        tracker.move(reworked.box_id, Stage.ON_HOLD, QC, "QUARANTINE", note="x")
        tracker.move(reworked.box_id, Stage.IN_PRODUCTION, SUPERVISOR, "PROD")
        boxes += [held, reworked]

        good = [make_box_id(sku, date(2026, 10, d), s)
                for sku in ("BGA", "MSP", "DON") for d in (1, 25) for s in (1, 42, 9999)]
        bad = [g[:-1] + ("0" if g[-1] != "0" else "1") for g in good]
        roles = [r.value for r in Role]

        payload = {"data": data, "store": str(ROOT / "web/assets/js/store.js"),
                   "boxes": [_box_json(b) for b in boxes], "roles": roles, "ids": good + bad}
        result = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(payload),
                                capture_output=True, text=True, check=True)
        out = json.loads(result.stdout)

        for box, js_options in zip(boxes, out["next"]):
            for role in Role:
                with self.subTest(box=box.box_id, stage=box.stage.value, role=role.value):
                    self.assertEqual(js_options[role.value],
                                     [s.value for s in next_stages(box, role)])

        expected = []
        for i in good + bad:
            try:
                parse_box_id(i)
                expected.append(True)
            except InvalidBoxIdError:
                expected.append(False)
        self.assertEqual(out["ids"], expected)
        self.assertEqual(out["made"], make_box_id("BGA", date(2026, 10, 25), 7))


if __name__ == "__main__":
    unittest.main()
