(function () {
  'use strict';
  const { escapeHtml: esc } = SGUI;
  const $ = (id) => document.getElementById(id);
  const K = SG.data.kpis;

  SGUI.navbar('metrics.html');

  const dec = (value, digits = 1) => Number(value).toLocaleString('en-ZA', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const fmt = (value, unit) => `${dec(value, unit === 's' || unit === 'boxes/day' ? 0 : 1)} ${unit}`;
  const changeText = (r) => {
    if (r.improvement_pct === null) return 'n/a (baseline was 0)';
    const sign = r.improvement_pct >= 0 ? '+' : '−';
    return `${sign}${Math.abs(r.improvement_pct).toFixed(0)}% ${r.improvement_pct >= 0 ? 'better' : 'worse'}`;
  };
  const resultBadge = (r) => (r.improved
    ? '<span class="sg-badge sev-ok"><i class="bi bi-arrow-up-right-circle" aria-hidden="true"></i>Improved</span>'
    : '<span class="sg-badge sev-warning"><i class="bi bi-dash-circle" aria-hidden="true"></i>Not improved</span>');

  const TARGETS = [
    ['mean_investigation_minutes', 'search'],
    ['tracking_incident_rate_pct', 'tag'],
    ['traceability_coverage_pct', 'diagram-3'],
    ['mean_stage_dwell_hours', 'hourglass-split'],
    ['labour_utilisation_pct', 'people'],
  ];
  const byKey = Object.fromEntries(K.comparison.map((r) => [r.key, r]));

  $('target-cards').innerHTML = TARGETS.map(([key, iconName]) => {
    const r = byKey[key];
    return `<div class="col-12 col-sm-6 col-xl">
      ${SGUI.kpiCard({
        title: r.label, value: fmt(r.current, r.unit), iconName, tone: r.improved ? 'success' : 'warning',
        sub: `Baseline ${fmt(r.baseline, r.unit)} · ${changeText(r)}`,
      })}</div>`;
  }).join('');

  SGUI.sortableTable($('compare-table'), {
    caption: `${K.baseline.label} (${K.baseline.period_days} days) compared with ${K.current.label} (${K.current.period_days.toFixed(0)} days).`,
    rows: K.comparison,
    columns: [
      { key: 'label', label: 'Measure', rowHeader: true },
      { key: 'baseline', label: 'Before', className: 'text-end', render: (r) => esc(fmt(r.baseline, r.unit)) },
      { key: 'current', label: 'After', className: 'text-end', render: (r) => esc(fmt(r.current, r.unit)) },
      { key: 'bars', label: 'Comparison', sortable: false, className: 'd-none d-md-table-cell', render: (r) => {
        const max = Math.max(r.baseline, r.current) || 1;
        return `<div aria-hidden="true" style="min-width:8rem">
          <div class="sg-bar mb-1"><span class="before" style="width:${(r.baseline / max) * 100}%"></span></div>
          <div class="sg-bar"><span class="after" style="width:${(r.current / max) * 100}%"></span></div></div>`;
      } },
      { key: 'improvement_pct', label: 'Change', className: 'text-end', render: (r) => esc(changeText(r)),
        sortValue: (r) => (r.improvement_pct === null ? -Infinity : r.improvement_pct) },
      { key: 'improved', label: 'Result', render: resultBadge, sortValue: (r) => (r.improved ? 1 : 0) },
    ],
  });

  const dwellRows = SG.data.main_flow.filter((s) => K.dwell_by_stage_hours[s] !== undefined)
    .concat(['on_hold'].filter((s) => K.dwell_by_stage_hours[s] !== undefined))
    .map((s) => {
      const t = SG.data.thresholds[s];
      return { stage: s, hours: K.dwell_by_stage_hours[s], warning: t ? t.warning_minutes / 60 : null };
    });
  SGUI.sortableTable($('dwell-table'), {
    caption: 'Completed stays only. Warning limit shown for non-perishable products.',
    rows: dwellRows,
    columns: [
      { key: 'stage', label: 'Stage', rowHeader: true, render: (r) => SGUI.stageBadge(r.stage),
        sortValue: (r) => SG.data.stages.findIndex((s) => s.key === r.stage) },
      { key: 'hours', label: 'Mean dwell', className: 'text-end', render: (r) => `${dec(r.hours)} h` },
      { key: 'warning', label: 'Warning limit', className: 'text-end', render: (r) => (r.warning ? `${dec(r.warning, 0)} h` : '-') },
    ],
  });

  $('scan-compliance').textContent =
    `${dec(K.scan_compliance_pct)}% of stage changes in the pilot were captured by QR scan rather than typed in (target ≥ 95%).`;
})();
