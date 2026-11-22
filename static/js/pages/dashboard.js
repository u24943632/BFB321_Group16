(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;

  SGUI.navbar('/');

  const rand = (v) => `R ${Math.round(v).toLocaleString('en-ZA')}`;
  let summary = null; // live KPIs from GET /api/metrics/summary (calculated on the server)

  function renderCards() {
    if (!summary) return;
    const s = summary;
    const tone = (bad, level) => (bad ? level : 'success');
    const cards = [
      { title: 'Boxes in process', value: s.boxes_in_process, iconName: 'boxes', sub: `${s.boxes_total} boxes tracked in total` },
      { title: 'Critical alerts', value: s.alerts_critical, iconName: 'exclamation-octagon', tone: tone(s.alerts_critical, 'danger'), sub: 'Over critical stage-time limit' },
      { title: 'Warnings', value: s.alerts_warning, iconName: 'exclamation-triangle', tone: tone(s.alerts_warning, 'warning'), sub: 'Over warning limit' },
      { title: 'Open investigations', value: s.open_incidents, iconName: 'search', tone: tone(s.open_incidents, 'warning'), sub: 'Misplaced, label, quality or dispatch issues' },
      { title: 'Ready for dispatch', value: s.ready_for_dispatch, iconName: 'truck', sub: 'QC and packaging complete' },
      { title: 'Dispatched (24 h)', value: s.dispatched_24h, iconName: 'check2-all', tone: 'success', sub: 'Box throughput' },
      { title: 'Avg time in stage', value: `${s.mean_hours_in_current_stage.toFixed(1)} h`, iconName: 'stopwatch', sub: 'Boxes still in process' },
      { title: 'Traceability', value: `${s.traceability_coverage_pct.toFixed(0)}%`, iconName: 'diagram-3', tone: tone(s.traceability_coverage_pct < 100, 'warning'), sub: 'Dispatched boxes with a complete stage history' },
      { title: 'Stock value', value: rand(s.stock_value), iconName: 'cash-stack', sub: `${s.stock_on_hand_boxes} boxes on hand` },
      { title: 'Items to reorder', value: s.items_to_reorder, iconName: 'cart-plus', tone: tone(s.items_to_reorder, 'warning'), sub: `${s.items_critical} below safety stock` },
      { title: 'Active products', value: s.active_products, iconName: 'tags', sub: 'In the catalogue' },
    ];
    document.getElementById('kpi-cards').innerHTML = cards
      .map((c) => `<div class="col-6 col-md-4 col-xl-3">${SGUI.kpiCard(c)}</div>`).join('');
    document.getElementById('as-of').textContent = `Updated ${SG.formatDateTime(s.generated_at)}.`;
  }

  // Pull fresh data from MySQL: the box/incident lists and the KPI summary.
  async function refresh() {
    const [ok, result] = await Promise.all([SG.refresh(), SG.getJson('/api/metrics/summary')]);
    if (result.ok) summary = result.summary;
    else SGUI.toast(result.error, 'danger');
    if (!ok && result.ok) SGUI.toast('Could not refresh the box list.', 'warning');
    render();
  }

  function render() {
    const now = Date.now();
    const active = SG.boxes.filter((b) => !SG.isTerminal(SG.stageOf(b)));
    const alerts = SG.alerts(now);
    const openInc = SG.incidents.filter((i) => !i.closed_at);
    renderCards();

    const pipelineStages = SG.data.main_flow.filter((s) => s !== 'dispatched').concat(['on_hold']);
    document.getElementById('pipeline').innerHTML = pipelineStages.map((stage) => {
      const inStage = active.filter((b) => SG.stageOf(b) === stage);
      const avg = inStage.length ? inStage.reduce((t, b) => t + SG.dwellMs(b, now), 0) / inStage.length : 0;
      const late = alerts.filter((a) => SG.stageOf(a.box) === stage).length;
      return `
        <div class="card sg-stage-card" style="--stage-color: var(--sg-stage-${stage})">
          <div class="card-body">
            <h3 class="h6 mb-1">${esc(SG.stageLabel(stage))}</h3>
            <p class="count mb-0">${inStage.length}<span class="visually-hidden"> boxes</span></p>
            <p class="small text-muted mb-2">Avg ${inStage.length ? esc(SG.formatDuration(avg)) : '-'} in stage
              ${late ? `<br><strong class="text-danger">${late} over limit</strong>` : ''}</p>
            <a class="small" href="/boxes?stage=${stage}">View<span class="visually-hidden"> ${esc(SG.stageLabel(stage))} boxes</span></a>
          </div>
        </div>`;
    }).join('');

    document.getElementById('investigations').innerHTML = openInc.length
      ? openInc.map((i) => `
        <li class="list-group-item">
          <div class="d-flex justify-content-between gap-2">
            <strong>${esc(SG.incidentKindMap[i.kind])}</strong>
            <span class="small text-muted">${esc(SG.formatDuration(now - Date.parse(i.opened_at)))} open</span>
          </div>
          ${i.box_id ? `<a class="mono small" href="/boxes?box=${encodeURIComponent(i.box_id)}">${esc(i.box_id)}</a>` : ''}
          <div class="small text-muted">${esc(i.note)}</div>
        </li>`).join('')
      : '<li class="list-group-item text-muted">No open investigations.</li>';

    const reorder = SG.data.reorder.filter((r) => r.status !== 'OK');
    document.getElementById('reorder-list').innerHTML = reorder.length
      ? reorder.map((r) => `
        <li class="list-group-item d-flex justify-content-between align-items-center gap-2">
          <span>${esc(SG.productMap[r.sku].name)}<br><span class="small text-muted">${Math.round(r.on_hand)} on hand · reorder at ${Math.round(r.reorder_point)}</span></span>
          ${SGUI.stockBadge(r.status)}
        </li>`).join('')
      : '<li class="list-group-item text-muted">All products above reorder point.</li>';
  }

  render(); // straight away from the data loaded with the page
  refresh(); // then the server-calculated KPIs
  window.setInterval(refresh, 30000); // live view: picks up scans made on other devices
  document.addEventListener('sg:rolechange', render);
})();
