"""Mapping unit test — no network, no database. Run:

    python -m tests.test_mapping        (from the sales-sync/ directory)
"""
import datetime
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.mapping import to_order, order_channel   # noqa: E402


def test_channel_detection():
    """Retail vs wholesale comes from the WWPP _wwpp_order_type meta per order."""
    def wc(order_type):
        meta = [{"key": "_wwpp_order_type", "value": order_type}] if order_type is not None else []
        return {"id": 1, "number": "1", "billing": {"email": "x@y.com"},
                "meta_data": meta, "line_items": []}
    assert order_channel(wc("wholesale")) == "W"
    assert order_channel(wc("retail")) == "R"
    assert order_channel(wc(None)) == "R"                 # no meta -> default
    assert order_channel(wc(None), default="W") == "W"    # fallback honoured
    # to_order files the whole order under the detected source.
    assert to_order(wc("wholesale"))["source"] == "W"
    assert to_order(wc("retail"))["source"] == "R"
    print("channel detection OK")


def main():
    test_channel_detection()
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "sample_woo_order.json")) as fh:
        wc_order = json.load(fh)

    order = to_order(wc_order, channel="R")

    assert order["external_id"] == "80412", order["external_id"]
    assert order["source"] == "R"
    assert order["customers_po_number"] == "80412"
    assert order["date_ordered"] == datetime.date(2026, 6, 14), order["date_ordered"]
    assert order["date_shipped"] == datetime.date(2026, 6, 15), order["date_shipped"]
    assert order["paid"] is True

    cust = order["customer"]
    assert cust["name"] == "Dana Whitfield"          # no company → person name
    assert cust["email"] == "dana.whitfield@example.com"
    assert cust["state"] == "NC"

    assert len(order["line_items"]) == 3
    li0 = order["line_items"][0]
    assert li0["sku"] == "ES-103-MZY"
    assert li0["quantity"] == 3
    assert li0["line_total"] == Decimal("25.20"), li0["line_total"]
    assert li0["physical"] is True
    assert li0["source_sku"] == "ES-103-MZY", li0["source_sku"]   # raw SKU retained

    # totals sanity: line items sum to the order total
    total = sum(li["line_total"] for li in order["line_items"])
    assert total == Decimal("46.20"), total

    print("mapping OK:")
    print("  external_id :", order["external_id"])
    print("  customer    :", cust["name"], "<%s>" % cust["email"])
    print("  ordered     :", order["date_ordered"], "| shipped:", order["date_shipped"], "| paid:", order["paid"])
    print("  line items  :", [(li["sku"], li["quantity"], str(li["line_total"])) for li in order["line_items"]])
    print("  order total :", total)


if __name__ == "__main__":
    main()
