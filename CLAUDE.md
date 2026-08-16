# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit app (`app.py`, UI-only — DB access lives in `db.py`) that turns wholesale invoice PDFs from three vendors (FeinFood Express GmbH, Fatih Et GmbH, METRO) into a queryable product catalog and cost ledger, matches the FeinFood catalog against the vendor's own order sheets ("Bestellliste"), and drives the full FeinFood ordering round-trip: catalog → place order → generate vendor order file → verify vendor's invoice against what was ordered (confirm as unpaid, or generate a dispute message). Fatihet and Metro invoices are recorded as their own cost ledgers, not linked to the product catalog. Everything is backed by a single SQLite database, `invoices.db`. Deployed on Railway — see the README's Deployment section and `Procfile`.

This is a git repository — use normal git workflow (branches, commits) rather than treating changes as unversioned.

## Commands

```bash
# Setup (Python 3.11+)
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Run the app (opens at http://localhost:8501)
./venv/bin/streamlit run app.py
```

```bash
# Run the test suite (installs pytest too)
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest tests/
```

Tests that need a real sample invoice PDF (`invoices/`, `fatihet/`, `metro/` — all gitignored business data) skip themselves automatically if that folder isn't present, e.g. on a fresh clone. There is no linter or build step configured in this project.

After any manual/direct change to `invoices.db`'s schema or data, regenerate the plain-text dump so it stays in sync:

```bash
sqlite3 invoices.db ".dump" > invoices_dump.sql
```

## Architecture

Everything routes through `invoices.db` (SQLite) as the single source of truth, accessed only through `db.py` — every other module goes through that layer rather than opening its own connection.

- **`app.py`** — the entire UI. All Streamlit pages/forms/dialogs live here; it calls into `db.py` for every read/write. Shared UI helpers (`render_vendor_invoice_section`, `render_confirm_buttons`) exist because the Fatihet and Metro upload flows, and every delete-confirmation dialog, would otherwise be near-duplicated. When adding a UI feature, this is almost always the file to edit.
- **`db.py`** — every SQLite read/write function, plus `@st.cache_data` caching (cleared explicitly by `app.py` after each write via `db.<fn>.clear()`). Fatihet and Metro are stored in structurally identical tables, so their CRUD is one set of `vendor`-parameterized functions (`load_vendor_invoices`, `import_vendor_invoice`, etc.) driven by the `VENDOR_TABLES` dict, not two copies.
- **`invoice_parser.py` / `fatihet_parser.py` / `metro_parser.py`** — one PDF-parsing module per vendor (each vendor's invoice layout is meaningfully different — see each module's docstring), using `pdfplumber` + regex. Pure parsing, no DB access. `metro_parser`'s item-level output is explicitly best-effort (METRO's invoice is a dense POS ledger); its Netto/USt./Brutto totals, read from a fixed-format summary block, are reliable.
- **`invoice_ingest.py`** — takes a parsed FeinFood invoice dict and writes it into `invoices` / `invoice_line_items` / `products`, creating new product rows on price changes (`_find_or_create_product`). Also finds PDFs in `invoices/` not yet in the DB (`list_unimported_pdfs`). Has its own `DB_PATH` (same env var as `db.py`) rather than routing through `db.py`, since it's a self-contained ingestion pipeline.
- **`order_utils.py`** — generates the two files produced when an order is placed: the vendor's own bestellliste `.xlsx` template with quantities filled in (`generate_vendor_order_file`), and an invoice-pattern `.xlsx` mirroring the vendor's invoice layout for later verification (`generate_invoice_pattern_file`). Also has `compute_totals` for Netto/USt./Brutto math, used by both `db.py` and `app.py`.
- **`compare_utils.py`** — parses an order-confirmation `.xlsx` back into line items and diffs it against a real invoice's parsed line items (`compare_order_to_invoice`), flagging price mismatches, quantity mismatches, unexpected charges, and missing items. `build_dispute_message` turns a flagged comparison into a plain-text message for the vendor.
- **`tests/`** — pytest suite covering `db.py`, `invoice_ingest.py`, `compare_utils.py`, `order_utils.py`, and all three parsers. `tests/conftest.py`'s `test_db` fixture spins up a fresh temp SQLite file per test (full schema, monkeypatched over `db.DB_PATH`/`invoice_ingest.DB_PATH`) so tests never touch the real `invoices.db` — and explicitly clears every `@st.cache_data` function's cache per test, since Streamlit's cache is keyed by function+args, not by which DB file is active.

### Key schema concept: products are price eras, not rows-per-item

`products` is keyed by `(product_number, unit_price)` (UNIQUE constraint), not just `product_number`. A product gets a **new row** whenever its price changes rather than an overwrite — this preserves full price history. Any code that looks up "the current product" needs to pick the latest `effective_date` row for a given `product_number`, and code that inserts must not assume `product_number` is unique.

`bestellliste_map` is a soft link (by matched item name, not a hard foreign key) from a `product_id` to the vendor's own order-sheet row, because the vendor's own numbering scheme is inconsistent across their two sheets (`bestellliste/fein food .xlsx`, `bestellliste/nacho king.xlsx`).

`invoices.is_paid` / `invoices.paid_date` track payment status per invoice and can be toggled from either the paid or unpaid view in the app.

Full table-by-table reference: see the ERD linked from `README.md`, or run `sqlite3 invoices.db ".schema"`.

### Directory layout

- `invoices/`, `fatihet/`, `metro/` — source PDFs per vendor (gitignored business data; `invoices/RG####.pdf` is read by `invoice_ingest.py`, the other two are uploaded through the app).
- `bestellliste/` — the vendor's own order-sheet templates, used as the base for generated vendor order files. Not gitignored (blank templates, no transaction data) — `order_utils.py` needs them at runtime.
- `orders/` — generated output files (vendor order file + invoice-pattern confirmation), one pair per placed order, written by `order_utils.py`.
- `tests/` — pytest suite; see Commands above.
- `invoices.db.bak-*` — point-in-time backups taken before risky schema migrations; safe to delete once the current DB is confirmed healthy.

## Working in this codebase

- Money values are Netto (pre-VAT) / USt. or vat (VAT amount) / Brutto (total incl. VAT) throughout — German invoice terminology, keep it consistent with existing column/variable names rather than translating to English.
- Any change touching the `products`, `orders`, `order_items`, `invoices`, or `invoice_line_items` tables should go through a migration-style script (see the pattern of one-off scripts used historically for schema changes) rather than editing `invoices.db` by hand, and should end with regenerating `invoices_dump.sql`.
- Back up `invoices.db` (copy to `invoices.db.bak-<timestamp>`) before any migration that alters existing rows, matching the existing `.bak-*` naming convention.
