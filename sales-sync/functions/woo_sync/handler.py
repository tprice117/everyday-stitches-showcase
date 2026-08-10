"""woo_sync — pull WooCommerce orders changed since the last run and upsert them
into RDS. Triggered monthly by EventBridge.

Config (SSM Parameter Store, or SALES_SYNC_WOO_* env vars locally):
    /sales-sync/woo/site_url          e.g. https://example-store.com
    /sales-sync/woo/consumer_key      WooCommerce REST API key
    /sales-sync/woo/consumer_secret   WooCommerce REST API secret
    /sales-sync/woo/channel           sales_channels id: 'R' (default) or 'W'
    /sales-sync/woo/watermark         ISO timestamp of the last successful run
"""
import logging

from shared.params import get_param, put_param
from shared.db import connect, upsert_order
from shared.bundles import load_bundles_by_source
from shared.audit import record_unmatched
from shared.mapping import to_order, now_iso

log = logging.getLogger()
log.setLevel(logging.INFO)

DEFAULT_WATERMARK = "2020-01-01T00:00:00"
PER_PAGE = 100


def fetch_woo_orders(site_url, consumer_key, consumer_secret, modified_after):
    """Yield WooCommerce orders modified since `modified_after`, page by page.
    Isolated here so the rest of the pipeline stays import-light and testable."""
    import requests   # provided by the Lambda layer

    page = 1
    while True:
        resp = requests.get(
            f"{site_url.rstrip('/')}/wp-json/wc/v3/orders",
            params={
                "modified_after": modified_after,
                "per_page": PER_PAGE,
                "page": page,
                "orderby": "modified",
                "order": "asc",
            },
            auth=(consumer_key, consumer_secret),
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return
        for order in batch:
            yield order
        if len(batch) < PER_PAGE:
            return
        page += 1


def handler(event, context):
    site = get_param("/sales-sync/woo/site_url")
    key = get_param("/sales-sync/woo/consumer_key")
    secret = get_param("/sales-sync/woo/consumer_secret")
    channel = get_param("/sales-sync/woo/channel", default="R")
    since = get_param("/sales-sync/woo/watermark", default=DEFAULT_WATERMARK)

    run_started = now_iso()
    imported, unmatched, details = 0, [], []
    conn = connect()
    try:
        bundle_maps = load_bundles_by_source(conn.cursor())
        for wc_order in fetch_woo_orders(site, key, secret, since):
            result = upsert_order(conn, to_order(wc_order, channel=channel, bundle_maps=bundle_maps))
            unmatched.extend(result["unmatched_skus"])
            details.extend(result["unmatched_details"])
            imported += 1
        # Persist skipped SKUs as an admin worklist, inside this transaction.
        record_unmatched(conn, details)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("woo_sync failed; rolled back, watermark not advanced")
        raise
    finally:
        conn.close()

    # Only advance the watermark after a clean, committed run.
    put_param("/sales-sync/woo/watermark", run_started)
    unmatched = sorted(set(s for s in unmatched if s))
    log.info("woo_sync imported=%d unmatched_skus=%s", imported, unmatched)
    return {"imported": imported, "unmatched_skus": unmatched}
