(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;
  const $ = (id) => document.getElementById(id);

  SGUI.navbar('scan.html');

  const idInput = $('scan-id');
  const moveForm = $('move-form');
  let box = null;
  const recent = [];
  // Damaged-label incident waiting for the operator to type the printed ID.
  let pendingIncident = null;
  let lastLoggedText = '';

  function setIdError(message) {
    idInput.classList.toggle('is-invalid', !!message);
    idInput.setAttribute('aria-invalid', String(!!message));
    $('scan-id-error').textContent = message || '';
  }

  /** Open an incident for a bad scan; read-only roles and repeat presses log nothing. */
  function logIncident(kind, text, note) {
    if (!SG.canWrite() || text === lastLoggedText) return null;
    const result = SG.openIncident({ kind, boxId: null, note });
    if (!result.ok) return null;
    lastLoggedText = text;
    return result.incident;
  }

  function updateReadonly() {
    $('readonly-notice').classList.toggle('d-none', SG.canWrite());
  }

  function selectedStage() {
    const checked = moveForm.querySelector('input[name="to-stage"]:checked');
    return checked ? checked.value : '';
  }

  function fillLocations(stage) {
    const suggested = SG.data.stage_locations[stage] || [];
    const options = SG.data.locations.map((l) =>
      `<option value="${l.code}">${esc(l.label)} (${l.code})${suggested.includes(l.code) ? ' - standard for this stage' : ''}</option>`);
    $('scan-loc').innerHTML = `<option value="">Choose location…</option>${options.join('')}`;
    if (suggested.length) $('scan-loc').value = suggested[0];
  }

  function renderBox() {
    const stage = SG.stageOf(box);
    const now = Date.now();
    $('box-status').innerHTML = `
      <div class="border rounded p-3">
        <div class="d-flex flex-wrap justify-content-between gap-2">
          <a class="mono fw-semibold" href="boxes.html?box=${encodeURIComponent(box.box_id)}">${esc(box.box_id)}</a>
          ${SGUI.stageBadge(stage)}
        </div>
        <div class="small text-muted mt-1">${esc(SG.productMap[box.sku].name)} · last scanned at ${esc(SG.locationMap[box.location] || box.location)}
          ${SG.isTerminal(stage) ? '' : ` · ${esc(SG.formatDuration(SG.dwellMs(box, now)))} in stage ${SGUI.severityBadge(SG.severity(box, now))}`}</div>
      </div>`;

    const role = SG.getRole();
    const options = SG.nextStages(box, role);
    if (!options.length) {
      let why = 'No further moves are allowed.';
      if (!SG.canWrite(role)) why = 'Your role is read-only.';
      else if (stage === 'on_hold') why = 'This box is on hold. Only a supervisor can release or reject it.';
      else if (!SG.isTerminal(stage)) {
        why = SG.validateTransition(box, SG.data.transitions[stage].find((s) => s !== 'on_hold'), role) || why;
      }
      $('stage-options').innerHTML = `<p class="text-muted mb-0">${esc(why)}</p>`;
    } else {
      $('stage-options').innerHTML = options.map((s, i) => `
        <input type="radio" class="btn-check" name="to-stage" id="to-${s}" value="${s}" ${i === 0 && s !== 'on_hold' && s !== 'rejected' ? 'checked' : ''}>
        <label class="btn btn-outline-${s === 'on_hold' || s === 'rejected' ? 'danger' : 'primary'}" for="to-${s}">${SGUI.stageBadge(s)}</label>`).join('');
    }
    moveForm.querySelector('button[type="submit"]').disabled = !options.length;
    fillLocations(selectedStage() || stage);
    onStageChange();
  }

  function onStageChange() {
    const stage = selectedStage();
    const noteRequired = SG.data.note_required.includes(stage);
    $('scan-note').required = noteRequired;
    $('scan-note-label').classList.toggle('required', noteRequired);
    if (stage) fillLocations(stage);
  }

  $('lookup-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const raw = idInput.value.trim().toUpperCase();
    const parsed = SG.parseBoxId(raw);
    if (!parsed.ok) {
      let message = parsed.error;
      // A wrong check character means a damaged or misread label, so log it
      // (same as the Python model). Format mistakes are just typing hints.
      if (parsed.damaged) {
        message += ' Type the ID printed under the QR code, and ask for a label reprint.';
        const logged = logIncident('damaged_label', raw, `Unreadable scan: '${raw}'`);
        if (logged) pendingIncident = logged.id;
        if (pendingIncident) message += ` Logged as ${pendingIncident}.`;
      }
      setIdError(message);
      moveForm.classList.add('d-none');
      idInput.focus();
      return;
    }
    const found = SG.findBox(parsed.id);
    if (!found) {
      let message = `${parsed.id} is a valid ID but is not registered. Put the box in quarantine and tell a supervisor.`;
      const logged = logIncident('unknown_label', parsed.id, `Unregistered label ${parsed.id}`);
      if (logged) message += ` Logged as ${logged.id}.`;
      setIdError(message);
      moveForm.classList.add('d-none');
      idInput.focus();
      return;
    }
    if (pendingIncident) {
      const linked = SG.linkIncident(pendingIncident, found.box_id);
      if (linked.ok) {
        SGUI.toast(`${pendingIncident} linked to ${found.box_id}. Close it on the box board once the label is reprinted.`, 'info');
      }
      pendingIncident = null;
    }
    setIdError('');
    idInput.value = parsed.id;
    box = found;
    moveForm.classList.remove('d-none');
    renderBox();
    SGUI.announce(`${box.box_id} found: ${SG.stageLabel(SG.stageOf(box))}.`);
    $('move-heading').scrollIntoView({ block: 'nearest' });
  });

  moveForm.addEventListener('change', (e) => { if (e.target.name === 'to-stage') onStageChange(); });

  moveForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const stage = selectedStage();
    $('stage-error').textContent = stage ? '' : 'Choose the stage this box is moving to.';
    const ok = SGUI.validateForm(moveForm, {
      'scan-loc': (f) => (f.value ? '' : 'Choose where the box is now.'),
      'scan-note': (f) => (f.required && f.value.trim().length < 5 ? 'Give a reason (at least 5 characters).' : ''),
    });
    if (!ok || !stage) return;

    const previous = SG.stageOf(box);
    const result = SG.moveBox(box.box_id, stage, {
      location: $('scan-loc').value, note: $('scan-note').value, method: $('scan-method').value,
    });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }

    recent.unshift({ id: box.box_id, from: previous, to: stage, at: new Date().toISOString() });
    $('recent').innerHTML = recent.slice(0, 10).map((r) => `
      <li class="list-group-item">
        <span class="mono small">${esc(r.id)}</span><br>
        ${SGUI.stageBadge(r.from)} <i class="bi bi-arrow-right" aria-hidden="true"></i><span class="visually-hidden">to</span> ${SGUI.stageBadge(r.to)}
        <span class="small text-muted ms-1">${esc(SG.formatDateTime(r.at))}</span>
      </li>`).join('');
    SGUI.toast(`${box.box_id} is now '${SG.stageLabel(stage)}'.`);
    resetForNext();
  });

  function resetForNext() {
    box = null;
    moveForm.reset();
    moveForm.classList.add('d-none');
    idInput.value = '';
    setIdError('');
    idInput.focus();
  }
  $('scan-next').addEventListener('click', resetForNext);

  document.addEventListener('sg:rolechange', () => { updateReadonly(); if (box) renderBox(); });
  updateReadonly();

  const preset = new URLSearchParams(window.location.search).get('box');
  if (preset) {
    idInput.value = preset;
    $('lookup-form').requestSubmit();
  } else {
    idInput.focus();
  }
})();
