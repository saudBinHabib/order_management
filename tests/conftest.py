import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

SCHEMA = """
CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    product_number   TEXT NOT NULL,
    product_name     TEXT NOT NULL,
    unit             TEXT,
    unit_price       REAL NOT NULL,
    vat_percent      REAL,
    effective_date   TEXT NOT NULL,
    source_invoice   TEXT,
    UNIQUE (product_number, unit_price)
);
CREATE TABLE bestellliste_map (
    product_id           INTEGER PRIMARY KEY,
    bestellliste          TEXT NOT NULL,
    matched_item_name     TEXT NOT NULL,
    matched_artikelnr     TEXT,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE TABLE invoices (
    invoice_number   TEXT PRIMARY KEY,
    invoice_date     TEXT NOT NULL,
    customer_number  TEXT,
    customer_name    TEXT,
    supplier_name    TEXT,
    netto_total REAL, vat_total REAL, brutto_total REAL,
    is_paid INTEGER NOT NULL DEFAULT 0, paid_date TEXT
);
CREATE TABLE invoice_line_items (
    line_item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number   TEXT NOT NULL,
    product_id       INTEGER NOT NULL,
    quantity         REAL NOT NULL,
    line_total       REAL NOT NULL,
    FOREIGN KEY (invoice_number) REFERENCES invoices(invoice_number),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE TABLE orders (
    order_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_date             TEXT NOT NULL,
    bestellliste           TEXT NOT NULL,
    netto_total             REAL NOT NULL,
    vat_total                REAL NOT NULL,
    brutto_total              REAL NOT NULL,
    vendor_order_file          TEXT,
    invoice_pattern_file        TEXT,
    created_at                   TEXT NOT NULL
);
CREATE TABLE order_items (
    order_item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id                 INTEGER NOT NULL,
    product_id                 INTEGER NOT NULL,
    product_number               TEXT NOT NULL,
    product_name                   TEXT NOT NULL,
    unit                             TEXT,
    quantity                          REAL NOT NULL,
    unit_price                          REAL NOT NULL,
    vat_percent                           REAL NOT NULL,
    line_netto                              REAL NOT NULL,
    line_brutto                               REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE TABLE fatihet_invoices (
    invoice_number TEXT PRIMARY KEY,
    invoice_date   TEXT NOT NULL,
    delivery_date  TEXT,
    netto_total    REAL NOT NULL,
    vat_total      REAL NOT NULL,
    brutto_total   REAL NOT NULL,
    source_file    TEXT,
    imported_at    TEXT NOT NULL
);
CREATE TABLE fatihet_invoice_items (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    pos            INTEGER,
    description    TEXT NOT NULL,
    product_number TEXT,
    quantity       REAL NOT NULL,
    unit           TEXT,
    unit_price     REAL NOT NULL,
    line_total     REAL NOT NULL,
    FOREIGN KEY (invoice_number) REFERENCES fatihet_invoices(invoice_number)
);
CREATE TABLE metro_invoices (
    invoice_number TEXT PRIMARY KEY,
    invoice_date   TEXT NOT NULL,
    delivery_date  TEXT,
    netto_total    REAL NOT NULL,
    vat_total      REAL NOT NULL,
    brutto_total   REAL NOT NULL,
    source_file    TEXT,
    imported_at    TEXT NOT NULL
);
CREATE TABLE metro_invoice_items (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    pos            INTEGER,
    description    TEXT NOT NULL,
    product_number TEXT,
    quantity       REAL NOT NULL,
    unit           TEXT,
    unit_price     REAL NOT NULL,
    line_total     REAL NOT NULL,
    FOREIGN KEY (invoice_number) REFERENCES metro_invoices(invoice_number)
);
"""


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """A fresh SQLite file with the full schema, wired up as db.DB_PATH / invoice_ingest.DB_PATH."""
    db_path = tmp_path / "test_invoices.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    import db as db_module
    import invoice_ingest as invoice_ingest_module

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(invoice_ingest_module, "DB_PATH", db_path)

    # st.cache_data is keyed by function+args, not by DB_PATH, so a cached result from
    # a previous test's temp database would otherwise leak into this one.
    for cached_fn in (
        db_module.load_data, db_module.load_all_products_with_map, db_module.load_orderable,
        db_module.load_monthly_costs, db_module.load_invoices_by_status, db_module.load_vendor_invoices,
        db_module.load_order_history, db_module.load_orders_for_select,
    ):
        cached_fn.clear()

    return db_path


@pytest.fixture
def repo_root():
    return Path(__file__).parent.parent
