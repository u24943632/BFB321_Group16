/*
 * Reusable UI components: navbar, cards, badges, sortable table,
 * toasts and screen-reader announcements. Every page uses these, so markup
 * and accessibility behaviour stay consistent.
 */
(function () {
  'use strict';

  const PAGES = [
    { href: 'index.html', label: 'Dashboard', icon: 'speedometer2' },
    { href: 'boxes.html', label: 'Box board', icon: 'table' },
    { href: 'scan.html', label: 'Scan & update', icon: 'qr-code-scan' },
    { href: 'register.html', label: 'Register box', icon: 'plus-square' },
    { href: 'reorder.html', label: 'Reorder', icon: 'arrow-repeat' },
    { href: 'metrics.html', label: 'KPIs', icon: 'graph-up-arrow' },
  ];

  const STAGE_ICONS = {
    in_production: 'gear', awaiting_qc: 'hourglass-split', qc_complete: 'clipboard-check',
    packaging_complete: 'box-seam', ready_for_dispatch: 'truck', dispatched: 'check2-all',
    on_hold: 'pause-circle', rejected: 'x-octagon',
  };
  const SEVERITY = {
    ok: { label: 'On time', icon: 'check-circle' },
    warning: { label: 'Warning', icon: 'exclamation-triangle' },
    critical: { label: 'Critical', icon: 'exclamation-octagon' },
  };
  const STOCK = {
    OK: { label: 'OK', icon: 'check-circle' },
    REORDER: { label: 'Reorder', icon: 'cart-plus' },
    CRITICAL: { label: 'Critical', icon: 'exclamation-octagon' },
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const icon = (name) => `<i class="bi bi-${name}" aria-hidden="true"></i>`;

  function stageBadge(stage) {
    return `<span class="sg-badge stage-${escapeHtml(stage)}">${icon(STAGE_ICONS[stage] || 'circle')}${escapeHtml(SG.stageLabel(stage))}</span>`;
  }
  function severityBadge(sev) {
    const s = SEVERITY[sev] || SEVERITY.ok;
    return `<span class="sg-badge sev-${sev}">${icon(s.icon)}${s.label}</span>`;
  }
  function stockBadge(status) {
    const s = STOCK[status] || STOCK.OK;
    return `<span class="sg-badge stock-${status}">${icon(s.icon)}${s.label}</span>`;
  }

  /** KPI / summary card. tone: '', 'danger', 'warning', 'success'. */
  function kpiCard({ title, value, sub = '', iconName = 'info-circle', tone = '' }) {
    return `
      <div class="card sg-kpi ${tone ? `tone-${tone}` : ''}">
        <div class="card-body">
          <div class="sg-kpi-icon">${icon(iconName)}</div>
          <div>
            <p class="sg-kpi-title">${escapeHtml(title)}</p>
            <p class="sg-kpi-value">${escapeHtml(value)}</p>
            ${sub ? `<p class="sg-kpi-sub">${escapeHtml(sub)}</p>` : ''}
          </div>
        </div>
      </div>`;
  }

  function navbar(activeHref) {
    const host = document.getElementById('sg-nav');
    if (!host) return;
    const role = SG.getRole();
    const links = PAGES.map((p) => {
      const active = p.href === activeHref;
      return `<li class="nav-item"><a class="nav-link ${active ? 'active' : ''}" href="${p.href}"
        ${active ? 'aria-current="page"' : ''}>${icon(p.icon)} ${p.label}</a></li>`;
    }).join('');
    const roles = SG.data.roles.map((r) =>
      `<option value="${r.key}" ${r.key === role ? 'selected' : ''}>${escapeHtml(r.label)} (${escapeHtml(r.user)})</option>`).join('');
    host.innerHTML = `
      <nav class="navbar navbar-expand-xl navbar-dark sg-navbar" aria-label="Main">
        <div class="container-xxl">
          <a class="navbar-brand" href="index.html">${icon('box2-heart')} Simply Garlic <span class="fw-normal">Tracker</span></a>
          <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#sg-nav-links"
            aria-controls="sg-nav-links" aria-expanded="false" aria-label="Toggle navigation">
            <span class="navbar-toggler-icon"></span>
          </button>
          <div class="collapse navbar-collapse" id="sg-nav-links">
            <ul class="navbar-nav me-auto gap-xl-1">${links}</ul>
            <div class="d-flex align-items-center gap-2 py-2 py-xl-0">
              <label for="sg-role" class="text-white small mb-0">Signed in as</label>
              <select id="sg-role" class="form-select form-select-sm" style="width:auto">${roles}</select>
            </div>
          </div>
        </div>
      </nav>`;
    host.querySelector('#sg-role').addEventListener('change', (e) => {
      SG.setRole(e.target.value);
      announce(`Role changed to ${SG.roleLabel(e.target.value)}.`);
    });
  }

  /** Polite screen-reader announcement. */
  function announce(message) {
    let region = document.getElementById('sg-live');
    if (!region) {
      region = document.createElement('div');
      region.id = 'sg-live';
      region.className = 'visually-hidden';
      region.setAttribute('aria-live', 'polite');
      region.setAttribute('role', 'status');
      document.body.appendChild(region);
    }
    region.textContent = '';
    window.setTimeout(() => { region.textContent = message; }, 50);
  }

  /** Visual toast (also announced). tone: success | danger | warning | info */
  function toast(message, tone = 'success') {
    let wrap = document.getElementById('sg-toasts');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'sg-toasts';
      wrap.className = 'toast-container position-fixed bottom-0 end-0 p-3';
      document.body.appendChild(wrap);
    }
    const el = document.createElement('div');
    el.className = `toast align-items-center text-bg-${tone} border-0`;
    el.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    el.innerHTML = `<div class="d-flex"><div class="toast-body">${escapeHtml(message)}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
    wrap.appendChild(el);
    el.addEventListener('hidden.bs.toast', () => el.remove());
    new bootstrap.Toast(el, { delay: 5000 }).show();
  }

  /**
   * Sortable, accessible table.
   * columns: [{ key, label, sortable=true, render(row)->html, sortValue(row), className, rowHeader }]
   * Header buttons toggle sort; the sorted <th> gets aria-sort.
   */
  function sortableTable(container, opts) {
    const st = { rows: opts.rows || [], key: opts.sortKey || null, dir: opts.sortDir || 'asc' };
    const id = opts.id || `tbl-${Math.random().toString(36).slice(2, 8)}`;

    container.innerHTML = `
      <div class="table-responsive">
        <table class="table table-hover align-middle" id="${id}">
          <caption>${escapeHtml(opts.caption || '')}</caption>
          <thead><tr>${opts.columns.map((c) => `
            <th scope="col" class="${c.className || ''}" data-key="${c.key}">
              ${c.sortable === false ? escapeHtml(c.label) :
                `<button type="button" class="sg-sort-btn">${escapeHtml(c.label)} <i class="bi bi-arrow-down-up" aria-hidden="true"></i></button>`}
            </th>`).join('')}</tr></thead>
          <tbody></tbody>
        </table>
      </div>`;
    const table = container.querySelector('table');
    const tbody = table.querySelector('tbody');

    const valueOf = (col, row) => (col.sortValue ? col.sortValue(row) : row[col.key]);

    function renderBody() {
      const col = opts.columns.find((c) => c.key === st.key);
      let rows = st.rows.slice();
      if (col) {
        rows.sort((a, b) => {
          const va = valueOf(col, a);
          const vb = valueOf(col, b);
          const cmp = typeof va === 'number' && typeof vb === 'number'
            ? va - vb : String(va).localeCompare(String(vb), 'en', { numeric: true });
          return st.dir === 'asc' ? cmp : -cmp;
        });
      }
      table.querySelectorAll('th[data-key]').forEach((th) => {
        const sorted = th.dataset.key === st.key;
        if (sorted) th.setAttribute('aria-sort', st.dir === 'asc' ? 'ascending' : 'descending');
        else th.removeAttribute('aria-sort');
        const i = th.querySelector('.bi');
        if (i) i.className = `bi ${sorted ? (st.dir === 'asc' ? 'bi-sort-up' : 'bi-sort-down') : 'bi-arrow-down-up'}`;
      });
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="${opts.columns.length}" class="text-center text-muted py-4">${escapeHtml(opts.emptyText || 'Nothing to show.')}</td></tr>`;
        return;
      }
      tbody.innerHTML = rows.map((row) => `
        <tr class="${opts.rowClass ? opts.rowClass(row) : ''}">
          ${opts.columns.map((c) => {
            const content = c.render ? c.render(row) : escapeHtml(row[c.key]);
            return c.rowHeader
              ? `<th scope="row" class="${c.className || ''}">${content}</th>`
              : `<td class="${c.className || ''}">${content}</td>`;
          }).join('')}
        </tr>`).join('');
    }

    table.querySelectorAll('.sg-sort-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.closest('th').dataset.key;
        st.dir = st.key === key && st.dir === 'asc' ? 'desc' : 'asc';
        st.key = key;
        renderBody();
        const col = opts.columns.find((c) => c.key === key);
        announce(`Sorted by ${col.label}, ${st.dir === 'asc' ? 'ascending' : 'descending'}.`);
      });
    });

    renderBody();
    return { update(rows) { st.rows = rows; renderBody(); }, element: table };
  }

  /** Attach Bootstrap-style validation with custom messages to a form. */
  function validateForm(form, messages) {
    let firstInvalid = null;
    form.querySelectorAll('input, select, textarea').forEach((field) => {
      const feedback = form.querySelector(`#${field.id}-error`);
      field.setCustomValidity('');
      const custom = messages && messages[field.id] ? messages[field.id](field) : '';
      if (custom) field.setCustomValidity(custom);
      const valid = field.checkValidity();
      field.classList.toggle('is-invalid', !valid);
      field.setAttribute('aria-invalid', String(!valid));
      if (feedback) feedback.textContent = valid ? '' : (custom || field.validationMessage);
      if (!valid && !firstInvalid) firstInvalid = field;
    });
    if (firstInvalid) firstInvalid.focus();
    return !firstInvalid;
  }

  window.SGUI = {
    escapeHtml, icon, stageBadge, severityBadge, stockBadge, kpiCard, navbar,
    announce, toast, sortableTable, validateForm,
  };
})();
