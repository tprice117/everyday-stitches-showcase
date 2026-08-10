-- 006_create_import_audit.sql
-- Admin worklist of SKUs the sync could not place. A line item is skipped when
-- its SKU matches neither a bundle (expanded during mapping) nor a pattern
-- (looked up during the upsert) — previously that was only printed to the run
-- log and lost. Each skipped SKU lands here so an admin can act on it.
--
-- This is a WORKLIST, not an append-only event log: the import is idempotent and
-- gets re-run, so rows are keyed UNIQUE (source, normalized_sku) and upserted.
-- Counts / last_seen / sample refresh each run; first_seen is preserved.
--
-- status:
--   open     — needs attention (add the pattern, or a bundle definition)
--   resolved — dealt with; if the SKU is seen skipped again it re-opens
--   ignored  — deliberately never imported (rulers, kits, notions, the ES-PRE
--              pre-order placeholder). Sticky: stays ignored on later runs.

CREATE TABLE IF NOT EXISTS import_audit (
    issue_id        INTEGER       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          CHAR(1)       NOT NULL REFERENCES sales_channels(id),
    normalized_sku  VARCHAR(64)   NOT NULL,
    source_sku      VARCHAR(64),
    description     VARCHAR(255),
    occurrences     INTEGER       NOT NULL DEFAULT 0,
    total_quantity  INTEGER       NOT NULL DEFAULT 0,
    total_value     NUMERIC(10,2) NOT NULL DEFAULT 0,
    sample_order    VARCHAR(64),
    first_seen      TIMESTAMP     NOT NULL DEFAULT now(),
    last_seen       TIMESTAMP     NOT NULL DEFAULT now(),
    status          VARCHAR(10)   NOT NULL DEFAULT 'open',
    note            TEXT,

    CONSTRAINT uq_import_audit_source_sku UNIQUE (source, normalized_sku),
    CONSTRAINT ck_import_audit_status CHECK (status IN ('open', 'resolved', 'ignored'))
);

CREATE INDEX IF NOT EXISTS idx_import_audit_status ON import_audit (status);
