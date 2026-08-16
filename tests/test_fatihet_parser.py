import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import fatihet_parser

REPO_ROOT = Path(__file__).parent.parent
SAMPLE = REPO_ROOT / "fatihet" / "RG2606760.pdf"

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(),
    reason="Sample Fatihet invoice not present (fatihet/ is gitignored business data)",
)


def test_parses_header_fields():
    result = fatihet_parser.parse_fatihet_invoice_pdf(SAMPLE)

    assert result["invoice_number"] == "2606760"
    assert result["invoice_date"] == "2026-08-15"
    assert result["delivery_date"] == "2026-08-15"


def test_parses_all_line_items():
    result = fatihet_parser.parse_fatihet_invoice_pdf(SAMPLE)

    assert len(result["items"]) == 4
    first = result["items"][0]
    assert first["product_number"] == "1120"
    assert first["unit"] == "Kg"
    assert first["quantity"] == 40.0
    assert first["unit_price"] == 2.5
    assert first["line_total"] == 100.0

    delivery_fee = result["items"][3]
    assert delivery_fee["description"] == "Fahrtkostenpauschale"
    assert delivery_fee["unit"] == "Stück"
    assert delivery_fee["line_total"] == 30.0


def test_parses_totals():
    result = fatihet_parser.parse_fatihet_invoice_pdf(SAMPLE)

    assert result["netto_total"] == 278.0
    assert result["vat_total"] == 23.06
    assert result["brutto_total"] == 301.06


def test_line_items_reconcile_with_netto_total():
    result = fatihet_parser.parse_fatihet_invoice_pdf(SAMPLE)

    assert round(sum(i["line_total"] for i in result["items"]), 2) == result["netto_total"]
