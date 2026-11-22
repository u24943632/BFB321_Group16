/*
 * Product catalogue page. The list is paged and filtered by the API
 * (GET /api/products?q=&category=&active=&page=&per_page=); adding and
 * editing go through POST /api/products and PATCH /api/products/<sku>, and
 * the table is redrawn from the API response.
 */
(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;
  const $ = (id) => document.getElementById(id);
  const PER_PAGE = 10;
  const money = (v) => `R ${Number(v).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  SGUI.navbar('/products');

  let page = 1;
  let editingSku = null; // null while adding a new product
  const modal = new bootstrap.Modal($('product-modal'));

  // ---- category drop-downs -------------------------------------------------
  function fillCategories(selected) {
    const options = SG.data.categories.map((c) =>
      `<option value="${esc(c.name)}">${esc(c.name)}${c.perishable ? ' (perishable)' : ''}</option>`).join('');
    const filterValue = $('pf-category').value;
    $('pf-category').innerHTML = `<option value="">All categories</option>${options}`;
    $('pf-category').value = filterValue;
    $('p-category').innerHTML = `<option value="">Choose a category…</option>${options}`;
    if (selected) $('p-category').value = selected;
  }

  // ---- list ----------------------------------------------------------------
  const table = SGUI.sortableTable($('product-table'), {
    caption: 'Stock status comes from the EOQ / reorder point model on the Reorder page.',
    sortKey: 'sku',
    emptyText: 'No products match these filters.',
    rowClass: (r) => (r.active ? '' : 'text-muted'),
    columns: [
      { key: 'sku', label: 'SKU', rowHeader: true, render: (r) => `<span class="mono fw-semibold">${esc(r.sku)}</span>` },
      { key: 'name', label: 'Product' },
      { key: 'category', label: 'Category', className: 'd-none d-md-table-cell',
        render: (r) => `${esc(r.category)}${r.perishable ? ' <span class="badge text-bg-warning">Perishable</span>' : ''}` },
      { key: 'unit_cost', label: 'Unit cost', className: 'text-end', render: (r) => money(r.unit_cost) },
      { key: 'stock', label: 'Stock', sortable: false,
        render: (r) => (SG.reorderMap[r.sku] ? SGUI.stockBadge(SG.reorderMap[r.sku].status) : '<span class="small text-muted">No policy</span>') },
      { key: 'active', label: 'Status', render: (r) => (r.active ? 'Active' : 'Inactive'), sortValue: (r) => (r.active ? 0 : 1) },
      { key: 'actions', label: 'Actions', sortable: false,
        render: (r) => (SG.canManageCatalogue()
          ? `<button type="button" class="btn btn-sm btn-outline-primary" data-sku="${esc(r.sku)}">Edit<span class="visually-hidden"> ${esc(r.name)}</span></button>`
          : '') },
    ],
  });

  function filterQuery() {
    const params = new URLSearchParams({ page, per_page: PER_PAGE });
    if ($('pf-search').value.trim()) params.set('q', $('pf-search').value.trim());
    if ($('pf-category').value) params.set('category', $('pf-category').value);
    if ($('pf-active').value) params.set('active', $('pf-active').value);
    return params.toString();
  }

  let rows = [];
  async function loadPage(n = page) {
    page = n;
    const result = await SG.getJson(`/api/products?${filterQuery()}`);
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    rows = result.items;
    table.update(rows);
    SGUI.pager($('product-pager'), result.pagination, loadPage);
  }

  $('product-filters').addEventListener('submit', (e) => { e.preventDefault(); loadPage(1); });
  $('pf-category').addEventListener('change', () => loadPage(1));
  $('pf-active').addEventListener('change', () => loadPage(1));

  // ---- add / edit modal ----------------------------------------------------
  function openModal(product) {
    editingSku = product ? product.sku : null;
    SGUI.resetForm($('product-form'));
    fillCategories(product ? product.category : '');
    $('product-modal-title').textContent = product ? `Edit ${product.sku} · ${product.name}` : 'Add product';
    $('p-sku').value = product ? product.sku : '';
    $('p-sku').disabled = !!product;
    $('p-name').value = product ? product.name : '';
    $('p-cost').value = product ? product.unit_cost : '';
    $('p-active').checked = product ? product.active : true;
    $('p-active-wrap').classList.toggle('d-none', !product);
    // Stock settings of an existing product are edited on the Reorder page.
    $('p-policy').disabled = !!product;
    $('p-policy').classList.toggle('d-none', !!product);
    modal.show();
  }

  $('add-product').addEventListener('click', () => openModal(null));
  $('product-table').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-sku]');
    if (btn) openModal(rows.find((r) => r.sku === btn.dataset.sku));
  });

  function validate() {
    const positive = (label) => (f) => (Number(f.value) > 0 ? '' : `${label} must be greater than zero.`);
    const whole = (label, min) => (f) => (Number.isInteger(Number(f.value)) && Number(f.value) >= min && f.value !== '' ? '' : `${label} must be a whole number of at least ${min}.`);
    return SGUI.validateForm($('product-form'), {
      'p-sku': (f) => (f.disabled || /^[A-Za-z]{3}$/.test(f.value.trim()) ? '' : 'The SKU must be exactly 3 letters.'),
      'p-name': (f) => (f.value.trim().length >= 3 ? '' : 'Give the product a name (at least 3 characters).'),
      'p-cost': (f) => (f.value !== '' && Number(f.value) >= 0 ? '' : 'Unit cost cannot be negative.'),
      'p-category': (f) => (f.value ? '' : 'Choose a category.'),
      'c-name': () => '',
      'p-demand': whole('Annual demand', 1),
      'p-order': positive('Order cost'),
      'p-hold': positive('Holding cost'),
      'p-sd': (f) => (f.value !== '' && Number(f.value) >= 0 ? '' : 'Standard deviation cannot be negative.'),
      'p-lead': whole('Lead time', 1),
      'p-onhand': whole('Boxes on hand', 0),
    });
  }

  $('product-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!validate()) return;
    const fields = { name: $('p-name').value.trim(), category: $('p-category').value, unit_cost: Number($('p-cost').value) };
    let result;
    if (editingSku) {
      result = await SG.updateProduct(editingSku, { ...fields, active: $('p-active').checked });
    } else {
      result = await SG.createProduct({
        sku: $('p-sku').value.trim().toUpperCase(), ...fields,
        reorder_policy: {
          annual_demand: Number($('p-demand').value), order_cost: Number($('p-order').value),
          holding_cost: Number($('p-hold').value), daily_demand_std: Number($('p-sd').value),
          lead_time_days: Number($('p-lead').value), on_hand_qty: Number($('p-onhand').value),
        },
      });
    }
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    modal.hide();
    const p = result.product;
    const plan = p.reorder ? ` EOQ ${Math.round(p.reorder.eoq)} boxes, reorder at ${Math.round(p.reorder.reorder_point)}.` : '';
    SGUI.toast(`${p.sku} ${p.name} ${editingSku ? 'updated' : 'added'}.${editingSku ? '' : plan}`);
    await SG.refresh(); // new products appear on the register page, box board and reorder page
    loadPage(editingSku ? page : 1);
  });

  // ---- new category --------------------------------------------------------
  $('c-save').addEventListener('click', async () => {
    const nameField = $('c-name');
    const name = nameField.value.trim();
    const invalid = name.length < 3;
    nameField.classList.toggle('is-invalid', invalid);
    $('c-name-error').textContent = invalid ? 'Give the category a name (at least 3 characters).' : '';
    if (invalid) { nameField.focus(); return; }
    const result = await SG.createCategory({ name, perishable: $('c-perishable').checked });
    if (!result.ok) { SGUI.toast(result.error, 'danger'); return; }
    SG.data.categories.push({ name: result.category.name, perishable: result.category.perishable });
    SG.data.categories.sort((a, b) => a.name.localeCompare(b.name));
    fillCategories(result.category.name);
    nameField.value = '';
    $('c-perishable').checked = false;
    $('new-category').open = false;
    SGUI.toast(`Category '${result.category.name}' added.`);
  });

  // ---- role ----------------------------------------------------------------
  function applyRole() {
    const allowed = SG.canManageCatalogue();
    $('add-product').classList.toggle('d-none', !allowed);
    $('catalogue-readonly').classList.toggle('d-none', allowed);
    table.update(rows);
  }
  document.addEventListener('sg:rolechange', applyRole);

  fillCategories();
  applyRole();
  loadPage(1);
})();
