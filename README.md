# Simply Garlic box tracker

A web-based inventory tracking system for Simply Garlic, a South African
vegetable-based product manufacturer. Simply Garlic's Derdepoort facility relies on a manual, paper-based labelling system with no digital record or time-based trail, causing misplaced labels and a loss of production time, which increases the risks of delayed deliveries to clients. This solution gives each box its own ID and follows through production stages while raising alerts when a box stays in a stage too long. It also reports before/after process KPIs and reorder recommendations. Demo data is used to give an idea of how this project may look in practise. 

## Run it locally

Requires Python 3.10+. There are no packages to install. Node.js is optional
and only used for the browser/Python parity test.

```bash
# 1. Run the tests
python3 -m unittest discover -v

# 2. (Re)generate the demo data the frontend uses
python3 scripts/build_demo_data.py

# 3. Serve the site, then open http://localhost:8000
python3 -m http.server 8000 -d web
```

An internet connection is needed to load the assets used for the bootstrap. All changes made in the application are only local and will be reset after each new running of the application.

## Project layout

```
models/
  products.py   product catalogue (10 SKUs)
  tracking.py   box IDs + check character, stage state machine, roles,
                scans (duplicate/damaged/unknown), incidents, hash-chained audit log
  alerts.py     stage-dwell warning/critical thresholds
  metrics.py    baseline vs after KPIs and comparison
  eoq.py        calculate_eoq, orders_per_year, total_annual_cost,
                safety_stock, reorder_point, plan_reorder
tests/          unittest suites (hand-calculated values + Python/JS rule parity)
scripts/
  build_demo_data.py   runs a simulated pilot through the model -> web/assets/js/demo-data.js
web/
  index.html     dashboard: KPI cards, stage pipeline, alerts, investigations
  boxes.html     box board: filters, sortable table, history + exception modal
  scan.html      operator scan & stage update (phone-first)
  register.html  register a box and print its QR label
  reorder.html   EOQ / reorder point per SKU + what-if calculator
  metrics.html   before/after evaluation
  assets/css/styles.css      design tokens (type + spacing scale), components
  assets/js/components.js    reusable navbar, cards, badges, sortable table, toasts
  assets/js/store.js         applies the generated rules in the browser
docs/PROCESS_RULES.md
```