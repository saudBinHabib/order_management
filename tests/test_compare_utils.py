import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import compare_utils


def _order_item(number="1001", name="Pommes 2.5kg", qty=10.0, price=2.50, vat=7.0):
    total = round(qty * price, 2)
    return {
        "product_number": number, "product_name": name, "unit": "Karton",
        "quantity": qty, "unit_price": price, "vat_percent": vat, "line_total": total,
    }


def _invoice_item(number="1001", name="Pommes 2.5kg", qty=10.0, price=2.50, vat=7.0):
    total = round(qty * price, 2)
    return {
        "product_number": number, "product_name": name, "unit": "Karton",
        "quantity": qty, "unit_price": price, "vat_percent": vat, "line_total": total,
    }


def test_matching_item_is_ok():
    order_items = [_order_item()]
    invoice_items = [_invoice_item()]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert len(result) == 1
    assert result[0]["status"] == "✅ OK"
    assert result[0]["diff"] == 0.0


def test_quantity_mismatch_is_flagged():
    order_items = [_order_item(qty=10.0)]
    invoice_items = [_invoice_item(qty=12.0)]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "⚠️ Quantity mismatch"
    assert "10" in result[0]["notes"] and "12" in result[0]["notes"]
    assert result[0]["diff"] == round(12.0 * 2.50 - 10.0 * 2.50, 2)


def test_price_mismatch_is_flagged():
    order_items = [_order_item(price=2.50)]
    invoice_items = [_invoice_item(price=2.90)]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "🚨 Price mismatch"


def test_vat_mismatch_is_flagged_when_price_and_qty_match():
    order_items = [_order_item(vat=7.0)]
    invoice_items = [_invoice_item(vat=19.0)]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "⚠️ VAT mismatch"


def test_price_mismatch_takes_priority_over_vat_mismatch():
    order_items = [_order_item(price=2.50, vat=7.0)]
    invoice_items = [_invoice_item(price=2.90, vat=19.0)]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "🚨 Price mismatch"


def test_item_ordered_but_missing_from_invoice():
    order_items = [_order_item(number="1002", name="Ketchup 5L", qty=2.0, price=8.0)]
    invoice_items = []

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "❌ Missing from invoice"
    assert result[0]["invoiced_qty"] is None
    assert result[0]["diff"] == -16.0


def test_item_billed_but_not_ordered():
    order_items = []
    invoice_items = [_invoice_item(number="1003", name="Mayo 5L", qty=1.0, price=9.0)]

    result = compare_utils.compare_order_to_invoice(order_items, invoice_items)

    assert result[0]["status"] == "⚠️ Not ordered / unexpected charge"
    assert result[0]["ordered_qty"] is None
    assert result[0]["diff"] == 9.0


def test_build_dispute_message_lists_only_flagged_items():
    order_data = {"order_id": 42, "order_date": "2026-08-01", "items": [_order_item()]}
    invoice_data = {
        "invoice_number": "RG9999", "invoice_date": "2026-08-10",
        "netto_total": 30.0, "brutto_total": 32.10,
    }
    comparison = [
        {**_order_item(), "status": "✅ OK", "notes": "Matches order.",
         "ordered_qty": 10.0, "invoiced_qty": 10.0, "ordered_price": 2.50, "invoiced_price": 2.50,
         "ordered_total": 25.0, "invoiced_total": 25.0, "diff": 0.0},
        {"product_number": "1002", "product_name": "Ketchup 5L", "status": "❌ Missing from invoice",
         "notes": "Ordered but never billed — vendor may still owe this item.",
         "ordered_qty": 2.0, "invoiced_qty": None, "ordered_price": 8.0, "invoiced_price": None,
         "ordered_total": 16.0, "invoiced_total": None, "diff": -16.0},
    ]

    message = compare_utils.build_dispute_message(invoice_data, order_data, comparison)

    assert "RG9999" in message
    assert "Ketchup 5L" in message
    assert "Pommes 2.5kg" not in message  # the OK row must not appear in the dispute list
    assert "Netto €30.00" in message
    assert "#42" in message
