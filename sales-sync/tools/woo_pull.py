"""Pull real orders from WooCommerce — dry run by default.

Dry run fetches from the live store, maps each order, and reports exactly what
would land in RDS, without writing anything. Add --commit to actually upsert.

    cd ESAppProject/sales-sync
    python -m tools.woo_pull --check                    # credentials only
    python -m tools.woo_pull --since 2026-06-01         # dry run, show the plan
    python -m tools.woo_pull --since 2026-06-01 --limit 5 --verbose
    python -m tools.woo_pull --since 2026-06-01 --commit

Credentials come from ESAppProject/.env (gitignored), same file as DB_*:
    SALES_SYNC_WOO_SITE_URL, SALES_SYNC_WOO_CONSUMER_KEY,
    SALES_SYNC_WOO_CONSUMER_SECRET, SALES_SYNC_WOO_CHANNEL

The watermark is NOT advanced here — this tool is for manual/first runs, so
re-running the same window is always safe. The Lambda handler owns the watermark.
"""
import argparse
import os
import sys
from collections import Counter
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv   # noqa: E402
load_dotenv(os.path.join(ROOT, "..", ".env"))

from shared.params import get_param                    # noqa: E402
from shared.mapping import to_order                    # noqa: E402
from functions.woo_sync.handler import fetch_woo_orders  # noqa: E402


def normalize_since(value):
    """WooCommerce's modified_after wants a full ISO8601 datetime; accept a bare
    date (YYYY-MM-DD) for convenience and widen it to the start of that day."""
    value = value.strip()
    if len(value) == 10:   # just a date
        return value + "T00:00:00"
    return value


def load_config():
    cfg = {
        "site": get_param("/sales-sync/woo/site_url"),
        "key": get_param("/sales-sync/woo/consumer_key"),
        "secret": get_param("/sales-sync/woo/consumer_secret"),
        "channel": get_param("/sales-sync/woo/channel", default="R"),
    }
    missing = [k for k in ("site", "key", "secret") if not cfg[k]]
    if missing:
        sys.exit(
            "Missing WooCommerce config: %s\n"
            "Add these to ESAppProject/.env (it is gitignored):\n"
            "  SALES_SYNC_WOO_SITE_URL=https://yourstore.com\n"
            "  SALES_SYNC_WOO_CONSUMER_KEY=ck_...\n"
            "  SALES_SYNC_WOO_CONSUMER_SECRET=cs_...\n"
            % ", ".join(missing)
        )
    return cfg


def check(cfg):
    """Confirm the keys work and the REST route is reachable, without pulling
    the whole order history."""
    import requests
    url = f"{cfg['site'].rstrip('/')}/wp-json/wc/v3/orders"
    resp = requests.get(url, params={"per_page": 1}, auth=(cfg["key"], cfg["secret"]), timeout=30)
    print("GET", url, "->", resp.status_code)
    if resp.status_code == 401:
        sys.exit("401 Unauthorized — key/secret wrong, or the key lacks Read access.\n"
                 "Also note WooCommerce only accepts basic auth over HTTPS.")
    if resp.status_code == 404:
        sys.exit("404 — the wc/v3 route isn't there. Check the site URL and that\n"
                 "the WooCommerce REST API + pretty permalinks are enabled.")
    resp.raise_for_status()
    total = resp.headers.get("X-WP-Total")
    print("Credentials OK. Orders visible to this key:", total or "unknown")


def summarize(orders, cfg, verbose):
    """Report what the mapped orders would do to the database."""
    skus = Counter()
    unmatched_names = Counter()
    emails = set()
    revenue = Decimal("0")
    no_sku = 0

    for o in orders:
        emails.add((o["customer"]["email"] or "").lower())
        for li in o["line_items"]:
            revenue += li["line_total"]
            if li["sku"]:
                skus[li["sku"]] += li["quantity"]
            else:
                no_sku += 1
                unmatched_names[li["name"]] += li["quantity"]

    by_source = Counter(o["source"] for o in orders)
    print("\nOrders fetched      :", len(orders))
    print("Distinct emails     :", len([e for e in emails if e]))
    print("Line items          :", sum(len(o["line_items"]) for o in orders))
    print("Line-item revenue   : $%s" % revenue)
    print("Channel split       :", ", ".join("%s=%d" % (s, n) for s, n in sorted(by_source.items())))
    if no_sku:
        print("Line items with NO SKU:", no_sku, "- these can never match a pattern:")
        for name, qty in unmatched_names.most_common(10):
            print("    x%-4d %s" % (qty, name[:60]))

    if verbose:
        print("\nPer order:")
        for o in orders:
            print("  #%s  [%s]  %s  %s  paid=%s  %d items"
                  % (o["external_id"], o["source"], o["date_ordered"],
                     o["customer"]["email"], o["paid"], len(o["line_items"])))
    return skus


def check_skus(skus):
    """Resolve the pulled SKUs against the patterns table so unmatched ones are
    known BEFORE anything is written."""
    from shared.db import connect
    conn = connect()
    try:
        cur = conn.cursor()
        matched, unmatched = [], []
        for sku, qty in skus.items():
            cur.execute("SELECT pattern_id FROM products WHERE upper(sku)=upper(%s) LIMIT 1", (sku,))
            (matched if cur.fetchone() else unmatched).append((sku, qty))
        print("\nSKUs matched to a pattern  :", len(matched))
        print("SKUs with NO pattern       :", len(unmatched))
        for sku, qty in sorted(unmatched, key=lambda x: -x[1]):
            print("    %-20s x%d  (line items will be SKIPPED)" % (sku, qty))
        return unmatched
    finally:
        conn.close()


def commit(orders):
    from shared.db import connect, upsert_order
    from shared.audit import record_unmatched
    conn = connect()
    imported, unmatched, details = 0, [], []
    try:
        for o in orders:
            result = upsert_order(conn, o)
            unmatched.extend(result["unmatched_skus"])
            details.extend(result["unmatched_details"])
            imported += 1
        # Persist the skipped SKUs as an admin worklist, inside this transaction.
        logged = record_unmatched(conn, details)
        conn.commit()
        print("\nCOMMITTED. Orders upserted:", imported)
        print("Skipped line items (unmatched SKU):", sorted(set(s for s in unmatched if s)))
        print("Logged to import_audit (distinct SKUs):", logged)
    except Exception:
        conn.rollback()
        print("\nFAILED — rolled back, nothing written.")
        raise
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2020-01-01T00:00:00",
                    help="only orders modified after this ISO timestamp")
    ap.add_argument("--limit", type=int, help="stop after N orders (for a quick look)")
    ap.add_argument("--check", action="store_true", help="test credentials and exit")
    ap.add_argument("--commit", action="store_true", help="actually write to RDS")
    ap.add_argument("--verbose", action="store_true", help="list every order")
    args = ap.parse_args()

    cfg = load_config()
    print("Store:", cfg["site"])

    if args.check:
        check(cfg)
        return

    since = normalize_since(args.since)
    print("Fetching orders modified after", since, "...")
    raw = []
    for wc_order in fetch_woo_orders(cfg["site"], cfg["key"], cfg["secret"], since):
        raw.append(wc_order)
        if args.limit and len(raw) >= args.limit:
            break

    # Bundle definitions live in the DB now, so load them (needs a connection)
    # before mapping, so bundle line items expand into their component patterns.
    # Loaded per source so wholesale orders use wholesale bundle definitions.
    from shared.db import connect
    from shared.bundles import load_bundles_by_source
    conn = connect()
    try:
        bundle_maps = load_bundles_by_source(conn.cursor())
    finally:
        conn.close()
    print("Bundle definitions loaded:",
          ", ".join("%s=%d" % (s, len(m)) for s, m in sorted(bundle_maps.items())) or "none")

    orders = [to_order(o, channel=cfg["channel"], bundle_maps=bundle_maps) for o in raw]
    if not orders:
        print("No orders in that window.")
        return

    skus = summarize(orders, cfg, args.verbose)
    check_skus(skus)

    if args.commit:
        commit(orders)
    else:
        print("\nDRY RUN — nothing written. Re-run with --commit to import.")


if __name__ == "__main__":
    main()
