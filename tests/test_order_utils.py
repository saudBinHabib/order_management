import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import order_utils


def test_compute_totals_single_item():
    items = [{"quantity": 10.0, "unit_price": 2.50, "vat_percent": 7.0}]

    netto, vat, brutto = order_utils.compute_totals(items)

    assert netto == 25.0
    assert vat == 1.75
    assert brutto == 26.75


def test_compute_totals_multiple_items_with_different_vat_rates():
    items = [
        {"quantity": 4.0, "unit_price": 2.09, "vat_percent": 19.0},
        {"quantity": 2.0, "unit_price": 9.69, "vat_percent": 7.0},
    ]

    netto, vat, brutto = order_utils.compute_totals(items)

    expected_netto = round(4.0 * 2.09 + 2.0 * 9.69, 2)
    expected_vat = round(4.0 * 2.09 * 0.19 + 2.0 * 9.69 * 0.07, 2)
    assert netto == expected_netto
    assert vat == expected_vat
    assert brutto == round(expected_netto + expected_vat, 2)


def test_compute_totals_empty_list():
    netto, vat, brutto = order_utils.compute_totals([])

    assert (netto, vat, brutto) == (0.0, 0.0, 0.0)


def test_compute_totals_zero_vat():
    items = [{"quantity": 3.0, "unit_price": 5.0, "vat_percent": 0.0}]

    netto, vat, brutto = order_utils.compute_totals(items)

    assert (netto, vat, brutto) == (15.0, 0.0, 15.0)
