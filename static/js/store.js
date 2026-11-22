/*
 * Browser-side store. window.SG_DATA is loaded from MySQL by
 * <script src="/api/app-data.js"> on every page load, so the database is the
 * system of record. Writes are sent to the Flask API, which applies the
 * process rules (models/tracking.py) and saves to MySQL; the response updates
 * this page's copy. The rule helpers below mirror the Python rules so the UI
 * can show only the moves a role is allowed to make.
 */
(function () {
  'use strict';

  const D = window.SG_DATA;
  const ROLE_KEY = 'sg-role';

  const stageMap = Object.fromEntries(D.stages.map((s) => [s.key, s]));
  const productMap = {};
  const reorderMap = {};
  function indexCatalogue() {
    Object.keys(productMap).forEach((k) => delete productMap[k]);
    (D.products || []).forEach((p) => { productMap[p.sku] = p; });
    Object.keys(reorderMap).forEach((k) => delete reorderMap[k]);
    (D.reorder || []).forEach((r) => { reorderMap[r.sku] = r; });
  }
  indexCatalogue();
  const locationMap = Object.fromEntries(D.locations.map((l) => [l.code, l.label]));
  const roleMap = Object.fromEntries(D.roles.map((r) => [r.key, r]));
  const incidentKindMap = Object.fromEntries(D.incident_kinds.map((k) => [k.key, k.label]));

  // Storage can be blocked (private mode): never let it break the page.
  function readStorage(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function writeStorage(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* role just isn't remembered */ }
  }

  const state = { boxes: D.boxes, incidents: D.incidents, audit: D.audit };

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
  const canRegister = (role) => (D.register_roles || []).includes(role || getRole());
  const canManageCatalogue = (role) => (D.catalogue_roles || []).includes(role || getRole());

  
  // Call the Flask API. Writes (POST/PATCH/PUT) send JSON with the acting
  // username. Resolves to the parsed body, always with ok (+ error on failure).
  async function api(path, body, method = 'POST') {
    const options = { method, headers: { Accept: 'application/json' } };
    if (method !== 'GET') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify({ ...body, username: currentUser().username });
    }
    try {
      const response = await fetch(path, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return { ok: false, status: response.status, error: data.error || `Request failed (${response.status}).` };
      return { ...data, ok: true };
    } catch (e) {
      return { ok: false, error: 'Could not reach the server. Check your connection and try again.' };
    }
  }
  const getJson = (path) => api(path, null, 'GET');

  // Re-read everything from MySQL so this screen shows changes made on other
  // devices (e.g. a box scanned at the QC bay). Pages call this on a timer.
  async function refresh() {
    const fresh = await getJson('/api/app-data');
    if (!fresh.ok) return false;
    state.boxes = fresh.boxes;
    state.incidents = fresh.incidents;
    state.audit = fresh.audit;
    ['products', 'categories', 'reorder', 'kpis', 'generated_at'].forEach((k) => { D[k] = fresh[k]; });
    indexCatalogue();
    document.dispatchEvent(new CustomEvent('sg:refresh'));
    return true;
  }

  function upsert(list, item, key) {
    const index = list.findIndex((x) => x[key] === item[key]);
    if (index >= 0) list[index] = item; else list.push(item);
    return item;
  }
  const keepBox = (box) => (box ? upsert(state.boxes, box, 'box_id') : null);
  const keepIncident = (incident) => upsert(state.incidents, incident, 'id');

  function findBox(id) {
    const key = String(id || '').trim().toUpperCase();
    return state.boxes.find((b) => b.box_id === key) || null;
  }

  async function moveBox(boxId, to, { location, note = '', method = 'scan' } = {}) {
    const result = await api(`/api/boxes/${encodeURIComponent(boxId)}/moves`,
      { to_stage: to, location, note, method });
    return result.ok ? { ok: true, box: keepBox(result.box) } : result;
  }

  async function registerBox({ sku, batch, quantity, location }) {
    const result = await api('/api/boxes', { sku, batch_no: batch, quantity, location });
    return result.ok ? { ok: true, box: keepBox(result.box) } : result;
  }

  async function openIncident({ kind, boxId, note }) {
    const result = await api('/api/incidents', { kind, box_id: boxId || null, note });
    return result.ok ? { ok: true, incident: keepIncident(result.incident) } : result;
  }

  async function closeIncident(id, { resolution, foundLocation }) {
    const result = await api(`/api/incidents/${encodeURIComponent(id)}`,
      { status: 'closed', resolution, found_location: foundLocation || '' }, 'PATCH');
    if (!result.ok) return result;
    keepBox(result.box);
    return { ok: true, incident: keepIncident(result.incident) };
  }

  // A damaged-label incident is linked to its box once the printed ID is typed in.
  async function linkIncident(id, boxId) {
    const result = await api(`/api/incidents/${encodeURIComponent(id)}`, { box_id: boxId }, 'PATCH');
    return result.ok ? { ok: true, incident: keepIncident(result.incident) } : result;
  }

  // Correct a box's batch number / quantity (reason required).
  async function correctBox(boxId, { batch, quantity, reason }) {
    const result = await api(`/api/boxes/${encodeURIComponent(boxId)}`,
      { batch_no: batch, quantity, reason }, 'PATCH');
    return result.ok ? { ok: true, box: keepBox(result.box) } : result;
  }

  async function reprintLabel(boxId, reason) {
    const result = await api(`/api/boxes/${encodeURIComponent(boxId)}/label-reprints`, { reason });
    return result.ok ? { ok: true, box: keepBox(result.box) } : result;
  }

  // Product catalogue and reorder policies (supervisors only; checked by the API).
  const productPath = (sku) => `/api/products/${encodeURIComponent(sku)}`;
  const createProduct = (product) => api('/api/products', product);
  const updateProduct = (sku, changes) => api(productPath(sku), changes, 'PATCH');
  const createCategory = (category) => api('/api/categories', category);

  async function updateReorderPolicy(sku, changes) {
    const result = await api(`${productPath(sku)}/reorder-policy`, changes, 'PATCH');
    if (result.ok) {
      upsert(D.reorder, result.plan, 'sku');
      indexCatalogue();
    }
    return result;
  }

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
    reorderMap, getRole, setRole, currentUser, canWrite, canRegister, canManageCatalogue,
    findBox, moveBox, registerBox, openIncident, closeIncident, linkIncident, correctBox,
    reprintLabel, createProduct, updateProduct, createCategory, updateReorderPolicy,
    api, getJson, refresh, formatDuration, formatDateTime,
  };
})();
