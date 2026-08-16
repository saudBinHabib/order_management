import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import invoice_parser

REPO_ROOT = Path(__file__).parent.parent
SAMPLE = REPO_ROOT / "invoices" / "RG7206.pdf"

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(),
    reason="Sample FeinFood invoice not present (invoices/ is gitignored business data)",
)


def test_parses_header_fields():
    result = invoice_parser.parse_invoice_pdf(SAMPLE)

    assert result["invoice_number"] == "7206"
    assert result["invoice_date"] == "2026-07-02"
    assert result["customer_number"] == "10016"
    assert result["supplier_name"] == "FeinFood Express GmbH"


def test_parses_totals():
    result = invoice_parser.parse_invoice_pdf(SAMPLE)

    assert result["netto_total"] == 471.75
    assert result["brutto_total"] == 504.77


def test_parses_line_items():
    result = invoice_parser.parse_invoice_pdf(SAMPLE)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["product_number"] == "125"
    assert item["quantity"] == 37.0
    assert item["unit_price"] == 12.75
    assert item["vat_percent"] == 7.0
    assert item["line_total"] == 471.75


def test_line_items_reconcile_with_netto_total():
    result = invoice_parser.parse_invoice_pdf(SAMPLE)

    assert round(sum(i["line_total"] for i in result["items"]), 2) == result["netto_total"]
