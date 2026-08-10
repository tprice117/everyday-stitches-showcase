"""Record SKUs the sync could not place into the `import_audit` worklist.

A line item is skipped when its SKU matches neither a bundle (expanded during
mapping) nor a pattern (looked up during the upsert). Those used to exist only in
the run's console output; this persists them so an admin can act on them.

Deliberately a worklist, not an append-only log: the import is idempotent and
re-run often, so entries are keyed on (source, normalized_sku) and upserted —
counts refresh each run instead of piling up duplicates.
"""
from decimal import Decimal


def record_unmatched(conn, details):
    """Aggregate skipped line items by (source, normalized_sku) and upsert them
    into import_audit. `details` is the concatenation of every upsert_order()
    call's "unmatched_details". Does NOT commit — the caller owns the
    transaction, so a failed run rolls the audit rows back too.

    Returns the number of distinct SKUs recorded."""
    if not details:
        return 0

    agg = {}
    for d in details:
        sku = (d.get("normalized_sku") or "").strip()
        if not sku:
            continue
        key = (d.get("source"), sku)
        entry = agg.setdefault(key, {
            "source_sku": d.get("source_sku"),
            "description": d.get("description"),
            "sample_order": d.get("external_id"),
            "occurrences": 0,
            "total_quantity": 0,
            "total_value": Decimal("0"),
        })
        entry["occurrences"] += 1
        entry["total_quantity"] += int(d.get("quantity") or 0)
        entry["total_value"] += Decimal(str(d.get("line_total") or 0))

    cur = conn.cursor()
    for (source, sku), e in agg.items():
        cur.execute(
            """
            INSERT INTO import_audit
                (source, normalized_sku, source_sku, description, occurrences,
                 total_quantity, total_value, sample_order, first_seen, last_seen, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now(), 'open')
            ON CONFLICT (source, normalized_sku) DO UPDATE SET
                source_sku     = EXCLUDED.source_sku,
                description    = EXCLUDED.description,
                occurrences    = EXCLUDED.occurrences,
                total_quantity = EXCLUDED.total_quantity,
                total_value    = EXCLUDED.total_value,
                sample_order   = EXCLUDED.sample_order,
                last_seen      = now(),
                -- 'ignored' is sticky (deliberately never imported); anything
                -- else seen skipped again is, by definition, still open.
                status = CASE WHEN import_audit.status = 'ignored'
                              THEN 'ignored' ELSE 'open' END
            """,
            (source, sku, e["source_sku"], e["description"], e["occurrences"],
             e["total_quantity"], e["total_value"], e["sample_order"]),
        )
    cur.close()
    return len(agg)
