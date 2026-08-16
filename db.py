"""All SQLite access for the Rooster app lives here — app.py only calls into this module."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import order_utils

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "invoices.db"))

# Fatihet and Metro invoices are stored identically (see load/import/delete_vendor_invoice
# below) — this is the only thing that differs between the two.
VENDOR_TABLES = {
    "fatihet": ("fatihet_invoices", "fatihet_invoice_items"),
    "metro": ("metro_invoices", "metro_invoice_items"),
}

SURCHARGE_PRODUCT_NUMBER = {
    "Feinfood Bestellliste": "9",     # FeinFood Liefer- und Energiezuschlag
    "Nachoking Bestellliste": "214",  # NachoKings Liefer- und Energiezuschlag
}


@st.cache_data
def load_data():
    conn = sqlite3.connect(DB_PATH)
    products = pd.read_sql_query(
        """
        SELECT p.product_id, p.product_number, p.product_name, p.unit,
               p.unit_price, p.vat_percent, p.effective_date, p.source_invoice, m.bestellliste
        FROM products p
        LEFT JOIN bestellliste_map m ON m.product_id = p.product_id
        ORDER BY p.product_number, p.effective_date
        """,
        conn,
    )
    line_items = pd.read_sql_query(
        """
        SELECT li.invoice_number, i.invoice_date, p.product_number, p.product_name,
               p.unit, li.quantity, p.unit_price, li.line_total
        FROM invoice_line_items li
        JOIN products p ON p.product_id = li.product_id
        JOIN invoices i ON i.invoice_number = li.invoice_number
        ORDER BY i.invoice_date, li.invoice_number
        """,
        conn,
    )
    conn.close()
    return products, line_items


@st.cache_data
def load_all_products_with_map():
    """Same shape as load_data()'s products frame, but unfiltered/uncached-friendly for the edit form."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT p.product_id, p.product_number, p.product_name, p.unit, p.unit_price, p.vat_percent,
                  p.effective_date, p.source_invoice, m.bestellliste, m.matched_item_name
           FROM products p LEFT JOIN bestellliste_map m ON m.product_id = p.product_id
           ORDER BY p.product_number, p.effective_date""",
        conn,
    )
    conn.close()
    return df


@st.cache_data
def load_orderable(bestellliste_name: str):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT p.product_id, p.product_number, p.product_name, p.unit,
               p.unit_price, COALESCE(p.vat_percent, 0) AS vat_percent, m.matched_item_name
        FROM bestellliste_map m
        JOIN products p ON p.product_id = m.product_id
        WHERE m.bestellliste = ?
        ORDER BY p.product_number
        """,
        conn,
        params=(bestellliste_name,),
    )
    conn.close()
    return df


@st.cache_data
def load_monthly_costs():
    conn = sqlite3.connect(DB_PATH)
    orders_df = pd.read_sql_query(
        "SELECT order_date AS date, bestellliste AS source, netto_total, vat_total, brutto_total FROM orders",
        conn,
    )
    invoices_df = pd.read_sql_query(
        """SELECT invoice_date AS date, netto_total, vat_total, brutto_total
           FROM invoices WHERE netto_total IS NOT NULL""",
        conn,
    )
    fatihet_df = pd.read_sql_query(
        "SELECT invoice_date AS date, netto_total, vat_total, brutto_total FROM fatihet_invoices",
        conn,
    )
    metro_df = pd.read_sql_query(
        "SELECT invoice_date AS date, netto_total, vat_total, brutto_total FROM metro_invoices",
        conn,
    )
    conn.close()
    invoices_df["source"] = "Imported Invoices"
    fatihet_df["source"] = "Fatihet"
    metro_df["source"] = "Metro"

    df = pd.concat([orders_df, invoices_df, fatihet_df, metro_df], ignore_index=True)
    if not df.empty:
        df["month"] = df["date"].str.slice(0, 7)  # YYYY-MM
    return df


@st.cache_data
def load_invoices_by_status():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT invoice_number, invoice_date, customer_name, customer_number,
                  netto_total, vat_total, brutto_total, is_paid, paid_date
           FROM invoices ORDER BY invoice_date""",
        conn,
    )
    conn.close()
    return df


def set_invoice_paid_status(invoice_number: str, paid: bool):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE invoices SET is_paid = ?, paid_date = ? WHERE invoice_number = ?",
        (1 if paid else 0, datetime.now().date().isoformat() if paid else None, invoice_number),
    )
    conn.commit()
    conn.close()


def delete_invoice(invoice_number: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM invoice_line_items WHERE invoice_number = ?", (invoice_number,))
    cur.execute("DELETE FROM invoices WHERE invoice_number = ?", (invoice_number,))
    conn.commit()
    conn.close()


@st.cache_data
def load_vendor_invoices(vendor: str):
    """Fatihet or Metro invoice history — both tables have an identical shape."""
    invoices_table, _ = VENDOR_TABLES[vendor]
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        f"""SELECT invoice_number, invoice_date, delivery_date,
                   netto_total, vat_total, brutto_total, source_file
            FROM {invoices_table} ORDER BY invoice_date DESC""",
        conn,
    )
    conn.close()
    return df


def load_vendor_invoice_items(vendor: str, invoice_number: str):
    _, items_table = VENDOR_TABLES[vendor]
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        f"""SELECT pos, description, product_number, quantity, unit, unit_price, line_total
            FROM {items_table} WHERE invoice_number = ? ORDER BY pos""",
        conn,
        params=(invoice_number,),
    )
    conn.close()
    return df


def import_vendor_invoice(vendor: str, parsed: dict, source_file: str):
    invoices_table, items_table = VENDOR_TABLES[vendor]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""INSERT INTO {invoices_table}
                (invoice_number, invoice_date, delivery_date, netto_total, vat_total, brutto_total, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                parsed["invoice_number"], parsed["invoice_date"], parsed["delivery_date"],
                parsed["netto_total"], parsed["vat_total"], parsed["brutto_total"],
                source_file, datetime.now().isoformat(timespec="seconds"),
            ),
        )
        for item in parsed["items"]:
            cur.execute(
                f"""INSERT INTO {items_table}
                    (invoice_number, pos, description, product_number, quantity, unit, unit_price, line_total)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parsed["invoice_number"], item["pos"], item["description"], item["product_number"],
                    item["quantity"], item["unit"], item["unit_price"], item["line_total"],
                ),
            )
        conn.commit()
        return True, f"Imported invoice {parsed['invoice_number']} — {len(parsed['items'])} items."
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, f"Invoice {parsed['invoice_number']} has already been imported."
    finally:
        conn.close()


def delete_vendor_invoice(vendor: str, invoice_number: str):
    invoices_table, items_table = VENDOR_TABLES[vendor]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {items_table} WHERE invoice_number = ?", (invoice_number,))
    cur.execute(f"DELETE FROM {invoices_table} WHERE invoice_number = ?", (invoice_number,))
    conn.commit()
    conn.close()


def upsert_bestellliste_map(cur, product_id: int, bestellliste_name: str | None, matched_item_name: str | None):
    if bestellliste_name:
        cur.execute(
            """INSERT INTO bestellliste_map (product_id, bestellliste, matched_item_name, matched_artikelnr)
               VALUES (?, ?, ?, '')
               ON CONFLICT(product_id) DO UPDATE SET
                 bestellliste=excluded.bestellliste,
                 matched_item_name=excluded.matched_item_name""",
            (product_id, bestellliste_name, matched_item_name or ""),
        )
    else:
        cur.execute("DELETE FROM bestellliste_map WHERE product_id = ?", (product_id,))


def add_product(product_number, product_name, unit, unit_price, vat_percent, effective_date, bestellliste_name, matched_item_name):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO products (product_number, product_name, unit, unit_price, vat_percent, effective_date, source_invoice)
               VALUES (?, ?, ?, ?, ?, ?, 'Manual entry')""",
            (product_number, product_name, unit, unit_price, vat_percent, effective_date),
        )
        product_id = cur.lastrowid
        upsert_bestellliste_map(cur, product_id, bestellliste_name, matched_item_name)
        conn.commit()
        return True, f"Added product #{product_id} ({product_number} — {product_name})."
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, f"Product number '{product_number}' already has a recorded price of €{unit_price:.2f}. Use a different price to record a price change, or edit the existing entry instead."
    finally:
        conn.close()


def update_product(product_id, product_name, unit, unit_price, vat_percent, effective_date, bestellliste_name, matched_item_name):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE products SET product_name = ?, unit = ?, unit_price = ?, vat_percent = ?, effective_date = ?
               WHERE product_id = ?""",
            (product_name, unit, unit_price, vat_percent, effective_date, product_id),
        )
        upsert_bestellliste_map(cur, product_id, bestellliste_name, matched_item_name)
        conn.commit()
        return True, f"Saved changes to product #{product_id}."
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, f"Another entry for this product number already has a price of €{unit_price:.2f}. Choose a different price."
    finally:
        conn.close()


def get_product_usage(product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM invoice_line_items WHERE product_id = ?", (product_id,))
    invoice_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM order_items WHERE product_id = ?", (product_id,))
    order_count = cur.fetchone()[0]
    conn.close()
    return invoice_count, order_count


def delete_product(product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM bestellliste_map WHERE product_id = ?", (product_id,))
    cur.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()
    return True, f"Deleted product #{product_id}."


@st.cache_data
def load_order_history():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT order_id, order_date, bestellliste, netto_total, vat_total, brutto_total,
                  vendor_order_file, invoice_pattern_file, created_at
           FROM orders ORDER BY order_id DESC""",
        conn,
    )
    conn.close()
    return df


@st.cache_data
def load_orders_for_select():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT order_id, order_date, bestellliste, invoice_pattern_file FROM orders ORDER BY order_id DESC",
        conn,
    )
    conn.close()
    return df


def delete_order(order_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT vendor_order_file, invoice_pattern_file FROM orders WHERE order_id = ?", (order_id,))
    row = cur.fetchone()
    cur.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    cur.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))
    conn.commit()
    conn.close()

    if row:
        for file_path in row:
            if file_path:
                p = Path(file_path)
                if p.exists():
                    p.unlink()

    return True, f"Deleted order #{order_id}."


def get_surcharge_product(bestellliste_name: str):
    product_number = SURCHARGE_PRODUCT_NUMBER.get(bestellliste_name)
    if not product_number:
        return None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT product_id, product_number, product_name, unit, unit_price, vat_percent FROM products WHERE product_number = ?",
        (product_number,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "product_id": row[0], "product_number": row[1], "product_name": row[2],
        "unit": row[3], "unit_price": row[4], "vat_percent": row[5],
    }


def place_order(bestellliste_name: str, order_df: pd.DataFrame, order_date: str):
    order_rows = order_df[order_df["Order Qty"] > 0]
    if order_rows.empty:
        return None

    items_for_totals = [
        {
            "quantity": float(r["Order Qty"]),
            "unit_price": float(r["unit_price"]),
            "vat_percent": float(r["vat_percent"]),
        }
        for _, r in order_rows.iterrows()
    ]
    surcharge = get_surcharge_product(bestellliste_name)
    if surcharge:
        items_for_totals.append({"quantity": 1.0, "unit_price": surcharge["unit_price"], "vat_percent": surcharge["vat_percent"]})
    netto_total, vat_total, brutto_total = order_utils.compute_totals(items_for_totals)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO orders (order_date, bestellliste, netto_total, vat_total, brutto_total, created_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (order_date, bestellliste_name, netto_total, vat_total, brutto_total),
    )
    order_id = cur.lastrowid

    vendor_items = []
    invoice_items = []
    for _, r in order_rows.iterrows():
        qty = float(r["Order Qty"])
        line_netto = round(qty * float(r["unit_price"]), 2)
        line_brutto = round(line_netto * (1 + float(r["vat_percent"]) / 100), 2)
        cur.execute(
            """INSERT INTO order_items
               (order_id, product_id, product_number, product_name, unit, quantity, unit_price, vat_percent, line_netto, line_brutto)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id, int(r["product_id"]), r["product_number"], r["product_name"], r["unit"],
                qty, float(r["unit_price"]), float(r["vat_percent"]), line_netto, line_brutto,
            ),
        )
        vendor_items.append({"matched_item_name": r["matched_item_name"], "quantity": qty})
        invoice_items.append({
            "product_number": r["product_number"],
            "product_name": r["product_name"],
            "unit": r["unit"],
            "quantity": qty,
            "unit_price": float(r["unit_price"]),
            "vat_percent": float(r["vat_percent"]),
        })

    if surcharge:
        s_netto = round(1.0 * surcharge["unit_price"], 2)
        s_brutto = round(s_netto * (1 + surcharge["vat_percent"] / 100), 2)
        cur.execute(
            """INSERT INTO order_items
               (order_id, product_id, product_number, product_name, unit, quantity, unit_price, vat_percent, line_netto, line_brutto)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id, surcharge["product_id"], surcharge["product_number"], surcharge["product_name"],
                surcharge["unit"], 1.0, surcharge["unit_price"], surcharge["vat_percent"], s_netto, s_brutto,
            ),
        )
        # not added to vendor_items: this fee has no orderable row in the vendor's own bestellliste template
        invoice_items.append({
            "product_number": surcharge["product_number"],
            "product_name": surcharge["product_name"],
            "unit": surcharge["unit"],
            "quantity": 1.0,
            "unit_price": surcharge["unit_price"],
            "vat_percent": surcharge["vat_percent"],
        })

    vendor_path = order_utils.generate_vendor_order_file(bestellliste_name, vendor_items, order_date, order_id)
    invoice_path = order_utils.generate_invoice_pattern_file(bestellliste_name, invoice_items, order_date, order_id)

    cur.execute(
        "UPDATE orders SET vendor_order_file = ?, invoice_pattern_file = ? WHERE order_id = ?",
        (str(vendor_path), str(invoice_path), order_id),
    )
    conn.commit()
    conn.close()

    return {
        "order_id": order_id,
        "order_date": order_date,
        "bestellliste": bestellliste_name,
        "netto_total": netto_total,
        "vat_total": vat_total,
        "brutto_total": brutto_total,
        "vendor_path": vendor_path,
        "invoice_path": invoice_path,
        "item_count": len(order_rows) + (1 if surcharge else 0),
        "surcharge": surcharge,
    }
