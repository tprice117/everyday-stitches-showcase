-- 002_add_kc_landmark_patterns.sql
-- Adds two KC Landmark Series patterns that exist in WooCommerce but were never
-- entered in the `patterns` table, so their sales currently can't match and get
-- skipped by the sync. Both are $28 paper-pieced patterns sold as Paper Pattern
-- (PP) and Downloadable PDF; the canonical SKU is the WooCommerce parent SKU.
--
--   ES-542-ROY  "KC Landmark Series: A Day at the K"  (created 2023)
--   ES-548-CHF  "KC Landmark Series: The Red Zone"    (created 2024)
--
-- pattern_id is GENERATED ALWAYS AS IDENTITY, so it is intentionally omitted and
-- the database assigns it. ON CONFLICT (sku) makes this safe to run more than
-- once (patterns.sku is unique).
--
-- >>> VALUES TO CONFIRM BEFORE APPLYING (could not be derived from WooCommerce):
--   * everyday_applique / applique / precuts / curve  -> all set FALSE.
--     These are paper-pieced patterns; adjust if any craft flag actually applies.
--   * inventory -> 0 (real paper-pattern stock unknown; set your on-hand count).
--   * ean       -> NULL (no barcode/GTIN in WooCommerce; fill if you have one).
--   * retail_price 28.00, year_introduced, current=TRUE taken from WooCommerce.

INSERT INTO patterns
    (pattern_name, year_introduced, sku, inventory,
     everyday_applique, applique, precuts, retail_price, ean, current, curve)
VALUES
    ('KC Landmark Series: A Day at the K', 2023, 'ES-542-ROY', 0,
     FALSE, FALSE, FALSE, 28.00, NULL, TRUE, FALSE),
    ('KC Landmark Series: The Red Zone',   2024, 'ES-548-CHF', 0,
     FALSE, FALSE, FALSE, 28.00, NULL, TRUE, FALSE)
ON CONFLICT (sku) DO NOTHING;
