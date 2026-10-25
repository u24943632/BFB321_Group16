(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;
  const $ = (id) => document.getElementById(id);

  SGUI.navbar('register.html');

  SG.data.products.forEach((p) => $('r-sku').insertAdjacentHTML('beforeend',
    `<option value="${p.sku}">${esc(p.name)} (${p.sku})</option>`));
  // One standard production location: every new box starts there.
  const PRODUCTION = SG.data.stage_locations.in_production[0];

  function updateRole() {
    const allowed = SG.data.register_roles.includes(SG.getRole());
    $('role-notice').classList.toggle('d-none', allowed);
    $('register-form').querySelector('button[type="submit"]').disabled = !allowed;
  }
  document.addEventListener('sg:rolechange', updateRole);
  updateRole();

  function qrSvg(text) {
    if (typeof window.qrcode !== 'function') return '';
    const qr = window.qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true, alt: `QR code for ${text}`, title: text });
  }

  $('register-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const ok = SGUI.validateForm(e.target, {
      'r-sku': (f) => (f.value ? '' : 'Choose the product in this box.'),
      'r-batch': (f) => (/^[A-Za-z0-9][A-Za-z0-9-]{2,19}$/.test(f.value.trim()) ? '' : 'Batch number must be 3–20 letters, digits or dashes, starting with a letter or digit.'),
      'r-qty': (f) => {
        const n = Number(f.value);
        return Number.isInteger(n) && n >= 1 && n <= 500 ? '' : 'Enter a whole number of units from 1 to 500.';
      },
      'r-confirm': (f) => (f.checked ? '' : 'Confirm the label will be applied to this box.'),
    });
    if (!ok) return;

    const result = SG.registerBox({
      sku: $('r-sku').value, batch: $('r-batch').value.trim(), quantity: Number($('r-qty').value), location: PRODUCTION,
    });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }

    const box = result.box;
    const product = SG.productMap[box.sku];
    $('label-output').innerHTML = `
      <div class="sg-label sg-print-area">
        <div class="d-flex gap-3 align-items-center">
          <div>${qrSvg(box.box_id) || '<p class="small text-muted">QR library unavailable offline.</p>'}</div>
          <div>
            <p class="fw-bold mb-1">${esc(product.name)}</p>
            <p class="small mb-1">Batch ${esc(box.batch_no)} · ${box.quantity} units</p>
            <p class="small mb-0">${esc(SG.formatDateTime(box.created_at))}</p>
          </div>
        </div>
        <p class="mono fw-bold fs-5 mt-2 mb-0 text-center">${esc(box.box_id)}</p>
      </div>
      <div class="d-flex flex-wrap gap-2 mt-3">
        <button type="button" class="btn btn-outline-primary" onclick="window.print()"><i class="bi bi-printer" aria-hidden="true"></i> Print label</button>
        <a class="btn btn-outline-secondary" href="boxes.html?box=${encodeURIComponent(box.box_id)}">View box</a>
      </div>`;
    SGUI.toast(`Registered ${box.box_id}.`);
    e.target.reset();
    $('r-sku').focus();
  });
})();
