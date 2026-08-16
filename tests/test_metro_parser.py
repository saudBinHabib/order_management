import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import metro_parser

REPO_ROOT = Path(__file__).parent.parent
SAMPLE = REPO_ROOT / "metro" / "75_502_529930_20260813173708_invoice_copy_main.pdf"

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(),
    reason="Sample Metro invoice not present (metro/ is gitignored business data)",
)

# Sum of the three "Mengenrabatt" discount lines on the sample invoice (not captured as
# line items — see metro_parser docstring), needed to reconcile items vs. the netto total.
DISCOUNT_TOTAL = 119.04 + 14.40 + 4.00


def test_parses_header_fields():
    result = metro_parser.parse_metro_invoice_pdf(SAMPLE)

    assert result["invoice_number"] == "13.08.2026/075/0/0/0502/047659"
    assert result["invoice_date"] == "2026-08-13"
    assert result["delivery_date"] == "2026-08-13"


def test_parses_totals_reliably_despite_dense_layout():
    result = metro_parser.parse_metro_invoice_pdf(SAMPLE)

    assert result["netto_total"] == 464.73
    assert result["vat_total"] == 88.30
    assert result["brutto_total"] == 553.03


def test_parses_expected_item_count():
    result = metro_parser.parse_metro_invoice_pdf(SAMPLE)

    # 4 standalone Nonfood items + 12 beverage items each with a "+" deposit sub-line
    assert len(result["items"]) == 4 + 12 * 2


def test_deposit_sub_line_is_captured_as_its_own_item():
    result = metro_parser.parse_metro_invoice_pdf(SAMPLE)

    deposit_lines = [i for i in result["items"] if "LEERGUT" in i["description"]]
    assert len(deposit_lines) == 12


def test_line_items_reconcile_with_netto_total_after_discounts():
    result = metro_parser.parse_metro_invoice_pdf(SAMPLE)

    raw_sum = sum(i["line_total"] for i in result["items"])
    assert round(raw_sum - DISCOUNT_TOTAL, 2) == result["netto_total"]
