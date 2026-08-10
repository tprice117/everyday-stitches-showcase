"""Translate an external order payload into the shape our database expects.

Kept deliberately free of network/DB code so it can be unit-tested on a saved
payload. `to_order` returns a plain dict; shared.db turns that into rows.
"""
import datetime
from decimal import Decimal, InvalidOperation

from shared.sku import normalize_sku, detect_physical
from shared.bundles import expand_bundle


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _to_date(value):
    """Parse a WooCommerce ISO timestamp ('2024-05-01T14:30:00') to a date."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _money(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _clip(value, length):
    return (value or "").strip()[:length] or None


def order_channel(wc_order, default="R"):
    """Retail vs wholesale for one WooCommerce order, from the WWPP plugin's
    `_wwpp_order_type` meta: 'wholesale' -> 'W', anything else (or missing) ->
    the default. Retail and wholesale share one store/API, so the channel is a
    per-order property, not a separate credential set."""
    for m in wc_order.get("meta_data", []):
        if m.get("key") == "_wwpp_order_type":
            return "W" if str(m.get("value") or "").lower() == "wholesale" else "R"
    return default


def to_order(wc_order, channel="R", bundle_maps=None):
    """Map a WooCommerce order dict to our order shape.

    channel: the DEFAULT sales_channels id, used only when the order carries no
    WWPP order-type meta. The order's real channel ('R' or 'W') is detected
    per-order by order_channel().
    bundle_maps: {source: {bundle_sku: [components]}} from
    shared.bundles.load_bundles_by_source; the order's own source picks the map,
    so a wholesale order uses wholesale bundle definitions. Empty/None = no
    expansion, so this stays a pure function (tests pass a dict, no DB).
    """
    source = order_channel(wc_order, default=channel)
    bundle_map = (bundle_maps or {}).get(source, {})
    billing = wc_order.get("billing") or {}
    first = (billing.get("first_name") or "").strip()
    last = (billing.get("last_name") or "").strip()
    person = f"{first} {last}".strip()
    company = (billing.get("company") or "").strip()

    customer = {
        # Prefer the company name; fall back to the person's name.
        "name": _clip(company or person or "Web customer", 40),
        "contact_name": _clip(person, 50),
        "email": _clip(billing.get("email"), 50),
        "street_addr": _clip(billing.get("address_1"), 50),
        "city": _clip(billing.get("city"), 35),
        "state": _clip(billing.get("state"), 2),
        "zip_code": _clip(billing.get("postcode"), 10),
    }

    paid = bool(wc_order.get("date_paid")) or \
        wc_order.get("status") in ("processing", "completed")

    line_items = []
    for li in wc_order.get("line_items", []):
        raw_sku = (li.get("sku") or "").strip()
        # Store SKUs carry (PP)/(PDF)/*/space noise; normalize to the canonical
        # ES-NNN-LLL the patterns table uses, or matching silently fails.
        sku = normalize_sku(raw_sku)
        quantity = int(li.get("quantity") or 0)
        total = _money(li.get("total"))

        # A fixed bundle expands into its component patterns, so revenue lands on
        # the real patterns instead of a synthetic bundle SKU that matches nothing.
        expanded = expand_bundle(bundle_map, sku, quantity)
        if expanded is not None:
            # Trace each component back to the bundle SKU as submitted.
            for item in expanded:
                item["source_sku"] = raw_sku or None
            line_items.extend(expanded)
            continue

        # The suffix that normalize_sku strips is exactly the physical/digital
        # signal: (PP) -> physical, (PDF) -> digital. Default physical when absent.
        physical = detect_physical(raw_sku)
        line_items.append({
            "sku": sku or "",
            "name": li.get("name") or "",
            "quantity": quantity,
            "line_total": total,
            "physical": True if physical is None else physical,
            # The raw SKU exactly as submitted, kept for traceability.
            "source_sku": raw_sku or None,
        })

    return {
        "external_id": str(wc_order.get("id")),
        "source": source,
        "customers_po_number": _clip(str(wc_order.get("number") or ""), 20),
        "date_ordered": _to_date(wc_order.get("date_created")),
        "date_shipped": _to_date(wc_order.get("date_completed")),
        "paid": paid,
        "customer": customer,
        "line_items": line_items,
    }
