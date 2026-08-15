# Rooster Inventory

A Streamlit app that turns a folder of German wholesale-food invoices (FeinFood Express GmbH) into a queryable product catalog, matches that catalog against the vendor's own order sheets ("Bestellliste"), and lets you place inventory orders, generate vendor-ready order files, and verify the vendor's invoice against what you actually ordered — all backed by a single SQLite database.

**Schema reference:** [Entity Relationship Diagram](https://claude.ai/code/artifact/216590c8-d043-4ae2-9828-9d2d088dcb73)

## Why this exists

Rooster Fried Chicken / Habib RFC Mannheim orders freezer goods, packaging, and supplies from FeinFood Express, who circulate two order-taking spreadsheets ("fein food .xlsx" and "nacho king.xlsx"). Historically:

- Prices lived only in scanned PDF invoices, with no easy way to see what a product costs or whether a price ever changed.
- Placing an order meant manually retyping quantities into the vendor's spreadsheet.
- There was no way to check whether the vendor's invoice actually matched what was ordered, at the price that was agreed.

This project extracts the invoice history into a real database, keeps price changes as a version history rather than overwriting them, and wraps the whole ordering round-trip (catalog → order → vendor file → invoice check) in one app.

## Project structure

```
Rooster/
├── app.py                  # Streamlit app — all UI lives here
├── order_utils.py          # Vendor order file + order confirmation file generation
├── invoice_parser.py       # Parses vendor invoice PDFs (pdfplumber + regex)
├── compare_utils.py        # Order-confirmation parsing + discrepancy comparison
├── invoices.db              # SQLite database (source of truth)
├── invoices_dump.sql       # Plain-text SQL dump of invoices.db, regenerate after schema changes
├── requirements.txt        # Pinned Python dependencies
├── venv/                   # Project-local virtualenv (not committed)
├── invoices/                # Source PDFs — FeinFood Express invoices (RG####.pdf)
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

## What's in the database

Everything lives in `invoices.db` (6 tables — see the [ERD](https://claude.ai/code/artifact/216590c8-d043-4ae2-9828-9d2d088dcb73) for the full picture):

| Table | Purpose |
|---|---|
| `products` | The catalog. One row per product **price era** — a product gets a *new* row when its price changes, not an overwrite, so price history is never lost. |
| `bestellliste_map` | Links a catalog product to the vendor's own order-sheet row (their sheet uses a different, inconsistent numbering scheme, so this is a soft link keyed by matched item name). |
| `invoices` / `invoice_line_items` | The historical record, parsed from the PDFs in `invoices/`. |
| `orders` / `order_items` | Orders placed through the app. |

`invoices_dump.sql` is a plain-text SQL export of `invoices.db`, regenerated with `sqlite3 invoices.db ".dump" > invoices_dump.sql` — keep it in sync any time you change the schema directly.

## Using the app

### 1. Product catalog

The top-level table lists every product with its current price, unit, VAT rate, the invoice/date it was first seen on, and which vendor order sheet ("Bestellliste") it maps to. Search and filter by name, number, or bestellliste. If a product's price ever changed across invoices, it shows up in the "products with more than one price on record" callout with each price era listed separately.

### 2. Monthly inventory cost by vendor

At the top of the page, pick a month to see total Netto/Brutto spend broken down by vendor (Feinfood vs. Nachoking) for every order placed that month.

### 3. Manage products

Under **Manage Products**:
- **Add Product** — register a new catalog item (name, unit, price, VAT, effective date, optional bestellliste link).
- **Edit Product** — pick any product and update its details, including which vendor order sheet it maps to.
- **Delete This Product** — removes a product, with a confirmation dialog that warns you how many historical invoice/order records reference it before you confirm.

### 4. Place an inventory order

Under **Place Inventory Order**, pick a vendor (🧊 Feinfood or 🌮 Nachoking) to see every product mapped to that bestellliste with an editable quantity column. Live Netto/USt./Brutto totals update as you type. A delivery/energy surcharge specific to that vendor is added automatically (this mirrors what the vendor actually bills — see *Design notes* on the ERD page).

Clicking **Place Order**:
1. Records the order and its line items in the database.
2. Generates two files, saved to `orders/`:
   - **Vendor order file** — the vendor's own bestellliste template, untouched except your ordered quantities filled into the `Bestellung` column. Send this straight to the vendor.
   - **Order confirmation** — a document mirroring the vendor's invoice layout (same columns, same Netto → USt. → Brutto footer), for your own records and for the invoice-verification step below.

**Order history** lists every past order with download buttons for both files, and a delete button (with confirmation) that removes the order and its generated files.

### 5. Verify a vendor invoice

Under **Verify Vendor Invoice**: pick a past order (or upload an order confirmation `.xlsx` directly) and upload the vendor's real invoice PDF. The app parses the PDF, matches every line item against what was ordered, and flags:

- 🚨 **Price mismatch** — vendor charged a different unit price than agreed
- ⚠️ **Quantity mismatch** — billed quantity differs from what was ordered
- ⚠️ **Not ordered / unexpected charge** — something on the invoice you never ordered
- ❌ **Missing from invoice** — something you ordered that was never billed

Netto/USt./Brutto totals from the invoice are compared against the order's own totals so you can catch a discrepancy even if every line item looks fine individually.

## Notes

- `invoices.db.bak-*` files are point-in-time backups taken before risky schema migrations — safe to delete once you've confirmed the current database is healthy.
- The `venv/` and `__pycache__/` directories are local build artifacts, not part of the project source.
