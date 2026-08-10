-- 004_create_bundles.sql
-- A lookup table that defines bundle products as sets of component patterns, so
-- the sync can expand a bundle SKU sold as one line into line items against the
-- real patterns. Replaces the hardcoded BUNDLES dict that lived in
-- shared/bundles.py (data the business can edit without a code deploy).
--
-- One row per (bundle, component, channel). A bundle sold under different SKUs
-- per channel, or split differently per channel, is expressed by the source key.
--
--   bundle_sku  the SKU the store sells the bundle under (e.g. ES-FPP-BNL)
--   item_sku    a component pattern's canonical SKU; FK to patterns(sku)
--   alt_price   per-UNIT price to record for that component; the expanded line
--               item's total = alt_price * quantity. Fixed catalog allocation
--               (does not follow per-order discounts), so the components should
--               normally sum to the bundle's list price.
--   description a human label for the component (shown in the row; not matched on)
--   source      sales_channels id the definition applies to (default 'R')
--   physical    FALSE for digital/PDF components (the Notions bundle is PDF)

CREATE TABLE IF NOT EXISTS bundles (
    bundle_sku   VARCHAR(20)   NOT NULL,
    item_sku     VARCHAR(20)   NOT NULL REFERENCES patterns(sku),
    alt_price    NUMERIC(10,2) NOT NULL,
    description  VARCHAR(255),
    source       CHAR(1)       NOT NULL DEFAULT 'R' REFERENCES sales_channels(id),
    physical     BOOLEAN       NOT NULL DEFAULT TRUE,
    PRIMARY KEY (bundle_sku, item_sku, source)
);

-- Seed: Notions PDF Bundle (ES-FPP-BNL) = five FPP patterns, $80 / 5 = $16 each,
-- all digital. Migrated from the old shared/bundles.py hardcoded definition.
INSERT INTO bundles (bundle_sku, item_sku, alt_price, description, source, physical)
VALUES
    ('ES-FPP-BNL', 'ES-548-SCI', 16.00, 'Sadie the Scissors', 'R', FALSE),
    ('ES-FPP-BNL', 'ES-549-CUT', 16.00, 'Callie the Cutter',  'R', FALSE),
    ('ES-FPP-BNL', 'ES-545-RIP', 16.00, 'Rita the Ripper',    'R', FALSE),
    ('ES-FPP-BNL', 'ES-547-BOB', 16.00, 'Bonnie the Bobbin',  'R', FALSE),
    ('ES-FPP-BNL', 'ES-550-PIN', 16.00, 'Pippa the Pin',      'R', FALSE)
ON CONFLICT (bundle_sku, item_sku, source) DO NOTHING;
