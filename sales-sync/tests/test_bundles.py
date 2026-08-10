"""Bundle expansion — pure logic, no network or DB (a bundle_map is injected).

    python -m tests.test_bundles      (from the sales-sync/ directory)
"""
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from shared.bundles import expand_bundle, is_bundle   # noqa: E402
from shared.mapping import to_order                    # noqa: E402

# A sample of what load_bundles() returns from the `bundles` table: the Notions
# PDF Bundle, five patterns at $16 each, all digital.
SAMPLE_MAP = {
    "ES-FPP-BNL": [
        {"item_sku": "ES-548-SCI", "alt_price": Decimal("16.00"), "physical": False, "description": "Sadie the Scissors"},
        {"item_sku": "ES-549-CUT", "alt_price": Decimal("16.00"), "physical": False, "description": "Callie the Cutter"},
        {"item_sku": "ES-545-RIP", "alt_price": Decimal("16.00"), "physical": False, "description": "Rita the Ripper"},
        {"item_sku": "ES-547-BOB", "alt_price": Decimal("16.00"), "physical": False, "description": "Bonnie the Bobbin"},
        {"item_sku": "ES-550-PIN", "alt_price": Decimal("16.00"), "physical": False, "description": "Pippa the Pin"},
    ],
}


def test_known_bundle_expands():
    items = expand_bundle(SAMPLE_MAP, "ES-FPP-BNL", quantity=1)
    assert [i["sku"] for i in items] == [c["item_sku"] for c in SAMPLE_MAP["ES-FPP-BNL"]]
    assert all(i["physical"] is False for i in items)
    assert all(i["quantity"] == 1 for i in items)
    assert all(i["line_total"] == Decimal("16.00") for i in items)
    assert sum((i["line_total"] for i in items), Decimal("0")) == Decimal("80.00")


def test_quantity_multiplies_alt_price():
    # 2 bundles -> quantity 2 of each component, line_total = alt_price * 2.
    items = expand_bundle(SAMPLE_MAP, "ES-FPP-BNL", quantity=2)
    assert all(i["quantity"] == 2 for i in items)
    assert all(i["line_total"] == Decimal("32.00") for i in items)
    assert sum((i["line_total"] for i in items), Decimal("0")) == Decimal("160.00")


def test_non_bundle_returns_none():
    assert expand_bundle(SAMPLE_MAP, "ES-103-MZY", 1) is None
    assert expand_bundle({}, "ES-FPP-BNL", 1) is None       # empty map = no expansion
    assert is_bundle(SAMPLE_MAP, "ES-FPP-BNL") is True
    assert is_bundle(SAMPLE_MAP, "ES-103-MZY") is False


def test_component_name_falls_back_to_sku():
    m = {"ES-X-BND": [{"item_sku": "ES-101-BBL", "alt_price": Decimal("5"), "physical": True, "description": None}]}
    (item,) = expand_bundle(m, "ES-X-BND", 1)
    assert item["name"] == "ES-101-BBL"


def test_bundle_expands_inside_to_order():
    wc = {
        "id": 999, "number": "999",
        "billing": {"email": "buyer@example.com", "first_name": "B", "last_name": "Uyer"},
        "line_items": [{"sku": "ES-FPP-BNL", "name": "Notions PDF Bundle",
                        "quantity": 1, "total": "80.00"}],
    }
    order = to_order(wc, channel="R", bundle_maps={"R": SAMPLE_MAP})
    assert len(order["line_items"]) == 5
    assert {li["sku"] for li in order["line_items"]} == {c["item_sku"] for c in SAMPLE_MAP["ES-FPP-BNL"]}
    assert all(li["physical"] is False for li in order["line_items"])
    # Every component traces back to the bundle SKU as submitted.
    assert all(li["source_sku"] == "ES-FPP-BNL" for li in order["line_items"])


def test_no_bundle_map_leaves_line_as_is():
    # Without a bundle_map, the bundle SKU is treated as an ordinary line.
    wc = {
        "id": 1000, "number": "1000",
        "billing": {"email": "b@e.com"},
        "line_items": [{"sku": "ES-FPP-BNL", "name": "Notions PDF Bundle", "quantity": 1, "total": "80.00"}],
    }
    order = to_order(wc, channel="R")   # no bundle_map
    assert len(order["line_items"]) == 1
    assert order["line_items"][0]["sku"] == "ES-FPP-BNL"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nRESULT: PASS (%d)" % len(tests))


if __name__ == "__main__":
    main()
