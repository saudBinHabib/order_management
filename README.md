# Rooster Inventory

A Streamlit app that turns a folder of German wholesale invoices (FeinFood Express GmbH, Fatih Et GmbH, and METRO) into a queryable product catalog and cost ledger, matches the FeinFood catalog against the vendor's own order sheets ("Bestellliste"), and lets you place inventory orders, generate vendor-ready order files, verify a vendor's invoice against what you actually ordered, and track meat and cash-and-carry spend by month — all backed by a single SQLite database.

**Schema reference:** [Entity Relationship Diagram](https://claude.ai/code/artifact/216590c8-d043-4ae2-9828-9d2d088dcb73) — all 10 tables, including the Fatihet and Metro invoice ledgers.

## Why this exists

Rooster Fried Chicken / Habib RFC Mannheim buys from three places: FeinFood Express (freezer goods, packaging, supplies — ordered via two vendor spreadsheets, "fein food .xlsx" and "nacho king.xlsx"), Fatih Et GmbH (meat, ordered separately and priced per invoice), and METRO (cash-and-carry, prices that change too often to catalog). Historically:

- Prices lived only in scanned PDF invoices, with no easy way to see what a product costs or whether a price ever changed.
- Placing a FeinFood order meant manually retyping quantities into the vendor's spreadsheet.
- There was no way to check whether the vendor's invoice actually matched what was ordered, at the price that was agreed.
- Meat and cash-and-carry spend had no monthly total anywhere — just a stack of PDFs.

This project extracts the invoice history into a real database, keeps FeinFood price changes as a version history rather than overwriting them, wraps the FeinFood ordering round-trip (catalog → order → vendor file → invoice check) in one app, and records Fatihet/Metro invoices as their own cost ledgers that roll up into a single monthly spend view across every vendor.

## Project structure

```
Rooster/
├── app.py                  # Streamlit app — all UI lives here
├── order_utils.py          # Vendor order file + order confirmation file generation
├── invoice_parser.py       # Parses FeinFood invoice PDFs (pdfplumber + regex)
├── invoice_ingest.py       # Imports parsed FeinFood invoices into the catalog/invoice tables
├── fatihet_parser.py       # Parses Fatih Et (meat) invoice PDFs
├── metro_parser.py         # Parses METRO invoice PDFs (best-effort on line items, reliable on totals)
├── compare_utils.py        # Order-confirmation parsing + discrepancy comparison
├── invoices.db              # SQLite database (source of truth)
├── invoices_dump.sql       # Plain-text SQL dump of invoices.db, regenerate after schema changes
├── requirements.txt        # Pinned Python dependencies
├── Procfile                # Railway/Heroku-style start command for deployment
├── venv/                   # Project-local virtualenv (not committed)
├── invoices/                # Source PDFs — FeinFood Express invoices (RG####.pdf)
├── fatihet/                  # Source PDFs — Fatih Et (meat) invoices
├── metro/                     # Source PDFs — METRO invoices
├── bestellliste/            # Vendor's own order-sheet templates (.xlsx)
│   ├── fein food .xlsx
│   └── nacho king.xlsx
└── orders/                  # Generated order files (vendor template + order confirmation), one pair per placed order
```

## Setup

Requires Python 3.11+.

```bash
cd Rooster
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Running the app

```bash
./venv/bin/streamlit run app.py
```

Opens at `http://localhost:8501`.

### Configuration

Paths default to local folders next to `app.py`, but can be overridden with environment variables — this is what lets the app run against a persistent disk when deployed (see [Deployment](#deployment)):

| Variable | Default | Used for |
|---|---|---|
| `DB_PATH` | `./invoices.db` | The SQLite database |
| `INVOICES_DIR` | `./invoices/` | FeinFood invoice PDFs to scan for import |
| `BESTELLLISTE_DIR` | `./bestellliste/` | Vendor order-sheet templates |
| `ORDERS_DIR` | `./orders/` | Generated order files |

## What's in the database

Everything lives in `invoices.db` (10 tables):

| Table | Purpose |
|---|---|
| `products` | The FeinFood catalog. One row per product **price era** — a product gets a *new* row when its price changes, not an overwrite, so price history is never lost. |
| `bestellliste_map` | Links a catalog product to the vendor's own order-sheet row (their sheet uses a different, inconsistent numbering scheme, so this is a soft link keyed by matched item name). |
| `invoices` / `invoice_line_items` | FeinFood's historical invoice record, parsed from the PDFs in `invoices/`, including payment status (`is_paid`, `paid_date`). |
| `orders` / `order_items` | FeinFood orders placed through the app. |
| `fatihet_invoices` / `fatihet_invoice_items` | Fatih Et (meat) invoices, uploaded and parsed through the app. No link to `products` — meat is ordered and priced separately, not catalogued. |
| `metro_invoices` / `metro_invoice_items` | METRO invoices, uploaded and parsed through the app. No link to `products` — METRO's prices change too often to catalog. Line items are best-effort; the Netto/USt./Brutto totals are read straight from the invoice's own summary and are reliable. |

`invoices_dump.sql` is a plain-text SQL export of `invoices.db`, regenerated with `sqlite3 invoices.db ".dump" > invoices_dump.sql` — keep it in sync any time you change the schema directly.

## Using the app

### 1. Product catalog

The top-level table lists every FeinFood product with its current price, unit, VAT rate, the invoice/date it was first seen on, and which vendor order sheet ("Bestellliste") it maps to. Search and filter by name, number, or bestellliste. If a product's price ever changed across invoices, it shows up in the "products with more than one price on record" callout with each price era listed separately.

### 2. Monthly inventory cost

At the top of the page, pick a month to see total Netto/Brutto spend broken down by source — Feinfood Bestellliste, Nachoking Bestellliste, Imported Invoices, Fatihet, and Metro — plus a grand total across all of them for that month.

### 3. Manage products

Under **Manage Products**:
- **Add Product** — register a new catalog item (name, unit, price, VAT, effective date, optional bestellliste link).
- **Edit Product** — pick any product and update its details, including which vendor order sheet it maps to.
- **Delete This Product** — removes a product, with a confirmation dialog that warns you how many historical invoice/order records reference it before you confirm.

### 4. Place an inventory order (FeinFood)

Under **Place Inventory Order**, pick a vendor (🧊 Feinfood or 🌮 Nachoking) to see every product mapped to that bestellliste with an editable quantity column. Live Netto/USt./Brutto totals update as you type. A delivery/energy surcharge specific to that vendor is added automatically (this mirrors what the vendor actually bills — see *Design notes* on the ERD page).

Clicking **Place Order**:
1. Records the order and its line items in the database.
2. Generates two files, saved to `orders/`:
   - **Vendor order file** — the vendor's own bestellliste template, untouched except your ordered quantities filled into the `Bestellung` column. Send this straight to the vendor.
   - **Order confirmation** — a document mirroring the vendor's invoice layout (same columns, same Netto → USt. → Brutto footer), for your own records and for the invoice-verification step below.

**Order history** lists every past order with download buttons for both files, and a delete button (with confirmation) that removes the order and its generated files.

### 5. Fatihet Meat Invoices

Under **Fatihet Meat Invoices**, upload a Fatih Et invoice PDF. The app parses it, shows a preview (date, each meat item with quantity/unit/price, and Netto/USt./Brutto totals), and — on **Import this invoice** — records it. Re-uploading the same invoice is automatically rejected as a duplicate. **Fatihet invoice history** lists every imported invoice with its totals, a per-invoice item view, and a delete button. Nothing here touches the product catalog.

### 6. Metro Invoices

Under **Metro Invoices**, upload a METRO invoice PDF the same way. METRO's invoices are a dense point-of-sale ledger (category headers, per-item deposit lines, quantity discounts all interleaved), so the item breakdown is best-effort — but the Netto/USt./Brutto totals are parsed from the invoice's own fixed-format summary block and are reliable. Same import/duplicate-detection/history/delete pattern as Fatihet. Nothing here touches the product catalog either.

### 7. Verify a vendor invoice (FeinFood)

Under **Verify Vendor Invoice**: pick a past order (or upload an order confirmation `.xlsx` directly) and upload the vendor's real invoice PDF. The app parses the PDF, matches every line item against what was ordered, and flags:

- 🚨 **Price mismatch** — vendor charged a different unit price than agreed
- ⚠️ **Quantity mismatch** — billed quantity differs from what was ordered
- ⚠️ **Not ordered / unexpected charge** — something on the invoice you never ordered
- ❌ **Missing from invoice** — something you ordered that was never billed

Netto/USt./Brutto totals from the invoice are compared against the order's own totals so you can catch a discrepancy even if every line item looks fine individually.

## Deployment

The app is set up to deploy on [Railway](https://railway.com): the `Procfile` starts Streamlit bound to Railway's `$PORT`, and the [Configuration](#configuration) env vars (`DB_PATH`, `ORDERS_DIR`, etc.) let you point everything at a persistent Volume (e.g. mounted at `/data`) so the database and generated order files survive redeploys — SQLite is a file, and without a mounted volume it's wiped on every deploy. The app doesn't auto-create its schema, so the volume needs to be seeded once with a copy of `invoices.db` (e.g. via `railway volume browse` or piping in `invoices_dump.sql`).

## Notes

- `invoices.db.bak-*` files are point-in-time backups taken before risky schema migrations — safe to delete once you've confirmed the current database is healthy.
- `invoices/`, `fatihet/`, `metro/`, and `orders/` hold real business data and are gitignored — only `bestellliste/` (blank vendor templates, no transaction data) is committed, since `order_utils.py` needs it at runtime.
- The `venv/` and `__pycache__/` directories are local build artifacts, not part of the project source.
