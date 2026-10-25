/*
 * Browser-side data store for the demo.
 *
 * Rules (transitions, prerequisites, role permissions, thresholds) are NOT
 * defined here - they come from window.SG_DATA, which is generated from the
 * Python model (scripts/build_demo_data.py). This file only applies them so
 * the UI behaves the same way the Python module does.
 *
 * State lives in localStorage so changes survive page navigation. It is a
 * per-browser demo convenience, not the system of record.
 */
(function () {
  'use strict';

  const D = window.SG_DATA;
  const STATE_KEY = 'sg-tracker-state-v1';
  const ROLE_KEY = 'sg-role';

  const stageMap = Object.fromEntries(D.stages.map((s) => [s.key, s]));
  const productMap = Object.fromEntries(D.products.map((p) => [p.sku, p]));
  const locationMap = Object.fromEntries(D.locations.map((l) => [l.code, l.label]));
  const roleMap = Object.fromEntries(D.roles.map((r) => [r.key, r]));
  const incidentKindMap = Object.fromEntries(D.incident_kinds.map((k) => [k.key, k.label]));

  // ---- storage helpers (storage can be blocked: never let it break the page)
  function readStorage(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function writeStorage(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* demo only */ }
  }
  function removeStorage(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* demo only */ }
  }

  // ---- seeding: shift generated timestamps so the demo always looks "live"
  function seedState() {
    const shift = Date.now() - Date.parse(D.generated_at);
    const move = (iso) => (iso ? new Date(Date.parse(iso) + shift).toISOString() : iso);
    const clone = JSON.parse(JSON.stringify({ boxes: D.boxes, incidents: D.incidents, audit: D.audit }));
    clone.boxes.forEach((b) => {
      b.created_at = move(b.created_at);
      b.location_updated_at = move(b.location_updated_at);
      b.history.forEach((e) => { e.at = move(e.at); });
    });
    clone.incidents.forEach((i) => { i.opened_at = move(i.opened_at); i.closed_at = move(i.closed_at); });
    clone.audit.forEach((a) => { a.at = move(a.at); });
    clone.seed = D.generated_at;
    return clone;
  }

  function loadState() {
    const raw = readStorage(STATE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed.seed === D.generated_at) return parsed;
      } catch (e) { /* fall through to reseed */ }
    }
    return seedState();
  }

  let state = loadState();
  const save = () => writeStorage(STATE_KEY, JSON.stringify(state));

  // ---- box helpers
  const stageOf = (box) => box.history[box.history.length - 1].to;
  const stageEnteredAt = (box) => box.history[box.history.length - 1].at;
  const isTerminal = (stage) => !!stageMap[stage].terminal;
  const mainIndex = (stage) => D.main_flow.indexOf(stage);
  const stageLabel = (stage) => (stageMap[stage] ? stageMap[stage].label : stage);
  const roleLabel = (role) => (roleMap[role] ? roleMap[role].label : role);

  function heldFrom(box) {
    return stageOf(box) === 'on_hold' ? box.history[box.history.length - 1].from : null;
  }

  function stagesThisCycle(box) {
    let reached = new Set();
    box.history.forEach((e) => {
      if (D.cycle_reset.includes(e.to)) reached = new Set();
      reached.add(e.to);
    });
    return reached;
  }

  function dwellMs(box, now) {
    return Math.max(0, (now || Date.now()) - Date.parse(stageEnteredAt(box)));
  }

  function thresholdFor(stage, sku) {
    const t = D.thresholds[stage];
    if (!t) return null;
    const factor = productMap[sku] && productMap[sku].perishable ? D.perishable_factor : 1;
    return { warning: t.warning_minutes * factor * 60000, critical: t.critical_minutes * factor * 60000 };
  }

  function severity(box, now) {
    const stage = stageOf(box);
    if (isTerminal(stage)) return 'ok';
    const t = thresholdFor(stage, box.sku);
    if (!t) return 'ok';
    const dwell = dwellMs(box, now);
    if (dwell >= t.critical) return 'critical';
    if (dwell >= t.warning) return 'warning';
    return 'ok';
  }

  function alerts(now) {
    now = now || Date.now();
    return state.boxes
      .map((box) => ({ box, severity: severity(box, now), dwell: dwellMs(box, now) }))
      .filter((a) => a.severity !== 'ok')
      .sort((a, b) => (a.severity === b.severity ? b.dwell - a.dwell : a.severity === 'critical' ? -1 : 1));
  }

  // ---- rules: mirror of models.tracking.validate_transition
  function validateTransition(box, to, role) {
    const cur = stageOf(box);
    if (to === cur) return `${box.box_id} is already '${stageLabel(cur)}'.`;
    if (isTerminal(cur)) return `${box.box_id} is '${stageLabel(cur)}'; no further moves are allowed.`;
    if (!D.transitions[cur].includes(to)) {
      const allowed = D.transitions[cur].map(stageLabel).join(', ');
      return `${box.box_id} cannot move from '${stageLabel(cur)}' to '${stageLabel(to)}'. Allowed next: ${allowed}.`;
    }
    if (cur === 'on_hold' && to !== 'rejected') {
      const held = heldFrom(box);
      if (mainIndex(to) > mainIndex(held)) {
        return `${box.box_id} was held at '${stageLabel(held)}' and can only be released to that stage or an earlier one.`;
      }
    }
    const reached = stagesThisCycle(box);
    const missing = (D.prerequisites[to] || []).filter((s) => !reached.has(s));
    if (missing.length) {
      return `${box.box_id} cannot be '${stageLabel(to)}' before completing: ${missing.map(stageLabel).join(', ')}.`;
    }
    const roles = cur === 'on_hold' ? D.hold_release_roles : D.transition_roles[to];
    if (!roles.includes(role)) {
      return `A ${roleLabel(role)} may not move a box from '${stageLabel(cur)}' to '${stageLabel(to)}'.`;
    }
    return null;
  }

  function nextStages(box, role) {
    const order = D.main_flow.concat(['on_hold', 'rejected']);
    return order.filter((s) => validateTransition(box, s, role) === null);
  }

  // ---- box IDs: mirror of models.tracking.check_character / parse_box_id
  const ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const BOX_ID_RE = /^SG-([A-Z]{3})-(\d{6})-(\d{4})-([0-9A-Z])$/;

  function checkCharacter(payload) {
    const n = ALPHABET.length;
    let factor = 2;
    let total = 0;
    for (let i = payload.length - 1; i >= 0; i -= 1) {
      const addend = factor * ALPHABET.indexOf(payload[i]);
      factor = factor === 2 ? 1 : 2;
      total += Math.floor(addend / n) + (addend % n);
    }
    return ALPHABET[(n - (total % n)) % n];
  }

  /** Returns {ok, id} or {ok:false, damaged, error}. */
  function parseBoxId(raw) {
    const text = String(raw || '').trim().toUpperCase();
    const m = BOX_ID_RE.exec(text);
    if (!m) {
      return { ok: false, damaged: false, error: 'Use the format SG-XXX-YYMMDD-NNNN-C, e.g. SG-BGA-261025-0007-K.' };
    }
    if (checkCharacter(m[1] + m[2] + m[3]) !== m[4]) {
      return { ok: false, damaged: true, error: 'The check character does not match. The label may be damaged or a character was mistyped.' };
    }
    return { ok: true, id: text };
  }

  function makeBoxId(sku, date, sequence) {
    const yymmdd = String(date.getFullYear()).slice(2) +
      String(date.getMonth() + 1).padStart(2, '0') + String(date.getDate()).padStart(2, '0');
    const seq = String(sequence).padStart(4, '0');
    return `SG-${sku}-${yymmdd}-${seq}-${checkCharacter(sku + yymmdd + seq)}`;
  }

  // ---- role / user
  function getRole() {
    const r = readStorage(ROLE_KEY);
    return roleMap[r] ? r : 'operator';
  }
  function setRole(role) {
    if (!roleMap[role]) return;
    writeStorage(ROLE_KEY, role);
    document.dispatchEvent(new CustomEvent('sg:rolechange', { detail: { role } }));
  }
  const currentUser = () => ({ username: roleMap[getRole()].user, role: getRole() });
  const canWrite = (role) => D.floor_roles.includes(role || getRole());

  // ---- mutations
  function audit(action, outcome, boxId, detail) {
    const u = currentUser();
    state.audit.push({ seq: state.audit.length + 1, at: new Date().toISOString(), user: u.username,
      role: u.role, action, outcome, box_id: boxId, detail, hash: 'browser' });
  }

  function findBox(id) {
    const key = String(id || '').trim().toUpperCase();
    return state.boxes.find((b) => b.box_id === key) || null;
  }

  function moveBox(boxId, to, { location, note = '', method = 'scan' } = {}) {
    const box = findBox(boxId);
    if (!box) return { ok: false, error: `No box registered as '${boxId}'.` };
    if (!locationMap[location]) return { ok: false, error: 'Choose a valid location.' };
    if (D.note_required.includes(to) && !note.trim()) {
      return { ok: false, error: `A reason is required to mark a box '${stageLabel(to)}'.` };
    }
    const u = currentUser();
    const error = validateTransition(box, to, u.role);
    if (error) {
      audit('MOVE', 'REJECTED', box.box_id, error);
      save();
      return { ok: false, error };
    }
    const from = stageOf(box);
    const at = new Date().toISOString();
    box.history.push({ from, to, at, user: u.username, role: u.role, location, method, note: note.trim() });
    box.location = location;
    box.location_updated_at = at;
    audit('MOVE', 'OK', box.box_id, `${stageLabel(from)} -> ${stageLabel(to)} at ${location} (${method})`);
    save();
    return { ok: true, box };
  }

  function registerBox({ sku, batch, quantity, location }) {
    const u = currentUser();
    if (!D.register_roles.includes(u.role)) {
      return { ok: false, error: `A ${roleLabel(u.role)} may not register boxes.` };
    }
    const now = new Date();
    const prefix = makeBoxId(sku, now, 1).slice(0, 14); // SG-XXX-YYMMDD-
    const used = state.boxes.filter((b) => b.box_id.startsWith(prefix))
      .map((b) => parseInt(b.box_id.slice(14, 18), 10));
    const sequence = (used.length ? Math.max(...used) : 0) + 1;
    const boxId = makeBoxId(sku, now, sequence);
    const at = now.toISOString();
    const box = {
      box_id: boxId, sku, batch_no: batch.toUpperCase(), quantity, created_at: at, label_version: 1,
      location, location_updated_at: at,
      history: [{ from: null, to: 'in_production', at, user: u.username, role: u.role, location, method: 'manual', note: 'Box registered' }],
    };
    state.boxes.push(box);
    audit('REGISTER', 'OK', boxId, `${productMap[sku].name}, batch ${box.batch_no}, qty ${quantity} at ${location}`);
    save();
    return { ok: true, box };
  }

  function openIncident({ kind, boxId, note }) {
    const u = currentUser();
    if (!canWrite(u.role)) return { ok: false, error: `A ${roleLabel(u.role)} may not log incidents.` };
    const incident = {
      id: `INC-${String(state.incidents.length + 1).padStart(4, '0')}`, kind, box_id: boxId || null,
      note: note.trim(), opened_at: new Date().toISOString(), opened_by: u.username,
      closed_at: null, closed_by: null, resolution: '',
    };
    state.incidents.push(incident);
    audit('INCIDENT_OPEN', 'OK', incident.box_id, `${incident.id} ${incidentKindMap[kind]}: ${incident.note}`);
    save();
    return { ok: true, incident };
  }

  function closeIncident(id, { resolution, foundLocation }) {
    const u = currentUser();
    if (!canWrite(u.role)) return { ok: false, error: `A ${roleLabel(u.role)} may not close incidents.` };
    const inc = state.incidents.find((i) => i.id === id);
    if (!inc || inc.closed_at) return { ok: false, error: 'Incident not found or already closed.' };
    inc.closed_at = new Date().toISOString();
    inc.closed_by = u.username;
    inc.resolution = resolution.trim();
    if (foundLocation && inc.box_id) {
      const box = findBox(inc.box_id);
      box.location = foundLocation;
      box.location_updated_at = inc.closed_at;
    }
    audit('INCIDENT_CLOSE', 'OK', inc.box_id, `${id}: ${inc.resolution}`);
    save();
    return { ok: true, incident: inc };
  }

  // Mirror of BoxTracker.link_incident: attach a box to an incident opened without one.
  function linkIncident(id, boxId) {
    const u = currentUser();
    if (!canWrite(u.role)) return { ok: false, error: `A ${roleLabel(u.role)} may not update incidents.` };
    const inc = state.incidents.find((i) => i.id === id);
    const box = findBox(boxId);
    if (!inc || inc.box_id || !box) return { ok: false, error: 'Incident cannot be linked.' };
    inc.box_id = box.box_id;
    audit('INCIDENT_LINK', 'OK', box.box_id, `${id} linked to ${box.box_id}`);
    save();
    return { ok: true, incident: inc };
  }

  function reset() {
    removeStorage(STATE_KEY);
    state = seedState();
    save();
  }

  // ---- formatting
  function formatDuration(ms) {
    const mins = Math.floor(ms / 60000);
    if (mins < 60) return `${mins} min`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} h ${String(mins % 60).padStart(2, '0')} min`;
    return `${Math.floor(hours / 24)} d ${hours % 24} h`;
  }
  const dtFormat = new Intl.DateTimeFormat('en-ZA', { dateStyle: 'medium', timeStyle: 'short' });
  const formatDateTime = (iso) => (iso ? dtFormat.format(new Date(iso)) : '-');

  window.SG = {
    data: D,
    get boxes() { return state.boxes; },
    get incidents() { return state.incidents; },
    get audit() { return state.audit; },
    stageMap, productMap, locationMap, roleMap, incidentKindMap,
    stageOf, stageEnteredAt, stageLabel, roleLabel, isTerminal, heldFrom, dwellMs, thresholdFor,
    severity, alerts, validateTransition, nextStages, parseBoxId, checkCharacter, makeBoxId,
    getRole, setRole, currentUser, canWrite, findBox, moveBox, registerBox, openIncident,
    closeIncident, linkIncident, reset, formatDuration, formatDateTime,
  };
})();
