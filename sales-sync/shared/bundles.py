"""Bundle expansion: turn a bundle SKU sold as one line into line items against
the real component patterns, so revenue attributes to the patterns that actually
sold instead of a synthetic bundle SKU.

Definitions live in the `bundles` table (see migrations/004_create_bundles.sql),
so the business can edit them without a code change. `load_bundles` reads that
table once per run into an in-memory map; `expand_bundle` is pure so mapping.py
stays testable — tests pass a map dict directly, no DB.

Only bundles whose components are all real patterns can live here (item_sku is a
FK to patterns). "Pick any N of your choice" bundles can't — what the customer
chose is per-order data, not a constant — so they stay logged as unmatched.
"""


def load_bundles(cur, source):
    """Read bundle definitions for one sales channel into the map expand_bundle
    expects: {bundle_sku: [ {item_sku, alt_price, physical, description}, ... ]}.
    `cur` is a DB cursor; `source` is a sales_channels id (e.g. 'R')."""
    cur.execute(
        "SELECT bundle_sku, item_sku, alt_price, physical, description "
        "FROM bundles WHERE source = %s",
        (source,),
    )
    bundle_map = {}
    for bundle_sku, item_sku, alt_price, physical, description in cur.fetchall():
        bundle_map.setdefault(bundle_sku, []).append({
            "item_sku": item_sku,
            "alt_price": alt_price,
            "physical": physical,
            "description": description,
        })
    return bundle_map


def load_bundles_by_source(cur):
    """Load ALL bundle definitions grouped by channel, for a mixed-channel import
    where each order's source (R/W) is detected per-order. Returns
    {source: {bundle_sku: [ {item_sku, alt_price, physical, description}, ... ]}}."""
    cur.execute(
        "SELECT source, bundle_sku, item_sku, alt_price, physical, description FROM bundles"
    )
    out = {}
    for source, bundle_sku, item_sku, alt_price, physical, description in cur.fetchall():
        out.setdefault(source, {}).setdefault(bundle_sku, []).append({
            "item_sku": item_sku,
            "alt_price": alt_price,
            "physical": physical,
            "description": description,
        })
    return out


def is_bundle(bundle_map, sku):
    return sku in bundle_map


def expand_bundle(bundle_map, sku, quantity):
    """If `sku` is a bundle in `bundle_map`, return a list of component line-item
    dicts (same shape mapping.to_order emits); otherwise return None.

    Each component's line_total is its stored per-unit alt_price times the
    bundle's quantity, so buying 2 bundles yields quantity 2 of each pattern with
    a doubled total. Unlike an even split of the order's line total, this is a
    fixed catalog allocation and does not follow per-order discounts.
    """
    components = bundle_map.get(sku)
    if not components:
        return None
    return [
        {
            "sku": c["item_sku"],
            "name": c["description"] or c["item_sku"],
            "quantity": quantity,
            "line_total": (c["alt_price"] or 0) * quantity,
            "physical": c["physical"],
        }
        for c in components
    ]
