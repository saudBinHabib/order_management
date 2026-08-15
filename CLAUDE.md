# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page Streamlit app (`app.py`) that turns a folder of German wholesale-food invoice PDFs (FeinFood Express GmbH) into a queryable product catalog, matches that catalog against the vendor's own order sheets ("Bestellliste"), and drives the full ordering round-trip: catalog → place order → generate vendor order file → verify vendor's invoice against what was ordered. Everything is backed by a single SQLite database, `invoices.db`.

Not a git repository — there is no version control here. Be extra careful with destructive file operations since there's no history to recover from.

## Commands

```bash
# Setup (Python 3.11+)
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Run the app (opens at http://localhost:8501)
./venv/bin/streamlit run app.py
```

There is no test suite, linter, or build step configured in this project.

After any manual/direct change to `invoices.db`'s schema or data, regenerate the plain-text dump so it stays in sync:

```bash
sqlite3 invoices.db ".dump" > invoices_dump.sql
```

## Architecture

Everything routes through `invoices.db` (SQLite) as the single source of truth. Every module opens its own `sqlite3.connect(DB_PATH)` connection per operation — there is no shared connection pool or ORM.

- **`app.py`** — the entire UI. All Streamlit pages, forms, and DB read/write functions live here (catalog view, monthly cost dashboard, product CRUD, order placement, invoice verification). When adding a UI feature, this is almost always the file to edit.
- **`invoice_parser.py`** — parses vendor invoice PDFs using `pdfplumber` + regex into a dict of header fields and line items. Pure parsing, no DB access.
- **`invoice_ingest.py`** — takes a parsed invoice dict and writes it into `invoices` / `invoice_line_items` / `products`, creating new product rows on price changes (`_find_or_create_product`). Also finds PDFs in `invoices/` not yet in the DB (`list_unimported_pdfs`).
- **`order_utils.py`** — generates the two files produced when an order is placed: the vendor's own bestellliste `.xlsx` template with quantities filled in (`generate_vendor_order_file`), and an invoice-pattern `.xlsx` mirroring the vendor's invoice layout for later verification (`generate_invoice_pattern_file`). Also has `compute_totals` for Netto/USt./Brutto math shared with `app.py`.
- **`compare_utils.py`** — parses an order-confirmation `.xlsx` back into line items and diffs it against a real invoice's parsed line items (`compare_order_to_invoice`), flagging price mismatches, quantity mismatches, unexpected charges, and missing items.

### Key schema concept: products are price eras, not rows-per-item

`products` is keyed by `(product_number, unit_price)` (UNIQUE constraint), not just `product_number`. A product gets a **new row** whenever its price changes rather than an overwrite — this preserves full price history. Any code that looks up "the current product" needs to pick the latest `effective_date` row for a given `product_number`, and code that inserts must not assume `product_number` is unique.

`bestellliste_map` is a soft link (by matched item name, not a hard foreign key) from a `product_id` to the vendor's own order-sheet row, because the vendor's own numbering scheme is inconsistent across their two sheets (`bestellliste/fein food .xlsx`, `bestellliste/nacho king.xlsx`).

`invoices.is_paid` / `invoices.paid_date` track payment status per invoice and can be toggled from either the paid or unpaid view in the app.

Full table-by-table reference: see the ERD linked from `README.md`, or run `sqlite3 invoices.db ".schema"`.

### Directory layout

- `invoices/` — source PDFs, one per vendor invoice (`RG####.pdf`), read by `invoice_ingest.py`.
- `bestellliste/` — the vendor's own order-sheet templates, used as the base for generated vendor order files.
- `orders/` — generated output files (vendor order file + invoice-pattern confirmation), one pair per placed order, written by `order_utils.py`.
- `invoices.db.bak-*` — point-in-time backups taken before risky schema migrations; safe to delete once the current DB is confirmed healthy.

## Working in this codebase

- Money values are Netto (pre-VAT) / USt. or vat (VAT amount) / Brutto (total incl. VAT) throughout — German invoice terminology, keep it consistent with existing column/variable names rather than translating to English.
- Any change touching the `products`, `orders`, `order_items`, `invoices`, or `invoice_line_items` tables should go through a migration-style script (see the pattern of one-off scripts used historically for schema changes) rather than editing `invoices.db` by hand, and should end with regenerating `invoices_dump.sql`.
- Back up `invoices.db` (copy to `invoices.db.bak-<timestamp>`) before any migration that alters existing rows, matching the existing `.bak-*` naming convention.
