(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const num = (v, digits = 0) => Number(v).toLocaleString('en-ZA', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const rand = (v) => `R ${num(v)}`;
  const STATUS_RANK = { OK: 0, REORDER: 1, CRITICAL: 2 };

  SGUI.navbar('reorder.html');

  SGUI.sortableTable($('plan-table'), {
    caption: 'Products at or below their reorder point are flagged. Select a heading to sort.',
    sortKey: 'status', sortDir: 'desc',
    rows: SG.data.reorder.map((r) => ({ ...r, name: SG.productMap[r.sku].name })),
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
    ],
  });

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
