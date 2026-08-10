-- 003_add_missing_patterns.sql
-- Six more patterns that exist in WooCommerce but were never entered in the
-- `patterns` table, found by a Jan-2025-onward dry run of the WooCommerce sync
-- (their line items were being skipped). Name / retail_price / year taken from
-- WooCommerce; canonical SKU is the WooCommerce parent SKU.
--
--   ES-553-PLZ  "KC Landmark Series: The Plaza"                 $28  2025
--   ES-555-RKT  "KC Landmark Series: There's A Rocket Downtown?" $28 2025
--   ES-526-PSM  "Pressing Matters"                              $12  2021
--   ES-303-WDR  "Wander"                                        $12  2020
--   ES-532-STR  "Starburst"                                     $12  2022
--   ES-002-TOL  "Tree of Life Quilts"                           $23  2021
--
-- pattern_id is GENERATED ALWAYS AS IDENTITY, so it is omitted (DB assigns it).
-- ON CONFLICT (sku) makes this safe to re-run.
--
-- >>> VALUES TO CONFIRM (not derivable from WooCommerce), same as migration 002:
--   * everyday_applique / applique / precuts / curve -> all FALSE. Adjust per
--     pattern if any craft flag actually applies (e.g. applique/precuts/curve).
--   * inventory -> 0 (real stock unknown; set on-hand counts).
--   * ean       -> NULL (no barcode/GTIN in WooCommerce).

INSERT INTO patterns
    (pattern_name, year_introduced, sku, inventory,
     everyday_applique, applique, precuts, retail_price, ean, current, curve)
VALUES
    ('KC Landmark Series: The Plaza',                  2025, 'ES-553-PLZ', 0,
     FALSE, FALSE, FALSE, 28.00, NULL, TRUE, FALSE),
    ('KC Landmark Series: There''s A Rocket Downtown?', 2025, 'ES-555-RKT', 0,
     FALSE, FALSE, FALSE, 28.00, NULL, TRUE, FALSE),
    ('Pressing Matters',                               2021, 'ES-526-PSM', 0,
     FALSE, FALSE, FALSE, 12.00, NULL, TRUE, FALSE),
    ('Wander',                                         2020, 'ES-303-WDR', 0,
     FALSE, FALSE, FALSE, 12.00, NULL, TRUE, FALSE),
    ('Starburst',                                      2022, 'ES-532-STR', 0,
     FALSE, FALSE, FALSE, 12.00, NULL, TRUE, FALSE),
    ('Tree of Life Quilts',                            2021, 'ES-002-TOL', 0,
     FALSE, FALSE, FALSE, 23.00, NULL, TRUE, FALSE)
ON CONFLICT (sku) DO NOTHING;
