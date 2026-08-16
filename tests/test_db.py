import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import db


def test_add_product_then_appears_in_load_data(test_db):
    ok, msg = db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)

    assert ok is True
    products, _ = db.load_data()
    assert len(products) == 1
    assert products.iloc[0]["product_number"] == "1001"
    assert products.iloc[0]["unit_price"] == 2.50


def test_add_product_duplicate_number_and_price_is_rejected(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)

    ok, msg = db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-02-01", None, None)

    assert ok is False
    assert "already has a recorded price" in msg


def test_add_product_same_number_different_price_creates_new_price_era(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)

    ok, msg = db.add_product("1001", "Pommes 2.5kg", "Karton", 2.75, 7.0, "2026-02-01", None, None)

    assert ok is True
    products, _ = db.load_data()
    assert len(products) == 2


def test_add_product_with_bestellliste_link(test_db):
    ok, msg = db.add_product(
        "1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01",
        "Feinfood Bestellliste", "Pommes Frites 2.5kg",
    )
    assert ok is True

    orderable = db.load_orderable("Feinfood Bestellliste")
    assert len(orderable) == 1
    assert orderable.iloc[0]["matched_item_name"] == "Pommes Frites 2.5kg"


def test_update_product_changes_fields(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)
    products, _ = db.load_data()
    product_id = int(products.iloc[0]["product_id"])

    ok, msg = db.update_product(product_id, "Pommes 2.5kg (neu)", "Beutel", 2.60, 7.0, "2026-01-15", None, None)

    assert ok is True
    db.load_data.clear()
    products, _ = db.load_data()
    assert products.iloc[0]["product_name"] == "Pommes 2.5kg (neu)"
    assert products.iloc[0]["unit"] == "Beutel"


def test_delete_product_removes_it(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)
    products, _ = db.load_data()
    product_id = int(products.iloc[0]["product_id"])

    ok, msg = db.delete_product(product_id)

    assert ok is True
    db.load_data.clear()
    products, _ = db.load_data()
    assert products.empty


def test_get_product_usage_counts_orders_and_invoices(test_db):
    db.add_product("1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01", None, None)
    products, _ = db.load_data()
    product_id = int(products.iloc[0]["product_id"])

    invoice_count, order_count = db.get_product_usage(product_id)

    assert (invoice_count, order_count) == (0, 0)


def test_place_order_computes_totals_and_writes_rows(test_db, monkeypatch, tmp_path):
    db.add_product(
        "1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01",
        "Feinfood Bestellliste", "Pommes Frites 2.5kg",
    )
    monkeypatch.setattr(
        db.order_utils, "generate_vendor_order_file",
        lambda *a, **k: tmp_path / "vendor.xlsx",
    )
    monkeypatch.setattr(
        db.order_utils, "generate_invoice_pattern_file",
        lambda *a, **k: tmp_path / "confirmation.xlsx",
    )

    orderable = db.load_orderable("Feinfood Bestellliste")
    order_df = orderable.copy()
    order_df["Order Qty"] = [3.0]

    result = db.place_order("Feinfood Bestellliste", order_df, "2026-08-16")

    assert result is not None
    assert result["netto_total"] == 7.50
    assert result["vat_total"] == 0.53
    assert result["item_count"] == 1

    history = db.load_order_history()
    assert len(history) == 1
    assert history.iloc[0]["order_date"] == "2026-08-16"


def test_place_order_with_no_quantities_returns_none(test_db):
    db.add_product(
        "1001", "Pommes 2.5kg", "Karton", 2.50, 7.0, "2026-01-01",
        "Feinfood Bestellliste", "Pommes Frites 2.5kg",
    )
    orderable = db.load_orderable("Feinfood Bestellliste")
    order_df = orderable.copy()
    order_df["Order Qty"] = [0.0]

    result = db.place_order("Feinfood Bestellliste", order_df, "2026-08-16")

    assert result is None


def test_set_invoice_paid_status_toggles_and_stamps_date(test_db):
    conn_row = ("RG1", "2026-08-01", None, None, None, 100.0, 7.0, 107.0, 0, None)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO invoices (invoice_number, invoice_date, customer_number, customer_name, "
        "supplier_name, netto_total, vat_total, brutto_total, is_paid, paid_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        conn_row,
    )
    conn.commit()
    conn.close()

    db.set_invoice_paid_status("RG1", True)

    status = db.load_invoices_by_status()
    row = status[status["invoice_number"] == "RG1"].iloc[0]
    assert row["is_paid"] == 1
    assert row["paid_date"] is not None


def test_delete_invoice_removes_it(test_db):
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO invoices (invoice_number, invoice_date, netto_total, vat_total, brutto_total, is_paid) "
        "VALUES ('RG1', '2026-08-01', 100.0, 7.0, 107.0, 0)"
    )
    conn.commit()
    conn.close()

    db.delete_invoice("RG1")

    status = db.load_invoices_by_status()
    assert status.empty


@pytest.mark.parametrize("vendor", ["fatihet", "metro"])
def test_import_vendor_invoice_then_appears_in_history(test_db, vendor):
    parsed = {
        "invoice_number": "INV1", "invoice_date": "2026-08-01", "delivery_date": "2026-08-01",
        "netto_total": 100.0, "vat_total": 7.0, "brutto_total": 107.0,
        "items": [
            {"pos": 1, "description": "Item A", "product_number": "A1",
             "quantity": 2.0, "unit": "Kg", "unit_price": 50.0, "line_total": 100.0},
        ],
    }

    ok, msg = db.import_vendor_invoice(vendor, parsed, "source.pdf")

    assert ok is True
    history = db.load_vendor_invoices(vendor)
    assert len(history) == 1
    assert history.iloc[0]["invoice_number"] == "INV1"

    items = db.load_vendor_invoice_items(vendor, "INV1")
    assert len(items) == 1
    assert items.iloc[0]["description"] == "Item A"


@pytest.mark.parametrize("vendor", ["fatihet", "metro"])
def test_import_vendor_invoice_duplicate_is_rejected(test_db, vendor):
    parsed = {
        "invoice_number": "INV1", "invoice_date": "2026-08-01", "delivery_date": "2026-08-01",
        "netto_total": 100.0, "vat_total": 7.0, "brutto_total": 107.0, "items": [],
    }
    db.import_vendor_invoice(vendor, parsed, "source.pdf")

    ok, msg = db.import_vendor_invoice(vendor, parsed, "source.pdf")

    assert ok is False
    assert "already been imported" in msg


@pytest.mark.parametrize("vendor", ["fatihet", "metro"])
def test_delete_vendor_invoice_removes_invoice_and_items(test_db, vendor):
    parsed = {
        "invoice_number": "INV1", "invoice_date": "2026-08-01", "delivery_date": "2026-08-01",
        "netto_total": 100.0, "vat_total": 7.0, "brutto_total": 107.0,
        "items": [
            {"pos": 1, "description": "Item A", "product_number": "A1",
             "quantity": 2.0, "unit": "Kg", "unit_price": 50.0, "line_total": 100.0},
        ],
    }
    db.import_vendor_invoice(vendor, parsed, "source.pdf")

    db.delete_vendor_invoice(vendor, "INV1")

    assert db.load_vendor_invoices(vendor).empty
    assert db.load_vendor_invoice_items(vendor, "INV1").empty


@pytest.mark.parametrize("vendor", ["fatihet", "metro"])
def test_vendor_invoices_feed_into_monthly_costs(test_db, vendor):
    parsed = {
        "invoice_number": "INV1", "invoice_date": "2026-08-01", "delivery_date": "2026-08-01",
        "netto_total": 100.0, "vat_total": 7.0, "brutto_total": 107.0, "items": [],
    }
    db.import_vendor_invoice(vendor, parsed, "source.pdf")

    monthly = db.load_monthly_costs()

    label = "Fatihet" if vendor == "fatihet" else "Metro"
    vendor_rows = monthly[monthly["source"] == label]
    assert len(vendor_rows) == 1
    assert vendor_rows.iloc[0]["brutto_total"] == 107.0
