import os
import sqlite3
from pathlib import Path

import invoice_parser

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "invoices.db"))


def list_unimported_pdfs(invoices_dir: Path):
    """PDFs in invoices_dir whose invoice number isn't already in the invoices table."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT invoice_number FROM invoices")
    known = {r[0] for r in cur.fetchall()}
    conn.close()

    results = []
    for pdf_path in sorted(invoices_dir.glob("*.pdf")):
        try:
            parsed = invoice_parser.parse_invoice_pdf(pdf_path)
        except Exception as e:
            results.append({"path": pdf_path, "error": str(e)})
            continue
        already_imported = parsed["invoice_number"] in known
        results.append({"path": pdf_path, "parsed": parsed, "already_imported": already_imported})
    return results


def _find_or_create_product(cur, item: dict, invoice_date: str, invoice_number: str) -> int:
    cur.execute(
        "SELECT product_id FROM products WHERE product_number = ? AND unit_price = ?",
        (item["product_number"], item["unit_price"]),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """INSERT INTO products (product_number, product_name, unit, unit_price, vat_percent, effective_date, source_invoice)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            item["product_number"], item["product_name"], item["unit"], item["unit_price"],
            item["vat_percent"], invoice_date, invoice_number,
        ),
    )
    return cur.lastrowid


def ingest_invoice(parsed: dict, mark_paid: bool) -> tuple[bool, str]:
    """Insert a single parsed invoice (from invoice_parser.parse_invoice_pdf) into the DB."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM invoices WHERE invoice_number = ?", (parsed["invoice_number"],))
    if cur.fetchone():
        conn.close()
        return False, f"Invoice {parsed['invoice_number']} already in the system."

    netto = parsed["netto_total"]
    brutto = parsed["brutto_total"]
    vat = round(brutto - netto, 2) if (netto is not None and brutto is not None) else None

    cur.execute(
        """INSERT INTO invoices
           (invoice_number, invoice_date, customer_number, customer_name, supplier_name,
            netto_total, vat_total, brutto_total, is_paid, paid_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            parsed["invoice_number"], parsed["invoice_date"], parsed["customer_number"],
            parsed["customer_name"], parsed["supplier_name"], netto, vat, brutto,
            1 if mark_paid else 0, None,
        ),
    )

    for item in parsed["items"]:
        product_id = _find_or_create_product(cur, item, parsed["invoice_date"], parsed["invoice_number"])
        cur.execute(
            "INSERT INTO invoice_line_items (invoice_number, product_id, quantity, line_total) VALUES (?, ?, ?, ?)",
            (parsed["invoice_number"], product_id, item["quantity"], item["line_total"]),
        )

    conn.commit()
    conn.close()
    return True, f"Imported invoice {parsed['invoice_number']} ({len(parsed['items'])} item(s))."
