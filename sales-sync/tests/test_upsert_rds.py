"""End-to-end upsert test against the real RDS, fully rolled back.

Postgres has transactional DDL, so this applies the schema change, runs the
upsert twice, verifies the results, then ROLLS BACK — RDS is left untouched.

    python -m tests.test_upsert_rds        (from the sales-sync/ directory)

Reads DB_* credentials from ESAppProject/.env.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv   # noqa: E402
# sales-sync lives inside the Django project, so its .env is one level up.
load_dotenv(os.path.join(ROOT, "..", ".env"))

from shared.db import connect, upsert_order   # noqa: E402
from shared.mapping import to_order            # noqa: E402


def main():
    with open(os.path.join(HERE, "sample_woo_order.json")) as fh:
        order = to_order(json.load(fh), channel="R")

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # Apply the migration inside the transaction (undone by rollback).
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_id VARCHAR(64)")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_source_external "
                    "ON orders (source, external_id)")

        # First import.
        r1 = upsert_order(conn, order)
        cur.execute("SELECT external_id, source, paid, date_shipped FROM orders WHERE order_id=%s", (r1["order_id"],))
        ext, src, paid, shipped = cur.fetchone()
        cur.execute("SELECT count(*) FROM line_items WHERE order_id=%s", (r1["order_id"],))
        li_count = cur.fetchone()[0]
        cur.execute("SELECT customer_name, contact_email FROM customers WHERE customer_id="
                    "(SELECT customer_id FROM orders WHERE order_id=%s)", (r1["order_id"],))
        cust = cur.fetchone()

        print("First import:")
        print("  order_id      :", r1["order_id"])
        print("  external_id   :", ext, "| source:", src, "| paid:", paid, "| shipped:", shipped)
        print("  line items    :", li_count, "(2 matched; ES-999-XXX has no pattern)")
        print("  unmatched SKUs:", r1["unmatched_skus"])
        print("  customer      :", cust[0], "<%s>" % cust[1])

        # Second import of the same order — must UPDATE, not duplicate.
        r2 = upsert_order(conn, order)
        cur.execute("SELECT count(*) FROM orders WHERE source='R' AND external_id=%s", (order["external_id"],))
        order_rows = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM line_items WHERE order_id=%s", (r2["order_id"],))
        li_count2 = cur.fetchone()[0]

        print("Second import (idempotency):")
        print("  same order_id :", r2["order_id"], "->", r1["order_id"] == r2["order_id"])
        print("  order rows    :", order_rows, "(must be 1 — no duplicate)")
        print("  line items    :", li_count2, "(must still be 2 — replaced, not appended)")

        ok = (r1["order_id"] == r2["order_id"] and order_rows == 1
              and li_count == 2 and li_count2 == 2 and r1["unmatched_skus"] == ["ES-999-XXX"])
        print("\nRESULT:", "PASS" if ok else "FAIL")
    finally:
        conn.rollback()
        conn.close()
        print("(transaction rolled back — RDS unchanged)")


if __name__ == "__main__":
    main()
