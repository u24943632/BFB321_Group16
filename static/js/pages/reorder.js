(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const num = (v, digits = 0) => Number(v).toLocaleString('en-ZA', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const rand = (v) => `R ${num(v)}`;
  const STATUS_RANK = { OK: 0, REORDER: 1, CRITICAL: 2 };

  SGUI.navbar('/reorder');

  // Totals over all products, recalculated whenever the plans change.
  function renderStockCards() {
    const plans = SG.data.reorder;
    const reorder = plans.filter((r) => r.status !== 'OK').length;
    const critical = plans.filter((r) => r.status === 'CRITICAL').length;
    const cards = [
      { title: 'Boxes on hand', value: num(plans.reduce((t, r) => t + r.on_hand, 0)), iconName: 'boxes', sub: `${plans.length} products` },
      { title: 'Stock value', value: rand(plans.reduce((t, r) => t + r.stock_value, 0)), iconName: 'cash-stack', sub: 'On hand x unit cost' },
      { title: 'Items to reorder', value: reorder, iconName: 'cart-plus', tone: reorder ? 'warning' : 'success', sub: 'At or below reorder point' },
      { title: 'Below safety stock', value: critical, iconName: 'exclamation-octagon', tone: critical ? 'danger' : 'success', sub: 'Order now' },
    ];
    $('stock-cards').innerHTML = cards.map((c) => `<div class="col-6 col-lg-3">${SGUI.kpiCard(c)}</div>`).join('');
  }

  const planTable = SGUI.sortableTable($('plan-table'), {
    caption: 'Products at or below their reorder point are flagged. Select a heading to sort.',
    sortKey: 'status', sortDir: 'desc',
    rows: SG.data.reorder,
    columns: [
      { key: 'name', label: 'Product', rowHeader: true },
      { key: 'on_hand', label: 'On hand', className: 'text-end', render: (r) => num(r.on_hand) },
      { key: 'avg_daily_demand', label: 'Daily demand', className: 'text-end d-none d-md-table-cell', render: (r) => num(r.avg_daily_demand, 1) },
      { key: 'lead_time_days', label: 'Lead time (d)', className: 'text-end d-none d-md-table-cell' },
      { key: 'safety_stock', label: 'Safety stock', className: 'text-end d-none d-lg-table-cell', render: (r) => num(r.safety_stock) },
      { key: 'reorder_point', label: 'Reorder point', className: 'text-end', render: (r) => num(r.reorder_point) },
      { key: 'eoq', label: 'EOQ', className: 'text-end', render: (r) => num(r.eoq) },
      { key: 'orders_per_year', label: 'Orders / yr', className: 'text-end d-none d-lg-table-cell', render: (r) => num(r.orders_per_year, 1) },
      { key: 'total_annual_cost', label: 'Annual cost', className: 'text-end d-none d-xl-table-cell', render: (r) => rand(r.total_annual_cost) },
      { key: 'status', label: 'Status', render: (r) => SGUI.stockBadge(r.status), sortValue: (r) => STATUS_RANK[r.status] },
      { key: 'actions', label: 'Actions', sortable: false,
        render: (r) => (SG.canManageCatalogue()
          ? `<button type="button" class="btn btn-sm btn-outline-primary" data-sku="${SGUI.escapeHtml(r.sku)}">Update<span class="visually-hidden"> ${SGUI.escapeHtml(r.name)}</span></button>`
          : '') },
    ],
  });

  function redraw() {
    planTable.update(SG.data.reorder);
    renderStockCards();
  }

  // ---- update stock on hand / model inputs (PATCH reorder-policy) -----------
  const policyModal = new bootstrap.Modal($('policy-modal'));
  let editingSku = null;
  const FIELDS = { 'u-onhand': 'on_hand', 'u-demand': 'annual_demand', 'u-order': 'order_cost',
    'u-hold': 'holding_cost', 'u-sd': 'daily_demand_std', 'u-lead': 'lead_time_days' };

  $('plan-table').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-sku]');
    if (!btn) return;
    const plan = SG.reorderMap[btn.dataset.sku];
    editingSku = plan.sku;
    SGUI.resetForm($('policy-form'));
    $('policy-modal-title').textContent = `Stock settings · ${plan.name}`;
    Object.entries(FIELDS).forEach(([id, key]) => { $(id).value = plan[key]; });
    policyModal.show();
  });

  $('policy-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const positive = (label) => (f) => (Number(f.value) > 0 ? '' : `${label} must be greater than zero.`);
    const whole = (label, min) => (f) => (f.value !== '' && Number.isInteger(Number(f.value)) && Number(f.value) >= min ? '' : `${label} must be a whole number of at least ${min}.`);
    const ok = SGUI.validateForm(e.target, {
      'u-onhand': whole('Boxes on hand', 0), 'u-demand': whole('Annual demand', 1),
      'u-order': positive('Order cost'), 'u-hold': positive('Holding cost'),
      'u-sd': (f) => (f.value !== '' && Number(f.value) >= 0 ? '' : 'Standard deviation cannot be negative.'),
      'u-lead': whole('Lead time', 1),
    });
    if (!ok) return;
    const before = SG.reorderMap[editingSku];
    const result = await SG.updateReorderPolicy(editingSku, {
      on_hand_qty: Number($('u-onhand').value), annual_demand: Number($('u-demand').value),
      order_cost: Number($('u-order').value), holding_cost: Number($('u-hold').value),
      daily_demand_std: Number($('u-sd').value), lead_time_days: Number($('u-lead').value),
    });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    policyModal.hide();
    redraw();
    const p = result.plan;
    const changed = before.status !== p.status ? ` Status ${before.status} -> ${p.status}.` : '';
    SGUI.toast(`${p.name}: EOQ ${num(p.eoq)} boxes, reorder point ${num(p.reorder_point)}.${changed}`,
      p.status === 'OK' ? 'success' : 'warning');
  });

  document.addEventListener('sg:rolechange', redraw);
  document.addEventListener('sg:refresh', redraw);
  window.setInterval(SG.refresh, 30000); // stock moves as boxes are dispatched elsewhere
  renderStockCards();

  $('calc-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const positive = (label) => (f) => (Number(f.value) > 0 ? '' : `${label} must be greater than zero.`);
    const ok = SGUI.validateForm(e.target, {
      'c-demand': positive('Annual demand'),
      'c-order': positive('Order cost'),
      'c-hold': positive('Holding cost'),
      'c-sd': (f) => (f.value !== '' && Number(f.value) >= 0 ? '' : 'Standard deviation cannot be negative.'),
      'c-lead': positive('Lead time'),
    });
    if (!ok) return;
    const D = Number($('c-demand').value);
    const S = Number($('c-order').value);
    const H = Number($('c-hold').value);
    const sd = Number($('c-sd').value);
    const L = Number($('c-lead').value);
    const z = Number($('c-service').value);

    const q = Math.sqrt((2 * D * S) / H);
    const ss = z * sd * Math.sqrt(L);
    const rop = (D / 250) * L + ss;
    const cost = (D / q) * S + (q / 2) * H;
    const results = [
      { title: 'EOQ', value: `${num(q)} boxes`, iconName: 'box-seam' },
      { title: 'Orders per year', value: num(D / q, 1), iconName: 'calendar3' },
      { title: 'Safety stock', value: `${num(ss)} boxes`, iconName: 'shield-check' },
      { title: 'Reorder point', value: `${num(rop)} boxes`, iconName: 'arrow-repeat' },
      { title: 'Ordering + holding cost', value: rand(cost), iconName: 'cash-coin', sub: 'Per year, excluding purchase cost' },
    ];
    $('calc-result').innerHTML = results.map((c) => `<div class="col-6 col-md-4 col-xl">${SGUI.kpiCard(c)}</div>`).join('');
  });
})();
