import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import invoice_ingest

REPO_ROOT = Path(__file__).parent.parent
INVOICES_DIR = REPO_ROOT / "invoices"


def _parsed_invoice(invoice_number="RG1", product_number="1001", unit_price=2.50):
    return {
        "invoice_number": invoice_number, "invoice_date": "2026-08-01",
        "customer_number": "10016", "customer_name": "Rooster Fried Chicken Mannheim",
        "supplier_name": "FeinFood Express GmbH",
        "netto_total": 25.0, "brutto_total": 26.75,
        "items": [
            {"pos": 1, "product_number": product_number, "product_name": "Pommes 2.5kg",
             "unit": "Karton", "unit_price": unit_price, "vat_percent": 7.0, "quantity": 10.0, "line_total": 25.0},
        ],
    }


def test_ingest_invoice_creates_invoice_and_product(test_db):
    ok, msg = invoice_ingest.ingest_invoice(_parsed_invoice(), mark_paid=False)

    assert ok is True
    status = db.load_invoices_by_status()
    assert len(status) == 1
    assert status.iloc[0]["is_paid"] == 0

    products, _ = db.load_data()
    assert len(products) == 1
    assert products.iloc[0]["product_number"] == "1001"


def test_ingest_invoice_mark_paid_true_sets_is_paid(test_db):
    invoice_ingest.ingest_invoice(_parsed_invoice(), mark_paid=True)

    status = db.load_invoices_by_status()
    assert status.iloc[0]["is_paid"] == 1


def test_ingest_invoice_duplicate_invoice_number_is_rejected(test_db):
    invoice_ingest.ingest_invoice(_parsed_invoice(invoice_number="RG1"), mark_paid=False)

    ok, msg = invoice_ingest.ingest_invoice(_parsed_invoice(invoice_number="RG1"), mark_paid=False)

    assert ok is False
    assert "already in the system" in msg


def test_ingest_invoice_reuses_existing_product_at_same_price(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)

    invoice_ingest.ingest_invoice(_parsed_invoice(invoice_number="RG1", unit_price=2.50), mark_paid=False)

    db.load_data.clear()
    products, _ = db.load_data()
    assert len(products) == 1  # no new product row — matched the existing price era


def test_ingest_invoice_creates_new_price_era_when_price_differs(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)

    invoice_ingest.ingest_invoice(_parsed_invoice(invoice_number="RG1", unit_price=2.75), mark_paid=False)

    db.load_data.clear()
    products, _ = db.load_data()
    assert len(products) == 2  # original + a new price era at 2.75


@pytest.mark.skipif(
    not (INVOICES_DIR / "RG7206.pdf").exists(),
    reason="Sample invoices/ folder not present (gitignored business data)",
)
def test_scan_finds_unimported_pdfs(test_db):
    results = invoice_ingest.list_unimported_pdfs(INVOICES_DIR)

    assert any(r["path"].name == "RG7206.pdf" and not r["already_imported"] for r in results if "parsed" in r)


@pytest.mark.skipif(
    not (INVOICES_DIR / "RG7206.pdf").exists(),
    reason="Sample invoices/ folder not present (gitignored business data)",
)
def test_scan_marks_already_imported_invoices(test_db):
    parsed = invoice_ingest.list_unimported_pdfs(INVOICES_DIR)
    rg7206 = next(r["parsed"] for r in parsed if "parsed" in r and r["path"].name == "RG7206.pdf")
    invoice_ingest.ingest_invoice(rg7206, mark_paid=False)

    results = invoice_ingest.list_unimported_pdfs(INVOICES_DIR)

    match = next(r for r in results if "parsed" in r and r["path"].name == "RG7206.pdf")
    assert match["already_imported"] is True
