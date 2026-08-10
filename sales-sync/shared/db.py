"""Database access for the sync functions: a psycopg2 connection plus the
idempotent UPSERT that lands a mapped order into orders / line_items / customers.

Connection settings come from the same env vars the Django app uses (DB_HOST,
DB_NAME, DB_USER, DB_PASSWORD, DB_PORT).

The upsert does NOT commit — the caller owns the transaction, so a whole run can
commit once and tests can roll back.
"""
import os
import psycopg2


def connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        port=os.environ.get("DB_PORT", "5432"),
        connect_timeout=10,
    )


# Retail web customers pay full retail; wholesale channels don't. Imported orders
# store an explicit line_total, so this pct only matters for the revenue estimate
# used elsewhere — 100 keeps retail correct.
_CHANNEL_CUSTOMER = {
    "R": ("R", 100),   # WooCommerce - Retail
    "W": ("W", 50),    # WooCommerce - Wholesale (placeholder pct; refine per account)
    "P": ("R", 100),   # PayPal POS - Retail
    "E": ("R", 100),   # Etsy
}


def find_or_create_customer(cur, customer, channel):
    """Return a customer_id, matching an existing customer by email (case
    insensitive) or creating one. Email is the dedupe key across channels."""
    email = customer.get("email")
    if email:
        cur.execute(
            "SELECT customer_id, type FROM customers WHERE lower(contact_email) = lower(%s) LIMIT 1",
            (email,),
        )
        row = cur.fetchone()
        if row:
            cid, existing_type = row
            # A wholesale order marks the customer as a wholesale account. Upgrade
            # R -> W (wholesale identity wins), but never downgrade a W back to R
            # if they also place a retail order.
            if channel == "W" and existing_type != "W":
                wtype, wpct = _CHANNEL_CUSTOMER["W"]
                cur.execute(
                    "UPDATE customers SET type = %s, pct_of_retail = %s WHERE customer_id = %s",
                    (wtype, wpct, cid),
                )
            return cid

    ctype, pct = _CHANNEL_CUSTOMER.get(channel, ("R", 100))
    cur.execute(
        """
        INSERT INTO customers
            (customer_name, contact_name, contact_email, type, pct_of_retail,
             street_addr, city, state, zip_code)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING customer_id
        """,
        (customer.get("name"), customer.get("contact_name"), email or "",
         ctype, pct, customer.get("street_addr"), customer.get("city"),
         customer.get("state"), customer.get("zip_code")),
    )
    return cur.fetchone()[0]


def find_product(cur, sku):
    """Return (pattern_id, type) for a SKU, or None if it isn't catalogued.
    `type` is 'P' (pattern), 'R' (resale), or 'B' (bundle)."""
    if not sku:
        return None
    cur.execute("SELECT pattern_id, type FROM products WHERE upper(sku) = upper(%s) LIMIT 1", (sku,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def find_pattern_id(cur, sku):
    """Back-compat: the pattern_id for a SKU, or None. Ignores type — callers
    that care about routing should use find_product."""
    prod = find_product(cur, sku)
    return prod[0] if prod else None


def upsert_order(conn, order):
    """Insert or update one order + its line items. Idempotent: keyed on
    (source, external_id), so re-running a pull updates in place. Line items are
    replaced wholesale. Returns {'order_id', 'unmatched_skus'}."""
    cur = conn.cursor()
    customer_id = find_or_create_customer(cur, order["customer"], order["source"])

    cur.execute(
        """
        INSERT INTO orders
            (external_id, source, customers_po_number, customer_id,
             date_ordered, date_shipped, paid)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, external_id) DO UPDATE SET
            customers_po_number = EXCLUDED.customers_po_number,
            customer_id         = EXCLUDED.customer_id,
            date_ordered        = EXCLUDED.date_ordered,
            date_shipped        = EXCLUDED.date_shipped,
            paid                = EXCLUDED.paid
        RETURNING order_id
        """,
        (order["external_id"], order["source"], order["customers_po_number"],
         customer_id, order["date_ordered"], order["date_shipped"], order["paid"]),
    )
    order_id = cur.fetchone()[0]

    # Replace line items so a re-run reflects the current state of the order.
    cur.execute("DELETE FROM line_items WHERE order_id = %s", (order_id,))
    shipped = order["date_shipped"] is not None
    unmatched = []
    unmatched_details = []
    for li in order["line_items"]:
        prod = find_product(cur, li["sku"])
        # A type='B' row is a bundle: it should have been expanded during mapping.
        # If one reaches here it wasn't (e.g. no bundle definition for this
        # channel), so it must NOT become a line item pointing at itself — route
        # it to audit like an unknown SKU. Patterns ('P') and resale ('R') import.
        if prod is None or prod[1] == "B":
            # Matched no catalogued product (or an unexpanded bundle).
            # Keep the detail so the caller can log it to import_audit.
            unmatched.append(li["sku"] or li["name"])
            unmatched_details.append({
                "source": order["source"],
                "normalized_sku": li["sku"] or "",
                "source_sku": li.get("source_sku"),
                "description": li.get("name"),
                "quantity": li.get("quantity") or 0,
                "line_total": li.get("line_total") or 0,
                "external_id": order.get("external_id"),
            })
            continue
        pattern_id = prod[0]
        cur.execute(
            """
            INSERT INTO line_items
                (order_id, pattern_id, quantity, line_total, physical, shipped, source_sku)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (order_id, pattern_id, li["quantity"], li["line_total"],
             li["physical"], shipped, li.get("source_sku")),
        )

    cur.close()
    return {"order_id": order_id, "unmatched_skus": unmatched,
            "unmatched_details": unmatched_details}
