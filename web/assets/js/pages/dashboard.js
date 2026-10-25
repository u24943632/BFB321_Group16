(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;

  SGUI.navbar('index.html');

  function render() {
    const now = Date.now();
    const boxes = SG.boxes;
    const active = boxes.filter((b) => !SG.isTerminal(SG.stageOf(b)));
    const alerts = SG.alerts(now);
    const critical = alerts.filter((a) => a.severity === 'critical').length;
    const dayAgo = now - 86400000;
    const dispatched24 = boxes.filter((b) => SG.stageOf(b) === 'dispatched' && Date.parse(SG.stageEnteredAt(b)) >= dayAgo).length;
    const ready = boxes.filter((b) => SG.stageOf(b) === 'ready_for_dispatch').length;
    const openInc = SG.incidents.filter((i) => !i.closed_at);

    document.getElementById('as-of').textContent = `Updated ${SG.formatDateTime(new Date(now).toISOString())}.`;

    const cards = [
      { title: 'Boxes in process', value: active.length, iconName: 'boxes', sub: 'Not yet dispatched or rejected' },
      { title: 'Critical alerts', value: critical, iconName: 'exclamation-octagon', tone: critical ? 'danger' : 'success', sub: 'Over critical dwell limit' },
      { title: 'Warnings', value: alerts.length - critical, iconName: 'exclamation-triangle', tone: alerts.length - critical ? 'warning' : 'success', sub: 'Over warning limit' },
      { title: 'Ready for dispatch', value: ready, iconName: 'truck', sub: 'QC and packaging complete' },
      { title: 'Dispatched (24 h)', value: dispatched24, iconName: 'check2-all', tone: 'success', sub: 'Box throughput' },
      { title: 'Open investigations', value: openInc.length, iconName: 'search', tone: openInc.length ? 'warning' : 'success', sub: 'Misplaced, label, quality or dispatch issues' },
    ];
    document.getElementById('kpi-cards').innerHTML = cards
      .map((c) => `<div class="col-6 col-md-4 col-xl-2">${SGUI.kpiCard(c)}</div>`).join('');

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
            <a class="small" href="boxes.html?stage=${stage}">View<span class="visually-hidden"> ${esc(SG.stageLabel(stage))} boxes</span></a>
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
          ${i.box_id ? `<a class="mono small" href="boxes.html?box=${encodeURIComponent(i.box_id)}">${esc(i.box_id)}</a>` : ''}
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

  render();
  window.setInterval(render, 60000);
  document.addEventListener('sg:rolechange', render);
})();
