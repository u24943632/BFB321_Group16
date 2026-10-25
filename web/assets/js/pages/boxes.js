(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;
  const params = new URLSearchParams(window.location.search);

  SGUI.navbar('boxes.html');

  const $ = (id) => document.getElementById(id);
  const filters = { search: $('f-search'), stage: $('f-stage'), product: $('f-product'), alerts: $('f-alerts'), done: $('f-done') };

  SG.data.stages.forEach((s) => filters.stage.insertAdjacentHTML('beforeend', `<option value="${s.key}">${esc(s.label)}</option>`));
  SG.data.products.forEach((p) => filters.product.insertAdjacentHTML('beforeend', `<option value="${p.sku}">${esc(p.name)}</option>`));
  $('product-codes').innerHTML = SG.data.products.map((p) =>
    `<li class="list-inline-item me-3"><span class="mono fw-semibold">${p.sku}</span> ${esc(p.name)}</li>`).join('');
  SG.data.incident_kinds.forEach((k) => $('ex-kind').insertAdjacentHTML('beforeend', `<option value="${k.key}">${esc(k.label)}</option>`));
  if (params.get('stage')) filters.stage.value = params.get('stage');

  const SEV_RANK = { ok: 0, warning: 1, critical: 2 };
  const table = SGUI.sortableTable($('board'), {
    caption: 'Select a column heading to sort. Time in stage updates every minute.',
    sortKey: 'dwell', sortDir: 'desc',
    emptyText: 'No boxes match these filters.',
    rowClass: (r) => (r.severity !== 'ok' ? `row-${r.severity}` : ''),
    columns: [
      { key: 'box_id', label: 'Box ID', rowHeader: true, render: (r) => `<span class="mono">${esc(r.box_id)}</span>` },
      { key: 'product', label: 'Product' },
      { key: 'batch_no', label: 'Batch', className: 'd-none d-lg-table-cell' },
      { key: 'quantity', label: 'Qty', className: 'd-none d-lg-table-cell text-end' },
      { key: 'stage', label: 'Stage', render: (r) => SGUI.stageBadge(r.stage), sortValue: (r) => SG.data.stages.findIndex((s) => s.key === r.stage) },
      { key: 'location', label: 'Last scan location', className: 'd-none d-md-table-cell' },
      { key: 'dwell', label: 'Time in stage', render: (r) => esc(SG.formatDuration(r.dwell)) },
      { key: 'severity', label: 'Status', render: (r) => SGUI.severityBadge(r.severity), sortValue: (r) => SEV_RANK[r.severity] },
      { key: 'actions', label: 'Actions', sortable: false,
        render: (r) => `<button type="button" class="btn btn-sm btn-outline-primary" data-box="${esc(r.box_id)}">View<span class="visually-hidden"> ${esc(r.box_id)}</span></button>` },
    ],
  });

  function rows() {
    const now = Date.now();
    const q = filters.search.value.trim().toUpperCase();
    return SG.boxes.filter((b) => {
      const stage = SG.stageOf(b);
      if (filters.stage.value) { if (stage !== filters.stage.value) return false; }
      else if (!filters.done.checked && SG.isTerminal(stage)) return false;
      if (filters.product.value && b.sku !== filters.product.value) return false;
      if (q && !b.box_id.includes(q) && !b.batch_no.includes(q)) return false;
      if (filters.alerts.checked && SG.severity(b, now) === 'ok') return false;
      return true;
    }).map((b) => ({
      box_id: b.box_id, product: SG.productMap[b.sku].name, batch_no: b.batch_no, quantity: b.quantity,
      stage: SG.stageOf(b), location: SG.locationMap[b.location] || b.location,
      dwell: SG.isTerminal(SG.stageOf(b)) ? 0 : SG.dwellMs(b, now), severity: SG.severity(b, now),
    }));
  }

  function refresh() {
    const r = rows();
    table.update(r);
    $('result-count').textContent = `${r.length} box${r.length === 1 ? '' : 'es'}`;
  }

  $('filters').addEventListener('input', refresh);
  $('filters').addEventListener('submit', (e) => { e.preventDefault(); refresh(); });

  // ---- detail modal
  const modalEl = $('box-modal');
  const modal = new bootstrap.Modal(modalEl);
  let currentBoxId = null;

  function renderModal() {
    const box = SG.findBox(currentBoxId);
    if (!box) return;
    const now = Date.now();
    const stage = SG.stageOf(box);
    $('box-modal-title').textContent = box.box_id;
    $('box-update-link').href = `scan.html?box=${encodeURIComponent(box.box_id)}`;
    $('box-summary').innerHTML = `
      <dl class="row mb-0">
        <dt class="col-sm-4">Product</dt><dd class="col-sm-8">${esc(SG.productMap[box.sku].name)}</dd>
        <dt class="col-sm-4">Batch / quantity</dt><dd class="col-sm-8">${esc(box.batch_no)} · ${box.quantity} units</dd>
        <dt class="col-sm-4">Current stage</dt><dd class="col-sm-8">${SGUI.stageBadge(stage)} ${SG.isTerminal(stage) ? '' : SGUI.severityBadge(SG.severity(box, now))}</dd>
        <dt class="col-sm-4">Time in stage</dt><dd class="col-sm-8">${SG.isTerminal(stage) ? 'Finished' : esc(SG.formatDuration(SG.dwellMs(box, now)))}</dd>
        <dt class="col-sm-4">Last scan location</dt><dd class="col-sm-8">${esc(SG.locationMap[box.location] || box.location)}
          <span class="text-muted small">(${esc(SG.formatDateTime(box.location_updated_at))})</span></dd>
        <dt class="col-sm-4">Label version</dt><dd class="col-sm-8">v${box.label_version}${box.label_version > 1 ? ' (reprinted)' : ''}</dd>
      </dl>`;

    $('box-history').innerHTML = box.history.slice().reverse().map((e, idx, arr) => {
      const i = box.history.length - 1 - idx;
      const end = i + 1 < box.history.length ? Date.parse(box.history[i + 1].at) : (SG.isTerminal(e.to) ? null : now);
      const spent = end ? SG.formatDuration(end - Date.parse(e.at)) : null;
      return `<li>
        <div class="d-flex flex-wrap align-items-center gap-2">${SGUI.stageBadge(e.to)}
          <time class="small" datetime="${esc(e.at)}">${esc(SG.formatDateTime(e.at))}</time>
          ${spent ? `<span class="small text-muted">· ${esc(spent)} in stage${i === box.history.length - 1 ? ' so far' : ''}</span>` : ''}</div>
        <div class="small text-muted mt-1">${esc(e.user)} (${esc(SG.roleLabel(e.role))}) at ${esc(SG.locationMap[e.location] || e.location)} ·
          ${e.method === 'scan' ? '<i class="bi bi-qr-code" aria-hidden="true"></i> scanned' : '<i class="bi bi-keyboard" aria-hidden="true"></i> manual entry'}</div>
        ${e.note ? `<div class="small">${esc(e.note)}</div>` : ''}
      </li>`;
    }).join('');

    const incidents = SG.incidents.filter((i) => i.box_id === box.box_id);
    const canWrite = SG.canWrite();
    $('box-incidents').innerHTML = incidents.length ? incidents.map((i) => `
      <div class="border rounded p-3 mb-2">
        <div class="d-flex justify-content-between flex-wrap gap-2">
          <strong>${esc(i.id)} · ${esc(SG.incidentKindMap[i.kind])}</strong>
          <span class="small">${i.closed_at ? `Closed after ${esc(SG.formatDuration(Date.parse(i.closed_at) - Date.parse(i.opened_at)))}` : '<span class="text-danger fw-semibold">Open</span>'}</span>
        </div>
        <div class="small text-muted">${esc(i.note)}</div>
        ${i.closed_at ? `<div class="small">Resolution: ${esc(i.resolution)}</div>` : canWrite ? `
          <form class="row g-2 mt-1 close-incident" data-id="${esc(i.id)}" novalidate>
            <div class="col-sm-5">
              <label class="form-label small" for="res-${esc(i.id)}">Resolution</label>
              <input class="form-control form-control-sm" id="res-${esc(i.id)}" required minlength="5" aria-describedby="res-${esc(i.id)}-error">
              <div class="invalid-feedback" id="res-${esc(i.id)}-error"></div>
            </div>
            <div class="col-sm-4">
              <label class="form-label small" for="loc-${esc(i.id)}">Found at (optional)</label>
              <select class="form-select form-select-sm" id="loc-${esc(i.id)}"><option value="">Not moved</option>
                ${SG.data.locations.map((l) => `<option value="${l.code}">${esc(l.label)}</option>`).join('')}</select>
            </div>
            <div class="col-sm-3 d-flex align-items-end"><button class="btn btn-sm btn-outline-primary w-100" type="submit">Close incident</button></div>
          </form>` : ''}
      </div>`).join('') : '<p class="text-muted small">No incidents for this box.</p>';

    $('exception-form').classList.toggle('d-none', !canWrite);
    $('exception-readonly').classList.toggle('d-none', canWrite);
  }

  function openBox(boxId) {
    currentBoxId = boxId;
    renderModal();
    modal.show();
  }

  $('board').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-box]');
    if (btn) openBox(btn.dataset.box);
  });

  $('exception-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const form = e.target;
    const ok = SGUI.validateForm(form, {
      'ex-kind': (f) => (f.value ? '' : 'Choose the type of exception.'),
      'ex-note': (f) => (f.value.trim().length < 10 ? 'Describe what happened in at least 10 characters.' : ''),
    });
    if (!ok) return;
    const result = SG.openIncident({ kind: $('ex-kind').value, boxId: currentBoxId, note: $('ex-note').value });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    form.reset();
    form.querySelectorAll('.is-invalid').forEach((f) => f.classList.remove('is-invalid'));
    SGUI.toast(`${result.incident.id} logged for ${currentBoxId}.`);
    renderModal();
    refresh();
  });

  $('box-incidents').addEventListener('submit', (e) => {
    const form = e.target.closest('form.close-incident');
    if (!form) return;
    e.preventDefault();
    const id = form.dataset.id;
    const ok = SGUI.validateForm(form, {
      [`res-${id}`]: (f) => (f.value.trim().length < 5 ? 'Say how it was resolved (at least 5 characters).' : ''),
    });
    if (!ok) return;
    const result = SG.closeIncident(id, { resolution: $(`res-${id}`).value, foundLocation: $(`loc-${id}`).value });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    SGUI.toast(`${id} closed.`);
    renderModal();
    refresh();
  });

  document.addEventListener('sg:rolechange', () => { if (currentBoxId) renderModal(); });
  window.setInterval(refresh, 60000);
  refresh();

  if (params.get('box')) {
    const box = SG.findBox(params.get('box'));
    if (box) openBox(box.box_id);
    else SGUI.toast(`No box found for ${params.get('box')}.`, 'warning');
  }
})();
