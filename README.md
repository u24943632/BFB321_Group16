# Simply Garlic box tracker

A web-based inventory tracking system for Simply Garlic, a South African
vegetable-based product manufacturer. Simply Garlic's Derdepoort facility relies on a manual, paper-based labelling system with no digital record or time-based trail, causing misplaced labels and a loss of production time, which increases the risks of delayed deliveries to clients. This solution gives each box its own ID and follows through production stages while raising alerts when a box stays in a stage too long. It also reports before/after process KPIs and reorder recommendations.  

This was a collaborative effort of group 16:
Elna-Grace Lushamba - u23531152
Marcello Schmidt- u23563789
Migal Groenewald – u24943632

Task Allocation:
Data models and database integration (B1) - Migal Groenewald

## Run it locally

You need **Python 3.10+** and **MySQL 8.0.16+** (for CHECK constraints), plus
an internet connection to load Bootstrap from its CDN. Node.js is optional and
only used for the browser/Python parity test.

The commands differ slightly between macOS and Windows, so follow the section
for your computer. Run every command from the project folder (the one that
contains `app.py`).

### macOS

**1. Install Python and MySQL (first time only).** With [Homebrew](https://brew.sh):

```bash
brew install python mysql
brew services start mysql      # starts MySQL now and at every login
```

A fresh Homebrew MySQL has user `root` with no password.

**2. Set up the project (first time only).**

```bash
cd path/to/simplyGarlicFinal
python3 -m venv .venv
source .venv/bin/activate      
pip install -r requirements.txt
cp .env.example .env          
```

Open `.env` and set `DB_USER` / `DB_PASSWORD` to your MySQL login
(for a fresh Homebrew install: `DB_USER=root` and an empty `DB_PASSWORD=`).

**3. Create the database, all tables and the seed data (one command).**

```bash
flask --app app init-db
```

**4. Start the app.**

```bash
flask --app app run --port 5001
```

Open <http://127.0.0.1:5001>. Stop the app with **Ctrl + C**.

**Every time after that:**

```bash
cd path/to/simplyGarlicFinal
source .venv/bin/activate
flask --app app run --port 5001
```

### Windows

Use **PowerShell** (or Command Prompt where noted).

**1. Install Python and MySQL (first time only).**

* Python: download it from <https://www.python.org/downloads/> and tick
  **"Add python.exe to PATH"** in the installer.
* MySQL: download **MySQL Installer** from <https://dev.mysql.com/downloads/installer/>,
  choose **Server only**, and keep the defaults. Write down the **root password**
  you set; the installer runs MySQL as a Windows service that starts automatically.

**2. Set up the project (first time only).**

```powershell
cd path\to\simplyGarlicFinal
python -m venv .venv
.venv\Scripts\Activate.ps1     
pip install -r requirements.txt
copy .env.example .env         
```

If PowerShell says running scripts is disabled, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Open `.env` in Notepad and set `DB_USER=root` and `DB_PASSWORD=` to the root
password you chose in the MySQL installer.

**3. Create the database, all tables and the seed data (one command).**

```powershell
flask --app app init-db
```

**4. Start the app.**

```powershell
flask --app app run --port 5001
```

Open <http://127.0.0.1:5001>. Stop the app with **Ctrl + C**.

**Every time after that:**

```powershell
cd path\to\simplyGarlicFinal
.venv\Scripts\Activate.ps1
flask --app app run --port 5001
```

### Both systems

* **Run the tests:** `python -m unittest discover -v` (with the virtual
  environment active). The MySQL tests build a separate `<DB_NAME>_test`
  database and are skipped if MySQL is not reachable.
* **Fresh demo data:** run `flask --app app init-db` again. It drops and
  rebuilds every table. Seed data is timed relative to now, so stage-dwell
  alerts look realistic right after seeding and grow as real time passes;
  reseed just before a demo.
* **Do not commit `.env`.** It holds your own passwords and is ignored by
  git; `.env.example` is the template that is committed.
* **Using a dedicated MySQL user instead of root:** the user needs permission to
  create the database, or create it first and grant the user all privileges:

  ```sql
  CREATE USER 'simply_garlic_app'@'localhost' IDENTIFIED BY 'choose-a-password';
  CREATE DATABASE simply_garlic;
  GRANT ALL PRIVILEGES ON simply_garlic.* TO 'simply_garlic_app'@'localhost';
  GRANT ALL PRIVILEGES ON simply_garlic_test.* TO 'simply_garlic_app'@'localhost';
  ```

### Troubleshooting

| You see | Fix |
|---|---|
| `flask: command not found` / `'flask' is not recognized` | Activate the virtual environment first (step 2); the prompt should show `(.venv)` |
| `Address already in use` / port 5000 is in use | Use `--port 5001` (macOS AirPlay uses port 5000) |
| `Could not locate a Flask application` | `cd` into the project folder (the one with `app.py`) |
| Page shows only the header, no data | Wrong settings in `.env`; fix them and restart Flask |
| `Access denied for user ...` | `DB_USER` / `DB_PASSWORD` in `.env` do not match your MySQL login |
| `Can't connect to MySQL server` | MySQL is not running: `brew services start mysql` (macOS) or start the **MySQL80** service in Windows *Services* |
| Changes to `.env` have no effect | `.env` is read when Flask starts: Ctrl + C and start it again |

## Data model and database (MySQL)

![Entity relationship diagram](docs/erd.png)

* **Schema:** [`database/schema.sql`](database/schema.sql) - 15 tables in
  3NF with primary/foreign keys, `NOT NULL`, `UNIQUE`, `CHECK` constraints,
  `DECIMAL` money columns, `created_at`/`updated_at` audit fields and
  composite lookup indexes.
* **ERD:** [`docs/ERD.md`](docs/ERD.md) - diagram, relationship table
  (including the `stage_permissions` many-to-many link table), index list
  and normalisation notes.
* **Seed data:** [`database/seed.py`](database/seed.py) - every table gets
  realistic data. Boxes, stage history, incidents and the audit trail come
  from a simulated three-day pilot run through the real process rules, so the
  seed cannot contain an invalid move and the audit hash chain verifies.
* **Flask connection:** [`database/db.py`](database/db.py) reads `.env`,
  opens one PyMySQL connection per request and closes it at teardown.
  All SQL is in [`models/repository.py`](models/repository.py) and uses
  `%s` placeholders, never string joining.
* **Data on screen comes from MySQL.** Each page loads `/api/app-data.js`,
  built from the database on every request; change a row (for example
  `UPDATE products SET product_name = 'Black garlic 500 g' WHERE sku = 'BGA';`)
  and refresh the page. Every write (boxes, stage moves, incidents, products,
  reorder policies) is saved through the [REST API](#rest-api) in one transaction.

## REST API

All endpoints live under `/api`, take and return JSON, and are defined in
four blueprints under [`routes/api/`](routes/api/). Try them in a browser
(GET), with curl or Postman, e.g.
`curl "http://127.0.0.1:5001/api/boxes?stage=awaiting_qc&per_page=5"`.

### Conventions

* **Who is acting:** every write (POST / PATCH / PUT) sends the `username`
  of an active user in its JSON body (see [demo users](#demo-users)). The
  user's role decides what is allowed, and the action is written to the
  hash-chained audit trail - refused attempts too.
* **Response shape** - the same everywhere:

  ```json
  one resource   {"ok": true, "box": {...}}
  a list         {"ok": true, "items": [...], "pagination": {...}, "filters": {...}}
  an error       {"ok": false, "error": "'quantity' must be a whole number.", "status": 400}
  ```

* **Pagination** on list endpoints: `?page=` (default 1) and `?per_page=`
  (default 20, max 100). The response holds
  `"pagination": {"page": 2, "per_page": 20, "total": 57, "pages": 3, "has_next": true, "has_prev": true}`.
* **Validation:** missing fields, wrong types, out-of-range numbers, unknown
  fields and unknown filter values are refused with `400` and a message that
  names the field. Duplicates (same SKU or category) return `409`.

| Status | When |
|---|---|
| `200` OK / `201` Created | read or update succeeded / a record was created |
| `400` Bad request | invalid input, unknown user, product or location |
| `403` Forbidden | the user's role may not do this (e.g. an executive registering a box) |
| `404` Not found | unknown box, incident, product or URL |
| `405` Method not allowed | e.g. `DELETE /api/boxes` |
| `409` Conflict | illegal stage move (e.g. dispatch before QC), duplicate SKU or category |
| `503` / `500` | MySQL unreachable / unexpected error (logged; no stack trace is shown) |

### Boxes - [`routes/api/boxes.py`](routes/api/boxes.py)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/boxes` | List boxes. Filters: `stage`, `sku`, `location`, `batch_no`, `q` (ID/batch text), `in_process=true\|false`, `sort=newest\|oldest\|box_id\|longest_in_stage`; paginated |
| POST | `/api/boxes` | Register a box (new unique ID, starts *In production*) -> `201` |
| GET | `/api/boxes/<box_id>` | Status lookup: full stage history, current alert, incidents and audit trail |
| PATCH | `/api/boxes/<box_id>` | Correct `batch_no` / `quantity` (needs `reason`) and/or record a new `location` |
| POST | `/api/boxes/<box_id>/moves` | Move the box to another stage; every process rule is enforced -> `201` |
| POST | `/api/boxes/<box_id>/label-reprints` | Reprint a damaged label (same ID, label version + 1) -> `201` |
| POST | `/api/scans` | Raw QR scan at a station: accepted, duplicate ignored, or damaged / unknown / suspected-duplicate label (opens an incident) |

```text
POST /api/boxes
  {"username": "thabo.m", "sku": "BGA", "batch_no": "BGA-2611-A", "quantity": 12, "location": "PROD"}
-> 201 {"ok": true, "box": {"box_id": "SG-BGA-261120-0004-N", "sku": "BGA", "batch_no": "BGA-2611-A",
        "quantity": 12, "location": "PROD", "label_version": 1, "created_at": "...",
        "history": [{"from": null, "to": "in_production", "at": "...", "user": "thabo.m",
                     "role": "operator", "location": "PROD", "method": "manual", "note": "Box registered"}]}}

POST /api/boxes/SG-BGA-261120-0004-N/moves
  {"username": "thabo.m", "to_stage": "awaiting_qc", "location": "QC-STAGE", "method": "scan"}
-> 201 {"ok": true, "box": {...}}
   409 {"ok": false, "error": "... cannot move from 'In production' to 'Dispatched'. Allowed next: Awaiting QC, On hold.", "status": 409}

PATCH /api/boxes/SG-BGA-261120-0004-N
  {"username": "johan.v", "quantity": 10, "reason": "Recounted at packing"}
-> 200 {"ok": true, "box": {...}}

GET /api/boxes?stage=awaiting_qc&per_page=2
-> 200 {"ok": true, "items": [{"box_id": "...", "sku": "BGA", "product_name": "Black garlic", "batch_no": "...",
        "quantity": 12, "stage": "awaiting_qc", "stage_label": "Awaiting QC", "stage_entered_at": "...",
        "location": "QC-STAGE", "location_updated_at": "...", "label_version": 1, "created_at": "..."}, ...],
        "pagination": {...}, "filters": {"stage": "awaiting_qc", "sort": "newest"}}
```

Stage codes: `in_production`, `awaiting_qc`, `qc_complete`, `packaging_complete`,
`ready_for_dispatch`, `dispatched`, `on_hold`, `rejected`. Location codes:
`PROD`, `QC-STAGE`, `QC-BAY`, `PACK`, `DSP-STG`, `DSP-DOCK`, `QUARANTINE`, `WASTE`.

### Incidents - [`routes/api/incidents.py`](routes/api/incidents.py)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/incidents` | List incidents. Filters: `status=open\|closed`, `kind`, `box_id`; paginated |
| POST | `/api/incidents` | Log an exception (misplaced box, damaged label, QC or dispatch error ...) -> `201` |
| GET | `/api/incidents/<id>` | One incident |
| PATCH | `/api/incidents/<id>` | Link a box (`box_id`) and/or close it (`"status": "closed"`, `resolution`, optional `found_location`) |

```text
POST  /api/incidents       {"username": "thabo.m", "kind": "misplaced", "box_id": "SG-...", "note": "Not on the QC rack at 10:15"}
PATCH /api/incidents/INC-0007  {"username": "johan.v", "status": "closed", "resolution": "Found behind pallet 4", "found_location": "PACK"}
-> 200 {"ok": true, "incident": {"id": "INC-0007", "kind": "misplaced", "closed_at": "...", ...}, "box": {...}}
```

Incident kinds: `misplaced`, `mislabelled`, `damaged_label`, `duplicate_label`,
`unknown_label`, `quality_error`, `dispatch_error`.

### Products, categories and reorder policies - [`routes/api/products.py`](routes/api/products.py)

Writes need a **supervisor** (`johan.v`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/products` | List products. Filters: `category`, `active=true\|false`, `q` (name/SKU); paginated |
| POST | `/api/products` | Add a product, optionally with its `reorder_policy` -> `201` (`409` if the SKU exists) |
| GET | `/api/products/<sku>` | One product with its current reorder plan |
| PATCH | `/api/products/<sku>` | Change `name`, `category`, `unit_cost` or `active` |
| GET | `/api/products/<sku>/reorder-policy` | Reorder inputs and the EOQ / safety stock / reorder point plan |
| PUT | `/api/products/<sku>/reorder-policy` | Create or replace the policy |
| PATCH | `/api/products/<sku>/reorder-policy` | Change some inputs, e.g. `on_hand_qty` after a stock count; returns the recalculated plan |
| GET | `/api/categories` | Categories with their product counts |
| POST | `/api/categories` | Add a category (`name`, `perishable`) -> `201` |

```text
POST /api/products
  {"username": "johan.v", "sku": "GRW", "name": "Garlic wraps", "category": "Fresh produce", "unit_cost": 120.50,
   "reorder_policy": {"annual_demand": 1200, "order_cost": 500, "holding_cost": 25,
                      "daily_demand_std": 2, "lead_time_days": 3, "on_hand_qty": 40}}
-> 201 {"ok": true, "product": {"sku": "GRW", "name": "Garlic wraps", "category": "Fresh produce", "perishable": true,
        "unit_cost": 120.5, "active": true, "reorder": {"eoq": 219.1, "safety_stock": 5.7, "reorder_point": 20.1,
        "status": "OK", ...}}}

PATCH /api/products/BGA/reorder-policy   {"username": "johan.v", "on_hand_qty": 20}
-> 200 {"ok": true, "plan": {"sku": "BGA", "on_hand": 20, "eoq": 464.8, "reorder_point": 194.0, "status": "CRITICAL", ...}}
```

Reorder policy fields: `annual_demand` (boxes/year), `order_cost` (R per order),
`holding_cost` (R per box per year), `daily_demand_std` (boxes), `lead_time_days`,
`on_hand_qty` (default 0), `service_level` (0-1, default 0.95), `working_days` (default 250).

### Reports - [`routes/api/reports.py`](routes/api/reports.py)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/metrics/summary` | Live dashboard KPIs, calculated from MySQL on each call |
| GET | `/api/metrics/kpis` | Before (paper baseline) vs after (pilot) comparison |
| GET | `/api/alerts` | Boxes over their stage-time limit; filter `severity=warning\|critical` |
| GET | `/api/reorder` | EOQ / reorder plan for every product; filter `status=OK\|REORDER\|CRITICAL` |
| GET | `/api/app-data` | Everything the pages show (also served as `/api/app-data.js` for a `<script>` tag) |
| GET | `/api/health` | `{"ok": true, "database": "up"}` when MySQL is reachable, otherwise `503` |

```text
GET /api/metrics/summary            (numbers depend on when the data was seeded)
-> 200 {"ok": true, "summary": {"boxes_total": 61, "boxes_in_process": 23, "boxes_by_stage": {...},
        "ready_for_dispatch": 4, "dispatched_24h": 9, "alerts_critical": 2, "alerts_warning": 3,
        "open_incidents": 2, "mean_hours_in_current_stage": 3.4, "traceability_coverage_pct": 100.0,
        "active_products": 10, "stock_on_hand_boxes": 962, "stock_value": 228340.0,
        "items_to_reorder": 4, "items_critical": 1, "generated_at": "..."}}
```

## End-to-end flows

Every flow goes from a form, through the API, into MySQL and back onto the
screen without a page reload (the page updates from the API response and
shows a confirmation). The dashboard and box board also re-read MySQL every
30 seconds, so a scan made on another device appears on its own.

### Demo users

There is no password login in this prototype: choose who you are in the
**Signed in as** list at the top right. The choice sets the `username` sent
with every write.

| User | Role | May |
|---|---|---|
| `thabo.m` (Thabo Mokoena) | Operator | register boxes, scan / move boxes, log incidents |
| `lerato.k` (Lerato Khumalo) | QC inspector | sign off QC, scan / move boxes, log incidents |
| `johan.v` (Johan van Wyk) | Supervisor | everything, incl. release / reject held boxes and change the product catalogue |
| `naledi.d` (Naledi Dlamini) | Executive | read only |

### The flows

| # | Flow (page) | API -> tables | What you see |
|---|---|---|---|
| 1 | **Register a box** (Register box, operator) | `POST /api/boxes` -> `boxes`, `status_events`, `audit_log`, `reorder_policies.on_hand_qty` +1 | QR label with the new unique ID; the box appears on the box board and dashboard |
| 2 | **Move a box through the stages** (Scan & update) | `POST /api/boxes/<id>/moves` -> `status_events`, `boxes`, `audit_log` | Only allowed next stages are offered; a box cannot be *Ready for dispatch* before QC and packaging (`409`). Dispatching takes the box out of stock |
| 3 | **Exception handling** (Box board -> View -> Log an exception / Close incident) | `POST /api/incidents`, `PATCH /api/incidents/<id>` -> `incidents`, `boxes.current_location_code` | Open investigation on the dashboard; closing it with "Found at" updates the box's location |
| 4 | **Update stock, model recalculates** (Reorder -> Update, supervisor) | `PATCH /api/products/<sku>/reorder-policy` -> `reorder_policies` | `models/eoq.py` recalculates EOQ, safety stock and reorder point; the row, the status badge and the dashboard's *Items to reorder* change |
| 5 | **Add a seasonal product** (Products -> Add product, supervisor) | `POST /api/products` (+ `POST /api/categories`) -> `products`, `product_categories`, `reorder_policies` | The product is listed with its EOQ plan and can be chosen on Register box straight away (proposal objective 2) |
| 6 | **Correct a box** (Box board -> View -> Correct box details) | `PATCH /api/boxes/<id>` -> `boxes`, `audit_log` | Batch / quantity corrected with a reason; the change is in the audit trail |

**The supply chain model drives flows 1, 2, 4 and 5:** stock on hand follows
the boxes (a registered box adds one finished box; a dispatched or rejected box
removes one), and every change re-runs the EOQ / reorder point model, so the
reorder advice on the dashboard is always based on current stock.

## Metrics and KPIs in the app

| Where | KPIs (all from live MySQL data) |
|---|---|
| Dashboard cards (`GET /api/metrics/summary`) | boxes in process, critical alerts, warnings, open investigations, ready for dispatch, dispatched in the last 24 h, average time in current stage, traceability coverage, stock value (R), items to reorder / below safety stock, active products |
| Dashboard | boxes and average time per stage (pipeline), open investigations, items to reorder |
| Reorder page | boxes on hand, stock value, items to reorder, items below safety stock; EOQ, safety stock, reorder point, orders per year and annual cost per product |
| KPIs page (`GET /api/metrics/kpis`) | before (paper) vs after (pilot): box throughput, lost / misplaced / mislabelled label rate, investigation time, status-retrieval time, stage dwell time, quality & dispatch error rate, traceability coverage, labour utilisation; plus scan compliance and dwell time per stage |

## Error handling and logging

* [`routes/errors.py`](routes/errors.py) handles every error in one place:
  API requests get the JSON error shape above, pages get a friendly error
  page ([`templates/error.html`](templates/error.html)), and no stack trace
  is ever sent to the browser.
* Failures are logged with Python's `logging` module (warnings for 4xx,
  the full stack trace for 5xx), and every API call is logged with its status
  and duration. Set the level with `LOG_LEVEL` in `.env`.
* Each write runs in one transaction. If anything fails the transaction is
  rolled back (in the tracker session, in the error handler and again at
  request teardown), so a half-finished write is never saved.

## Configuration

[`config.py`](config.py) has one class per environment; choose it with
`APP_ENV` in `.env` (default `development`).

| `APP_ENV` | Class | Differences |
|---|---|---|
| `development` | `DevelopmentConfig` | `DEBUG` on, debug logging |
| `testing` | `TestingConfig` | separate `<DB_NAME>_test` database, quiet logging (used by the tests) |
| `production` | `ProductionConfig` | no debug; refuses to start without a real `SECRET_KEY` |

Database settings (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`)
always come from the environment / `.env`, never from code.

## Known Limitations

- The system currently supports one active scan per box at a time — concurrent scans from multiple devices on the same box are not handled
- QR code images are generated in memory and not persisted to disk by default
- ABC classification thresholds (70/20/10) are hardcoded in `models.py` and can be made configurable in a future iteration

## Project layout

```
app.py                  Flask entry point: application factory, blueprints, request logging
config.py               settings per environment (development / testing / production)
requirements.txt        Python packages
.env.example            template for .env (never commit .env)
database/
  db.py                 MySQL connection per request, rollback, init-db CLI command
  schema.sql            CREATE TABLE statements (drops and rebuilds)
  seed.py               realistic seed data for every table
routes/                 HTTP layer (no SQL here)
  pages.py              HTML page routes
  errors.py             central error handlers (JSON for /api, HTML for pages)
  api/
    boxes.py            boxes, stage moves, scans, label reprints     (blueprint boxes_api)
    incidents.py        exceptions / investigations                   (incidents_api)
    products.py         product catalogue, categories, reorder policy (products_api)
    reports.py          KPIs, alerts, reorder advice, page data       (reports_api)
    common.py           validation, pagination, response envelope
services/               glue between routes and models
  tracker_service.py    loads the rules engine from MySQL, saves what changed, stock on hand
  catalogue_service.py  add / change products, categories and reorder policies
  app_data.py           JSON shapes, page data and the live KPI summary
  errors.py             error types with their HTTP status
models/                 framework-free business logic + SQL
  repository.py         all SQL queries (parameterised), one section per ERD table
  products.py           product catalogue (launch SKUs; run-time catalogue comes from MySQL)
  tracking.py           box IDs + check character, stage state machine, roles,
                        scans (duplicate/damaged/unknown), incidents, hash-chained audit log
  alerts.py             stage-dwell warning/critical thresholds
  metrics.py            baseline vs after KPIs and comparison
  eoq.py                calculate_eoq, orders_per_year, total_annual_cost,
                        safety_stock, reorder_point, plan_reorder
templates/
  base.html             shared layout: head, navbar slot, scripts
  index.html            dashboard: KPI cards, stage pipeline, investigations, reorder list
  boxes.html            box board: filters, sortable table, history, exceptions, corrections
  scan.html             operator scan & stage update (phone-first)
  register.html         register a box and print its QR label
  products.html         product catalogue: paged list, add / edit products and categories
  reorder.html          EOQ / reorder point per SKU, stock update, what-if calculator
  metrics.html          before/after evaluation
  error.html            friendly error page
static/
  css/styles.css        design tokens (type + spacing scale), components
  js/components.js      reusable navbar, cards, badges, sortable table, pager, toasts
  js/store.js           page state, rule helpers, API calls, live refresh
  js/pages/*.js         one script per page
tests/
  test_api.py           API validation, error handling, logging, config (no MySQL needed)
  test_database.py      MySQL integration: schema, seed, every endpoint end to end
  test_catalogue_and_stock.py  catalogue from MySQL, corrections, stock bookkeeping
  test_tracking.py, test_alerts_metrics.py, test_eoq.py   business rules
  test_js_parity.py     browser rules match the Python rules (needs Node.js)
docs/
  ERD.md                entity relationship diagram
  PROCESS_RULES.md      process rules
```
