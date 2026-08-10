-- 005_line_items_source_sku.sql
-- Adds line_items.source_sku: the raw SKU exactly as the sales channel submitted
-- it (e.g. 'ES-548-CHF(PP)', 'ES-548-CHF(PDF)-1'), kept for traceability back to
-- the source line. The matched pattern_id + physical flag capture the *meaning*;
-- this preserves the *literal* string, including sub-format tags like (PP, FPP).
--
-- For a bundle line, every expanded component records the bundle's submitted SKU
-- (e.g. 'ES-FPP-BNL'), so a component can be traced back to the bundle it came from.
--
-- Additive and nullable: existing rows (manual entries and the earlier import)
-- keep source_sku = NULL. Re-running the idempotent WooCommerce import backfills
-- it for previously imported orders.

ALTER TABLE line_items
    ADD COLUMN IF NOT EXISTS source_sku VARCHAR(64);
