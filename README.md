# Simply Garlic box tracker

A web-based inventory tracking system for Simply Garlic, a South African
vegetable-based product manufacturer. Simply Garlic's Derdepoort facility relies on a manual, paper-based labelling system with no digital record or time-based trail, causing misplaced labels and a loss of production time, which increases the risks of delayed deliveries to clients. This solution gives each box its own ID and follows through production stages while raising alerts when a box stays in a stage too long. It also reports before/after process KPIs and reorder recommendations.  

This was a collaborative effort of group 16:
Grace Lushamba - u23531152
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
  and refresh the page. Registering boxes, stage moves and incidents are
  saved through the API in one transaction each.

## Project layout

```
app.py                  Flask entry point (application factory, blueprints, CLI)
config.py               settings from environment variables / .env
requirements.txt        Python packages
.env.example            template for .env (never commit .env)
database/
  db.py                 MySQL connection per request, init-db CLI command
  schema.sql            CREATE TABLE statements (drops and rebuilds)
  seed.py               realistic seed data for every table
routes/
  pages.py              HTML page routes
  api.py                JSON API: app data, register, move, incidents
services/
  tracker_service.py    loads the rules engine from MySQL, saves what changed
  app_data.py           builds the page data (window.SG_DATA) from MySQL
models/
  repository.py         SQL queries, one section per ERD table
  products.py           product catalogue (10 SKUs)
  tracking.py           box IDs + check character, stage state machine, roles,
                        scans (duplicate/damaged/unknown), incidents, hash-chained audit log
  alerts.py             stage-dwell warning/critical thresholds
  metrics.py            baseline vs after KPIs and comparison
  eoq.py                calculate_eoq, orders_per_year, total_annual_cost,
                        safety_stock, reorder_point, plan_reorder
templates/
  index.html            dashboard: KPI cards, stage pipeline, alerts, investigations
  boxes.html            box board: filters, sortable table, history + exception modal
  scan.html             operator scan & stage update (phone-first)
  register.html         register a box and print its QR label
  reorder.html          EOQ / reorder point per SKU + what-if calculator
  metrics.html          before/after evaluation
static/
  css/styles.css        design tokens (type + spacing scale), components
  js/components.js      reusable navbar, cards, badges, sortable table, toasts
  js/store.js           page state, rule helpers, API calls
  js/pages/*.js         one script per page
tests/                  unittest suites (rules, KPIs, EOQ, JS parity, MySQL integration)
docs/
  ERD.md                entity relationship diagram
  PROCESS_RULES.md      process rules
```
