-- =====================================================================
-- Simply Garlic box tracker - MySQL schema (MySQL 8.0.16+ for CHECK)
--
-- Build from nothing:   flask --app app init-db      (schema + seed data)
--
-- All timestamps are stored in UTC (the application converts to SAST for display).
-- The ERD for these tables is in docs/ERD.md.
-- =====================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS incidents;
DROP TABLE IF EXISTS status_events;
DROP TABLE IF EXISTS boxes;
DROP TABLE IF EXISTS pilot_periods;
DROP TABLE IF EXISTS kpi_baselines;
DROP TABLE IF EXISTS incident_kinds;
DROP TABLE IF EXISTS stage_permissions;
DROP TABLE IF EXISTS stages;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS reorder_policies;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS product_categories;
SET FOREIGN_KEY_CHECKS = 1;


-- ---------------------------------------------------------------------
-- Product catalogue
-- ---------------------------------------------------------------------

-- Perishability depends on the category (fresh pastes and produce), not on
-- the individual product, so it is stored here once (3NF).
CREATE TABLE product_categories (
    category_id     INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    category_name   VARCHAR(40)   NOT NULL,
    is_perishable   BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (category_id),
    UNIQUE KEY uq_product_categories_name (category_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE products (
    product_id      INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    sku             CHAR(3)        NOT NULL,
    product_name    VARCHAR(80)    NOT NULL,
    category_id     INT UNSIGNED   NOT NULL,
    unit_cost       DECIMAL(10,2)  NOT NULL,
    is_active       BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (product_id),
    UNIQUE KEY uq_products_sku (sku),
    KEY ix_products_category (category_id),
    CONSTRAINT fk_products_category FOREIGN KEY (category_id)
        REFERENCES product_categories (category_id),
    CONSTRAINT ck_products_sku CHECK (REGEXP_LIKE(sku, '^[A-Z]{3}$', 'c')),
    CONSTRAINT ck_products_unit_cost CHECK (unit_cost >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE reorder_policies (
    product_id        INT UNSIGNED       NOT NULL,
    annual_demand     INT UNSIGNED       NOT NULL,             -- boxes per year (D)
    order_cost        DECIMAL(10,2)      NOT NULL,             -- R per order / run (S)
    holding_cost      DECIMAL(10,2)      NOT NULL,             -- R per box per year (H)
    daily_demand_std  DECIMAL(8,2)       NOT NULL,             -- boxes (sigma_d)
    lead_time_days    SMALLINT UNSIGNED  NOT NULL,             -- days (L)
    on_hand_qty       INT UNSIGNED       NOT NULL DEFAULT 0,   -- boxes in stock now
    service_level     DECIMAL(4,3)       NOT NULL DEFAULT 0.950,
    working_days      SMALLINT UNSIGNED  NOT NULL DEFAULT 250,
    created_at        DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (product_id),
    CONSTRAINT fk_reorder_policies_product FOREIGN KEY (product_id)
        REFERENCES products (product_id),
    CONSTRAINT ck_reorder_policies_positive CHECK (
        annual_demand > 0 AND order_cost > 0 AND holding_cost > 0
        AND lead_time_days > 0 AND working_days > 0 AND daily_demand_std >= 0),
    CONSTRAINT ck_reorder_policies_service CHECK (service_level > 0 AND service_level < 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- People and places
-- ---------------------------------------------------------------------

CREATE TABLE roles (
    role_code         VARCHAR(20)  NOT NULL,
    role_label        VARCHAR(40)  NOT NULL,
    is_floor_role     BOOLEAN      NOT NULL DEFAULT FALSE,  -- may write (scan, log incidents)
    can_register      BOOLEAN      NOT NULL DEFAULT FALSE,  -- may register boxes / reprint labels
    can_release_hold  BOOLEAN      NOT NULL DEFAULT FALSE,  -- may release or reject held boxes
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (role_code),
    UNIQUE KEY uq_roles_label (role_label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
    user_id     INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    username    VARCHAR(40)   NOT NULL,
    full_name   VARCHAR(80)   NOT NULL,
    role_code   VARCHAR(20)   NOT NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_username (username),
    KEY ix_users_role_active (role_code, is_active),
    CONSTRAINT fk_users_role FOREIGN KEY (role_code) REFERENCES roles (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE locations (
    location_code  VARCHAR(12)  NOT NULL,
    location_name  VARCHAR(60)  NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (location_code),
    UNIQUE KEY uq_locations_name (location_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- Process stages and who may move boxes into them
-- ---------------------------------------------------------------------

-- flow_position is the order of the normal path (1 = In production);
-- NULL for the exception stages On hold and Rejected.
-- warning/critical_minutes are the stage-dwell alert thresholds
-- (NULL = this stage never alerts).
CREATE TABLE stages (
    stage_code             VARCHAR(20)       NOT NULL,
    stage_label            VARCHAR(40)       NOT NULL,
    flow_position          TINYINT UNSIGNED  NULL,
    is_terminal            BOOLEAN           NOT NULL DEFAULT FALSE,
    requires_note          BOOLEAN           NOT NULL DEFAULT FALSE,
    default_location_code  VARCHAR(12)       NOT NULL,
    warning_minutes        INT UNSIGNED      NULL,
    critical_minutes       INT UNSIGNED      NULL,
    created_at             DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (stage_code),
    UNIQUE KEY uq_stages_label (stage_label),
    UNIQUE KEY uq_stages_flow_position (flow_position),
    KEY ix_stages_default_location (default_location_code),
    CONSTRAINT fk_stages_default_location FOREIGN KEY (default_location_code)
        REFERENCES locations (location_code),
    CONSTRAINT ck_stages_thresholds CHECK (
        (warning_minutes IS NULL AND critical_minutes IS NULL)
        OR (warning_minutes > 0 AND critical_minutes > warning_minutes))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE stage_permissions (
    stage_code  VARCHAR(20)  NOT NULL,
    role_code   VARCHAR(20)  NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stage_code, role_code),
    KEY ix_stage_permissions_role (role_code),
    CONSTRAINT fk_stage_permissions_stage FOREIGN KEY (stage_code) REFERENCES stages (stage_code),
    CONSTRAINT fk_stage_permissions_role FOREIGN KEY (role_code) REFERENCES roles (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- Boxes and their stage history
-- ---------------------------------------------------------------------

-- box_id is the printed label ID SG-<SKU>-<YYMMDD>-<SEQ>-<CHECK>.
-- current_stage_code is a deliberate, documented copy of the latest

CREATE TABLE boxes (
    box_id                 CHAR(20)           NOT NULL,
    product_id             INT UNSIGNED       NOT NULL,
    batch_no               VARCHAR(20)        NOT NULL,
    quantity               SMALLINT UNSIGNED  NOT NULL,
    production_date        DATE               NOT NULL,
    sequence_no            SMALLINT UNSIGNED  NOT NULL,
    current_stage_code     VARCHAR(20)        NOT NULL,
    current_location_code  VARCHAR(12)        NOT NULL,
    location_updated_at    DATETIME(6)        NOT NULL,
    label_version          SMALLINT UNSIGNED  NOT NULL DEFAULT 1,
    registered_by          INT UNSIGNED       NOT NULL,
    created_at             DATETIME(6)        NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at             DATETIME(6)        NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (box_id),
    UNIQUE KEY uq_boxes_product_day_sequence (product_id, production_date, sequence_no),
    KEY ix_boxes_stage_location (current_stage_code, current_location_code),
    KEY ix_boxes_location (current_location_code),
    KEY ix_boxes_batch (batch_no),
    KEY ix_boxes_registered_by (registered_by),
    CONSTRAINT fk_boxes_product FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT fk_boxes_stage FOREIGN KEY (current_stage_code) REFERENCES stages (stage_code),
    CONSTRAINT fk_boxes_location FOREIGN KEY (current_location_code) REFERENCES locations (location_code),
    CONSTRAINT fk_boxes_registered_by FOREIGN KEY (registered_by) REFERENCES users (user_id),
    CONSTRAINT ck_boxes_box_id CHECK (REGEXP_LIKE(box_id, '^SG-[A-Z]{3}-[0-9]{6}-[0-9]{4}-[0-9A-Z]$', 'c')),
    CONSTRAINT ck_boxes_quantity CHECK (quantity >= 1),
    CONSTRAINT ck_boxes_sequence CHECK (sequence_no BETWEEN 1 AND 9999),
    CONSTRAINT ck_boxes_label_version CHECK (label_version >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE status_events (
    event_id         BIGINT UNSIGNED         NOT NULL AUTO_INCREMENT,
    box_id           CHAR(20)                NOT NULL,
    from_stage_code  VARCHAR(20)             NULL,      
    to_stage_code    VARCHAR(20)             NOT NULL,
    occurred_at      DATETIME(6)             NOT NULL,
    user_id          INT UNSIGNED            NOT NULL,
    role_code        VARCHAR(20)             NOT NULL,
    location_code    VARCHAR(12)             NOT NULL,
    capture_method   ENUM('scan', 'manual')  NOT NULL DEFAULT 'manual',
    note             VARCHAR(500)            NOT NULL DEFAULT '',
    created_at       DATETIME(6)             NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (event_id),
    KEY ix_status_events_box_time (box_id, occurred_at),
    KEY ix_status_events_stage_time (to_stage_code, occurred_at),
    KEY ix_status_events_from_stage (from_stage_code),
    KEY ix_status_events_user (user_id),
    KEY ix_status_events_role (role_code),
    KEY ix_status_events_location (location_code),
    CONSTRAINT fk_status_events_box FOREIGN KEY (box_id) REFERENCES boxes (box_id),
    CONSTRAINT fk_status_events_from_stage FOREIGN KEY (from_stage_code) REFERENCES stages (stage_code),
    CONSTRAINT fk_status_events_to_stage FOREIGN KEY (to_stage_code) REFERENCES stages (stage_code),
    CONSTRAINT fk_status_events_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_status_events_role FOREIGN KEY (role_code) REFERENCES roles (role_code),
    CONSTRAINT fk_status_events_location FOREIGN KEY (location_code) REFERENCES locations (location_code),
    CONSTRAINT ck_status_events_stage_change CHECK (
        from_stage_code IS NULL OR from_stage_code <> to_stage_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- Exceptions (lost / misplaced boxes, label problems, QC and dispatch errors)
-- ---------------------------------------------------------------------


CREATE TABLE incident_kinds (
    kind_code             VARCHAR(20)  NOT NULL,
    kind_label            VARCHAR(40)  NOT NULL,
    is_tracking_incident  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (kind_code),
    UNIQUE KEY uq_incident_kinds_label (kind_label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- box_id is NULL when the box is not yet known (e.g. an unreadable label); it is filled in once the operator types the printed ID.
-- An incident is open while closed_at IS NULL.
CREATE TABLE incidents (
    incident_id  VARCHAR(12)   NOT NULL,
    kind_code    VARCHAR(20)   NOT NULL,
    box_id       CHAR(20)      NULL,
    note         VARCHAR(500)  NOT NULL DEFAULT '',
    opened_at    DATETIME(6)   NOT NULL,
    opened_by    INT UNSIGNED  NOT NULL,
    closed_at    DATETIME(6)   NULL,
    closed_by    INT UNSIGNED  NULL,
    resolution   VARCHAR(500)  NOT NULL DEFAULT '',
    created_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (incident_id),
    KEY ix_incidents_status (closed_at, kind_code),
    KEY ix_incidents_kind_opened (kind_code, opened_at),
    KEY ix_incidents_box (box_id),
    KEY ix_incidents_opened_by (opened_by),
    KEY ix_incidents_closed_by (closed_by),
    CONSTRAINT fk_incidents_kind FOREIGN KEY (kind_code) REFERENCES incident_kinds (kind_code),
    CONSTRAINT fk_incidents_box FOREIGN KEY (box_id) REFERENCES boxes (box_id),
    CONSTRAINT fk_incidents_opened_by FOREIGN KEY (opened_by) REFERENCES users (user_id),
    CONSTRAINT fk_incidents_closed_by FOREIGN KEY (closed_by) REFERENCES users (user_id),
    CONSTRAINT ck_incidents_id CHECK (REGEXP_LIKE(incident_id, '^INC-[0-9]{4,8}$', 'c')),
    CONSTRAINT ck_incidents_close_time CHECK (closed_at IS NULL OR closed_at >= opened_at),
    CONSTRAINT ck_incidents_closed_by CHECK ((closed_at IS NULL) = (closed_by IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



CREATE TABLE audit_log (
    seq          INT UNSIGNED             NOT NULL,
    occurred_at  DATETIME(6)              NOT NULL,
    user_id      INT UNSIGNED             NOT NULL,
    role_code    VARCHAR(20)              NOT NULL,
    action       VARCHAR(20)              NOT NULL,
    outcome      ENUM('OK', 'REJECTED')   NOT NULL DEFAULT 'OK',
    box_id       CHAR(20)                 NULL,
    detail       VARCHAR(1000)            NOT NULL DEFAULT '',
    prev_hash    CHAR(64)                 NOT NULL,
    entry_hash   CHAR(64)                 NOT NULL,
    created_at   DATETIME(6)              NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (seq),
    UNIQUE KEY uq_audit_log_entry_hash (entry_hash),
    KEY ix_audit_log_box_seq (box_id, seq),
    KEY ix_audit_log_action_outcome (action, outcome),
    KEY ix_audit_log_user (user_id),
    KEY ix_audit_log_role (role_code),
    CONSTRAINT fk_audit_log_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_audit_log_role FOREIGN KEY (role_code) REFERENCES roles (role_code),
    CONSTRAINT ck_audit_log_seq CHECK (seq >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



CREATE TABLE kpi_baselines (
    baseline_id                    INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    label                          VARCHAR(80)    NOT NULL,
    period_days                    DECIMAL(6,2)   NOT NULL,
    boxes_dispatched               INT UNSIGNED   NOT NULL,
    tracking_incidents             INT UNSIGNED   NOT NULL,
    investigations                 INT UNSIGNED   NOT NULL,
    investigation_minutes          DECIMAL(10,2)  NOT NULL,
    status_lookups                 INT UNSIGNED   NOT NULL,
    mean_status_retrieval_seconds  DECIMAL(8,2)   NOT NULL,
    mean_stage_dwell_hours         DECIMAL(6,2)   NOT NULL,
    quality_errors                 INT UNSIGNED   NOT NULL,
    dispatch_errors                INT UNSIGNED   NOT NULL,
    traced_boxes                   INT UNSIGNED   NOT NULL,
    labour_hours                   DECIMAL(8,2)   NOT NULL,
    created_at                     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (baseline_id),
    UNIQUE KEY uq_kpi_baselines_label (label),
    CONSTRAINT ck_kpi_baselines_period CHECK (period_days > 0 AND labour_hours > 0),
    CONSTRAINT ck_kpi_baselines_traced CHECK (traced_boxes <= boxes_dispatched)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE pilot_periods (
    pilot_id                       INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    label                          VARCHAR(80)    NOT NULL,
    baseline_id                    INT UNSIGNED   NOT NULL,
    starts_at                      DATETIME(6)    NOT NULL,
    ends_at                        DATETIME(6)    NULL,
    labour_hours_per_day           DECIMAL(6,2)   NOT NULL,
    status_lookups_per_day         INT UNSIGNED   NOT NULL,
    mean_status_retrieval_seconds  DECIMAL(8,2)   NOT NULL,
    is_active                      BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at                     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (pilot_id),
    UNIQUE KEY uq_pilot_periods_label (label),
    KEY ix_pilot_periods_active (is_active, starts_at),
    KEY ix_pilot_periods_baseline (baseline_id),
    CONSTRAINT fk_pilot_periods_baseline FOREIGN KEY (baseline_id) REFERENCES kpi_baselines (baseline_id),
    CONSTRAINT ck_pilot_periods_window CHECK (ends_at IS NULL OR ends_at > starts_at),
    CONSTRAINT ck_pilot_periods_labour CHECK (labour_hours_per_day > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
