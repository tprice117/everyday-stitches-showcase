-- 001_add_external_id.sql
-- Gives orders a stable external identifier so the sync Lambdas can UPSERT
-- idempotently: a re-run of a monthly pull updates the existing row instead of
-- creating a duplicate.
--
-- Safe/additive: the column is nullable, so every existing (manually entered)
-- order keeps external_id = NULL and is untouched. The unique index treats NULLs
-- as distinct, so those rows never collide with each other.
--
-- Apply against RDS, then reflect the change in db-mockup/schema_postgres.sql and
-- the Django Order model (managed=False → this DDL is the source of truth).

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS external_id VARCHAR(64);

-- Unique per channel: the same external id may legitimately appear under two
-- different sources, but never twice within one source. This index is what the
-- Lambdas' `ON CONFLICT (source, external_id)` upsert targets.
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_source_external
    ON orders (source, external_id);
