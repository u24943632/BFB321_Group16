# Entity relationship diagram

The diagram matches [`database/schema.sql`](../database/schema.sql) table for
table and column for column. GitHub renders the Mermaid block below as a
diagram. To export it as an image, paste the block into <https://mermaid.live>
or run `npx @mermaid-js/mermaid-cli -i erd.mmd -o docs/erd.png`.

Key: **PK** primary key · **FK** foreign key · **UK** unique key.
Crow's-foot notation: `||` exactly one, `o|` zero or one, `|{` one or more,
`o{` zero or more.

```mermaid
erDiagram
    product_categories ||--o{ products : "groups"
    products ||--o| reorder_policies : "has policy (1:1)"
    products ||--o{ boxes : "is packed in"

    roles ||--o{ users : "is held by"
    roles ||--o{ stage_permissions : "may move into"
    stages ||--o{ stage_permissions : "accepts"
    locations ||--o{ stages : "default location of"

    stages ||--o{ boxes : "current stage of"
    locations ||--o{ boxes : "current location of"
    users ||--o{ boxes : "registered"

    boxes ||--|{ status_events : "history"
    stages ||--o{ status_events : "to stage"
    stages |o--o{ status_events : "from stage"
    users ||--o{ status_events : "recorded by"
    roles ||--o{ status_events : "role at the time"
    locations ||--o{ status_events : "scanned at"

    incident_kinds ||--o{ incidents : "classifies"
    boxes |o--o{ incidents : "concerns"
    users ||--o{ incidents : "opened by"
    users |o--o{ incidents : "closed by"

    users ||--o{ audit_log : "acted"
    roles ||--o{ audit_log : "role at the time"

    kpi_baselines ||--o{ pilot_periods : "compared with"

    product_categories {
        int category_id PK
        varchar category_name UK
        boolean is_perishable
        datetime created_at
        datetime updated_at
    }
    products {
        int product_id PK
        char sku UK "CHECK ^[A-Z]{3}$"
        varchar product_name
        int category_id FK
        decimal unit_cost "DECIMAL(10,2) >= 0"
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    reorder_policies {
        int product_id PK, FK
        int annual_demand "> 0"
        decimal order_cost "DECIMAL(10,2) > 0"
        decimal holding_cost "DECIMAL(10,2) > 0"
        decimal daily_demand_std
        smallint lead_time_days "> 0"
        int on_hand_qty
        decimal service_level "0 < x < 1"
        smallint working_days
        datetime created_at
        datetime updated_at
    }
    roles {
        varchar role_code PK
        varchar role_label UK
        boolean is_floor_role
        boolean can_register
        boolean can_release_hold
        datetime created_at
        datetime updated_at
    }
    users {
        int user_id PK
        varchar username UK
        varchar full_name
        varchar role_code FK
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    locations {
        varchar location_code PK
        varchar location_name UK
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    stages {
        varchar stage_code PK
        varchar stage_label UK
        tinyint flow_position UK "NULL for exception stages"
        boolean is_terminal
        boolean requires_note
        varchar default_location_code FK
        int warning_minutes "dwell alert"
        int critical_minutes "> warning_minutes"
        datetime created_at
        datetime updated_at
    }
    stage_permissions {
        varchar stage_code PK, FK
        varchar role_code PK, FK
        datetime created_at
    }
    boxes {
        char box_id PK "SG-SKU-YYMMDD-SEQ-C"
        int product_id FK "UK with date + seq"
        varchar batch_no
        smallint quantity ">= 1"
        date production_date
        smallint sequence_no "1-9999"
        varchar current_stage_code FK
        varchar current_location_code FK
        datetime location_updated_at
        smallint label_version ">= 1"
        int registered_by FK
        datetime created_at
        datetime updated_at
    }
    status_events {
        bigint event_id PK
        char box_id FK
        varchar from_stage_code FK "NULL on registration"
        varchar to_stage_code FK
        datetime occurred_at
        int user_id FK
        varchar role_code FK
        varchar location_code FK
        enum capture_method "scan | manual"
        varchar note
        datetime created_at
    }
    incident_kinds {
        varchar kind_code PK
        varchar kind_label UK
        boolean is_tracking_incident
        datetime created_at
        datetime updated_at
    }
    incidents {
        varchar incident_id PK "INC-0001"
        varchar kind_code FK
        char box_id FK "NULL until known"
        varchar note
        datetime opened_at
        int opened_by FK
        datetime closed_at "NULL = open"
        int closed_by FK
        varchar resolution
        datetime created_at
        datetime updated_at
    }
    audit_log {
        int seq PK
        datetime occurred_at
        int user_id FK
        varchar role_code FK
        varchar action
        enum outcome "OK | REJECTED"
        char box_id "no FK - may be unregistered"
        varchar detail
        char prev_hash
        char entry_hash UK "SHA-256"
        datetime created_at
    }
    kpi_baselines {
        int baseline_id PK
        varchar label UK
        decimal period_days
        int boxes_dispatched
        int tracking_incidents
        int investigations
        decimal investigation_minutes
        int status_lookups
        decimal mean_status_retrieval_seconds
        decimal mean_stage_dwell_hours
        int quality_errors
        int dispatch_errors
        int traced_boxes "<= boxes_dispatched"
        decimal labour_hours
        datetime created_at
        datetime updated_at
    }
    pilot_periods {
        int pilot_id PK
        varchar label UK
        int baseline_id FK
        datetime starts_at
        datetime ends_at "NULL = running"
        decimal labour_hours_per_day
        int status_lookups_per_day
        decimal mean_status_retrieval_seconds
        boolean is_active
        datetime created_at
        datetime updated_at
    }
```

## Relationships

| Relationship | Type | How it is stored |
|---|---|---|
| product_categories → products | one-to-many | `products.category_id` FK |
| products → reorder_policies | one-to-one | `reorder_policies.product_id` is both PK and FK |
| products → boxes | one-to-many | `boxes.product_id` FK |
| roles → users | one-to-many | `users.role_code` FK |
| **stages ↔ roles** | **many-to-many** | resolved by the link table **`stage_permissions`** (composite PK `stage_code, role_code`) |
| locations → stages | one-to-many | `stages.default_location_code` FK |
| stages / locations / users → boxes | one-to-many | `current_stage_code`, `current_location_code`, `registered_by` FKs |
| boxes → status_events | one-to-many (at least one) | `status_events.box_id` FK |
| stages → status_events | one-to-many (twice) | `from_stage_code` (nullable), `to_stage_code` FKs |
| users / roles / locations → status_events | one-to-many | `user_id`, `role_code`, `location_code` FKs |
| incident_kinds → incidents | one-to-many | `incidents.kind_code` FK |
| boxes → incidents | zero-or-one-to-many | `incidents.box_id` FK (nullable) |
| users → incidents | one-to-many (twice) | `opened_by`, `closed_by` (nullable) FKs |
| users / roles → audit_log | one-to-many | `user_id`, `role_code` FKs |
| kpi_baselines → pilot_periods | one-to-many | `pilot_periods.baseline_id` FK |

## Indexes

Every primary and foreign key is indexed. Additional lookup and composite indexes:

| Table | Index | Columns | Used for |
|---|---|---|---|
| products | `uq_products_sku` (unique) | `sku` | SKU lookups, no duplicate SKUs |
| users | `ix_users_role_active` | `role_code, is_active` | active user for a role |
| boxes | `uq_boxes_product_day_sequence` (unique) | `product_id, production_date, sequence_no` | one label ID per box per day |
| boxes | `ix_boxes_stage_location` | `current_stage_code, current_location_code` | box board filters, "what is at QC now" |
| boxes | `ix_boxes_batch` | `batch_no` | batch recall |
| status_events | `ix_status_events_box_time` | `box_id, occurred_at` | a box's history in order |
| status_events | `ix_status_events_stage_time` | `to_stage_code, occurred_at` | dwell and throughput KPIs |
| incidents | `ix_incidents_status` | `closed_at, kind_code` | open investigations (`closed_at IS NULL`) |
| incidents | `ix_incidents_kind_opened` | `kind_code, opened_at` | incident rate per period |
| audit_log | `uq_audit_log_entry_hash` (unique) | `entry_hash` | tamper evidence |
| audit_log | `ix_audit_log_box_seq` | `box_id, seq` | audit trail for one box |
| audit_log | `ix_audit_log_action_outcome` | `action, outcome` | rejected-attempt reports |
| pilot_periods | `ix_pilot_periods_active` | `is_active, starts_at` | the current pilot |

## Design notes (normalisation)

* **3NF.** Supplier-style repetition is avoided: category details live in
  `product_categories`; product details are never copied into `boxes`;
  usernames and role labels are referenced by key, not repeated.
  Perishability depends on the category, so it is stored there, not per product.
* **`boxes.current_stage_code`** is the one deliberate copy: it equals the
  latest `status_events.to_stage_code` and is written in the same transaction
  as the event, so the box board can filter by stage through an index instead
  of reading every box's history. A test checks the two always agree.
* **`status_events.role_code` / `audit_log.role_code`** record the role the
  user held *at the time*; a user's role can change later, so this is history,
  not duplication.
* **`audit_log.box_id`** has no foreign key on purpose: a rejected scan of an
  unregistered label must still be recorded with the text that was scanned.
* **Money** uses `DECIMAL(10,2)`; timestamps use `DATETIME(6)` (UTC) where
  microseconds matter for ordering and for the audit hash.
* `created_at` / `updated_at` audit fields are on every main table. The
  append-only logs (`status_events`, `audit_log`) and the link table only
  have `created_at`, because their rows are never updated.
