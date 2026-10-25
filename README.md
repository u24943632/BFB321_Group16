# Simply Garlic box tracker

A web-based inventory tracking system for Simply Garlic, a South African
vegetable-based product manufacturer. It gives each box its own ID and follows
it from production through QC, packaging and dispatch, raising alerts when a
box stays in a stage too long. It also reports before/after process KPIs and
reorder recommendations.

- **Step 1: Frontend** (`web/`): responsive Bootstrap 5 pages for phones,
  tablets and laptops, with accessible forms and tables and reusable components.
- **Step 2: Supply-chain model** (`models/`): standalone Python with no
  framework and no database, covered by unit tests.

Process rules (thresholds, access, QR handling, audit, location, evaluation
method) are in [`docs/PROCESS_RULES.md`](docs/PROCESS_RULES.md).

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

Opening `web/index.html` directly from disk also works. The pages load
Bootstrap from a CDN, so they need an internet connection.

Use **Signed in as** in the navbar to switch roles (operator, QC inspector,
supervisor, executive) and see the access rules apply. Changes made in the
demo are saved in your browser. Use **Reset demo data** in the footer to
start again.

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

## Notes

- **All data is sample data.** The baseline KPIs and EOQ inputs in
  `scripts/build_demo_data.py` are placeholders, and the "after" figures
  come from a simulation. Replace them with measured figures before evaluation.
- The model never imports Flask or a database. A future API layer can wrap
  `BoxTracker` and persist its records without changing the rules.
