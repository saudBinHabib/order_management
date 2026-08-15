import os
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import compare_utils
import invoice_ingest
import invoice_parser
import order_utils

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "invoices.db"))
INVOICES_DIR = Path(os.environ.get("INVOICES_DIR", Path(__file__).parent / "invoices"))

st.set_page_config(page_title="Rooster's Inventory Management", page_icon="🍔", layout="wide")


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
    conn.close()
    invoices_df["source"] = "Imported Invoices"

    df = pd.concat([orders_df, invoices_df], ignore_index=True)
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
        (1 if paid else 0, date.today().isoformat() if paid else None, invoice_number),
    )
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


SURCHARGE_PRODUCT_NUMBER = {
    "Feinfood Bestellliste": "9",     # FeinFood Liefer- und Energiezuschlag
    "Nachoking Bestellliste": "214",  # NachoKings Liefer- und Energiezuschlag
}


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


def place_order(bestellliste_name: str, order_df: pd.DataFrame):
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

    order_date = date.today().isoformat()
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


products, line_items = load_data()

st.title("🍔 Rooster's Inventory Management")
st.caption("Extracted from invoices in the `invoices/` folder.")

st.subheader("📅 Monthly Inventory Cost")
st.caption("Covers both orders placed through this app and invoices imported from vendor PDFs.")
monthly_costs = load_monthly_costs()
if monthly_costs.empty:
    st.caption("No orders or imported invoices yet.")
else:
    months = sorted(monthly_costs["month"].unique(), reverse=True)
    month_labels = {m: date.fromisoformat(m + "-01").strftime("%B %Y") for m in months}
    selected_month = st.selectbox(
        "Select month", months, format_func=lambda m: month_labels[m], key="monthly_cost_month"
    )

    month_df = monthly_costs[monthly_costs["month"] == selected_month]
    by_source = (
        month_df.groupby("source")[["netto_total", "vat_total", "brutto_total"]]
        .sum()
        .reindex(["Feinfood Bestellliste", "Nachoking Bestellliste", "Imported Invoices"])
        .fillna(0.0)
        .reset_index()
    )

    v_cols = st.columns(len(by_source) + 1)
    for col, r in zip(v_cols, by_source.itertuples()):
        col.markdown(f"**{r.source}**")
        col.metric("Netto", f"€{r.netto_total:,.2f}")
        col.metric("Brutto", f"€{r.brutto_total:,.2f}")

    total_row = pd.DataFrame([{
        "source": "Total (all sources)",
        "netto_total": month_df["netto_total"].sum(),
        "vat_total": month_df["vat_total"].sum(),
        "brutto_total": month_df["brutto_total"].sum(),
    }])
    with v_cols[-1]:
        st.markdown(f"**Total — {month_labels[selected_month]}**")
        st.metric("Netto", f"€{total_row['netto_total'].iloc[0]:,.2f}")
        st.metric("Brutto", f"€{total_row['brutto_total'].iloc[0]:,.2f}")

    st.dataframe(
        pd.concat([by_source, total_row], ignore_index=True).rename(columns={
            "source": "Source", "netto_total": "Netto (EUR)",
            "vat_total": "USt. (EUR)", "brutto_total": "Brutto (EUR)",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Netto (EUR)": st.column_config.NumberColumn(format="€%.2f"),
            "USt. (EUR)": st.column_config.NumberColumn(format="€%.2f"),
            "Brutto (EUR)": st.column_config.NumberColumn(format="€%.2f"),
        },
    )

st.divider()
st.header("📥 Import Invoices")
st.caption("Scan the `invoices/` folder for PDFs that aren't in the system yet, and load them in.")

if st.button("🔍 Scan invoices/ folder for new invoices"):
    st.session_state["invoice_scan_results"] = invoice_ingest.list_unimported_pdfs(INVOICES_DIR)

scan_results = st.session_state.get("invoice_scan_results")
if scan_results is not None:
    new_ones = [r for r in scan_results if "parsed" in r and not r["already_imported"]]
    already = [r for r in scan_results if "parsed" in r and r["already_imported"]]
    errors = [r for r in scan_results if "error" in r]

    st.caption(
        f"{len(new_ones)} new invoice(s) found · {len(already)} already in the system"
        + (f" · {len(errors)} failed to parse" if errors else "")
    )

    if errors:
        for r in errors:
            st.error(f"{r['path'].name}: {r['error']}")

    if new_ones:
        preview_rows = []
        for r in new_ones:
            p = r["parsed"]
            preview_rows.append({
                "File": r["path"].name,
                "Invoice #": p["invoice_number"],
                "Date": p["invoice_date"],
                "Customer": p["customer_name"],
                "Items": len(p["items"]),
                "Netto (EUR)": p["netto_total"],
                "Brutto (EUR)": p["brutto_total"],
            })
        st.dataframe(
            pd.DataFrame(preview_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Netto (EUR)": st.column_config.NumberColumn(format="€%.2f"),
                "Brutto (EUR)": st.column_config.NumberColumn(format="€%.2f"),
            },
        )

        payment_choice = st.radio(
            "Mark these invoices as", ["Unpaid (still open)", "Paid"], horizontal=True,
            key="import_payment_choice",
        )
        if st.button(f"📥 Import {len(new_ones)} invoice(s)", type="primary"):
            imported, failed = 0, []
            for r in new_ones:
                ok, msg = invoice_ingest.ingest_invoice(r["parsed"], mark_paid=(payment_choice == "Paid"))
                if ok:
                    imported += 1
                else:
                    failed.append(msg)
            load_data.clear()
            load_invoices_by_status.clear()
            load_monthly_costs.clear()
            st.session_state["invoice_scan_results"] = None
            st.session_state["last_import_result"] = {"imported": imported, "failed": failed}
            st.rerun()
    elif not errors:
        st.info("Nothing new to import — every PDF in `invoices/` is already in the system.")

last_import = st.session_state.get("last_import_result")
if last_import:
    st.success(f"Imported {last_import['imported']} invoice(s).")
    if last_import["failed"]:
        st.warning("\n".join(last_import["failed"]))

st.divider()
st.header("💰 Invoices — Paid & Unpaid")

all_invoices = load_invoices_by_status()

if all_invoices.empty:
    st.info("No invoices on record yet.")
else:
    unpaid_df = all_invoices[all_invoices["is_paid"] == 0]
    paid_df = all_invoices[all_invoices["is_paid"] == 1]

    @st.dialog("Change payment status?")
    def confirm_payment_status_dialog(invoice_number, brutto_total, target_paid):
        action = "mark as PAID" if target_paid else "mark as UNPAID"
        st.write(f"{action.capitalize()}: invoice **{invoice_number}** (€{brutto_total:,.2f} brutto)?")
        st.caption("You can change this back at any time.")
        c_yes, c_no = st.columns(2)
        if c_yes.button("Yes, confirm", type="primary", use_container_width=True, key=f"pay_yes_{invoice_number}_{target_paid}"):
            set_invoice_paid_status(invoice_number, target_paid)
            load_invoices_by_status.clear()
            st.rerun()
        if c_no.button("No, cancel", use_container_width=True, key=f"pay_no_{invoice_number}_{target_paid}"):
            st.rerun()

    def render_invoice_table(df, make_paid: bool):
        if df.empty:
            st.caption("Nothing here.")
            return
        header = st.columns([1, 1, 2, 1.2, 1.2, 1.2, 1.3])
        labels = ["Invoice #", "Date", "Customer", "Netto", "USt.", "Brutto", ""]
        for col, label in zip(header, labels):
            col.markdown(f"**{label}**")
        for row in df.itertuples():
            c = st.columns([1, 1, 2, 1.2, 1.2, 1.2, 1.3])
            c[0].write(row.invoice_number)
            c[1].write(row.invoice_date)
            c[2].write(row.customer_name)
            c[3].write(f"€{row.netto_total:,.2f}")
            c[4].write(f"€{row.vat_total:,.2f}")
            c[5].write(f"€{row.brutto_total:,.2f}")
            btn_label = "✅ Mark paid" if make_paid else "↩️ Mark unpaid"
            if c[6].button(btn_label, key=f"toggle_paid_{row.invoice_number}_{make_paid}"):
                confirm_payment_status_dialog(row.invoice_number, row.brutto_total, make_paid)

    tab_unpaid, tab_paid = st.tabs([f"🔴 Unpaid ({len(unpaid_df)})", f"🟢 Paid ({len(paid_df)})"])

    with tab_unpaid:
        if unpaid_df.empty:
            st.info("No unpaid invoices on record.")
        else:
            p1, p2, p3 = st.columns(3)
            p1.metric("Open invoices", len(unpaid_df))
            p2.metric("Total pending (Netto)", f"€{unpaid_df['netto_total'].sum():,.2f}")
            p3.metric("Total pending (Brutto)", f"€{unpaid_df['brutto_total'].sum():,.2f}")
            render_invoice_table(unpaid_df, make_paid=True)

    with tab_paid:
        if paid_df.empty:
            st.info("No paid invoices on record.")
        else:
            p1, p2 = st.columns(2)
            p1.metric("Paid invoices", len(paid_df))
            p2.metric("Total paid (Brutto)", f"€{paid_df['brutto_total'].sum():,.2f}")
            render_invoice_table(paid_df, make_paid=False)

st.divider()

# Flag products that have more than one price entry (a real price change)
price_counts = products.groupby("product_number")["unit_price"].nunique()
changed_numbers = price_counts[price_counts > 1].index.tolist()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Distinct products", products["product_number"].nunique())
col2.metric("Product price entries", len(products))
col3.metric("Products with price changes", len(changed_numbers))
col4.metric("Matched to a bestellliste", products["bestellliste"].notna().sum())

search = st.text_input("Search by product name or number", "")
bestellliste_options = ["All"] + sorted(products["bestellliste"].dropna().unique().tolist()) + ["(no match)"]
bestellliste_filter = st.selectbox("Filter by bestellliste", bestellliste_options)

filtered = products
if search:
    mask = (
        products["product_name"].str.contains(search, case=False, na=False)
        | products["product_number"].str.contains(search, case=False, na=False)
    )
    filtered = filtered[mask]

if bestellliste_filter == "(no match)":
    filtered = filtered[filtered["bestellliste"].isna()]
elif bestellliste_filter != "All":
    filtered = filtered[filtered["bestellliste"] == bestellliste_filter]

st.subheader("All items and prices")
display_df = filtered.rename(
    columns={
        "product_number": "Item #",
        "product_name": "Product",
        "unit": "Unit",
        "unit_price": "Unit Price (EUR)",
        "vat_percent": "VAT %",
        "effective_date": "Effective Date",
        "source_invoice": "Source Invoice",
        "bestellliste": "Bestellliste",
    }
)[
    [
        "Item #",
        "Product",
        "Unit",
        "Unit Price (EUR)",
        "VAT %",
        "Effective Date",
        "Source Invoice",
        "Bestellliste",
    ]
]

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Unit Price (EUR)": st.column_config.NumberColumn(format="€%.2f"),
        "VAT %": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

if changed_numbers:
    st.subheader("⚠️ Products with more than one price on record")
    changed_df = products[products["product_number"].isin(changed_numbers)]
    st.dataframe(changed_df, use_container_width=True, hide_index=True)
else:
    st.info("No product currently has more than one recorded price — every item has been billed at a consistent unit price across all invoices.")

with st.expander("📄 Raw invoice line items"):
    st.dataframe(line_items, use_container_width=True, hide_index=True)

st.divider()
st.header("🛠️ Manage Products")

BESTELLLISTE_CHOICES = ["(none)", "Feinfood Bestellliste", "Nachoking Bestellliste"]

add_tab, edit_tab = st.tabs(["➕ Add Product", "✏️ Edit Product"])

with add_tab:
    with st.form("add_product_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        new_number = c1.text_input("Item # (product number)")
        new_name = c2.text_input("Product name")
        c3, c4, c5 = st.columns(3)
        new_unit = c3.text_input("Unit (e.g. Karton, Beutel)")
        new_price = c4.number_input("Unit price (EUR)", min_value=0.0, step=0.01, format="%.2f")
        new_vat = c5.number_input("VAT %", min_value=0.0, max_value=100.0, value=7.0, step=0.01, format="%.2f")
        c6, c7 = st.columns(2)
        new_date = c6.date_input("Effective date", value=date.today())
        new_bestellliste = c7.selectbox("Bestellliste", BESTELLLISTE_CHOICES)
        new_matched_name = None
        if new_bestellliste != "(none)":
            new_matched_name = st.text_input(
                "Matching item name in that bestellliste file (used to fill vendor order templates)",
                value=new_name,
            )

        submitted = st.form_submit_button("Add Product", type="primary")
        if submitted:
            if not new_number or not new_name or new_price <= 0:
                st.error("Item #, Product name, and a Unit price greater than 0 are required.")
            else:
                ok, msg = add_product(
                    new_number.strip(), new_name.strip(), new_unit.strip() or None, float(new_price),
                    float(new_vat), new_date.isoformat(),
                    None if new_bestellliste == "(none)" else new_bestellliste, new_matched_name,
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    load_data.clear()
                    load_orderable.clear()
                    st.rerun()

with edit_tab:
    conn = sqlite3.connect(DB_PATH)
    all_products = pd.read_sql_query(
        """SELECT p.product_id, p.product_number, p.product_name, p.unit, p.unit_price, p.vat_percent,
                  p.effective_date, p.source_invoice, m.bestellliste, m.matched_item_name
           FROM products p LEFT JOIN bestellliste_map m ON m.product_id = p.product_id
           ORDER BY p.product_number, p.effective_date""",
        conn,
    )
    conn.close()

    if all_products.empty:
        st.caption("No products yet.")
    else:
        labels = [
            f"{r.product_number} — {r.product_name} (€{r.unit_price:.2f}, since {r.effective_date})"
            for r in all_products.itertuples()
        ]
        idx = st.selectbox("Choose a product to edit", range(len(labels)), format_func=lambda i: labels[i])
        row = all_products.iloc[idx]

        with st.form("edit_product_form"):
            st.caption(f"Item # {row['product_number']} · product_id {row['product_id']} · source: {row['source_invoice']}")
            e1, e2 = st.columns(2)
            edit_name = e1.text_input("Product name", value=row["product_name"])
            edit_unit = e2.text_input("Unit", value=row["unit"] or "")
            e3, e4 = st.columns(2)
            edit_price = e3.number_input("Unit price (EUR)", min_value=0.0, step=0.01, format="%.2f", value=float(row["unit_price"]))
            edit_vat = e4.number_input(
                "VAT %", min_value=0.0, max_value=100.0, step=0.01, format="%.2f",
                value=float(row["vat_percent"]) if pd.notna(row["vat_percent"]) else 0.0,
            )
            e5, e6 = st.columns(2)
            edit_date = e5.date_input("Effective date", value=date.fromisoformat(row["effective_date"]))
            current_bl = row["bestellliste"] if pd.notna(row["bestellliste"]) else "(none)"
            edit_bestellliste = e6.selectbox("Bestellliste", BESTELLLISTE_CHOICES, index=BESTELLLISTE_CHOICES.index(current_bl))
            edit_matched_name = None
            if edit_bestellliste != "(none)":
                default_matched = row["matched_item_name"] if pd.notna(row["matched_item_name"]) else row["product_name"]
                edit_matched_name = st.text_input("Matching item name in that bestellliste file", value=default_matched)

            save = st.form_submit_button("Save Changes", type="primary")
            if save:
                if not edit_name or edit_price <= 0:
                    st.error("Product name and a Unit price greater than 0 are required.")
                else:
                    ok, msg = update_product(
                        int(row["product_id"]), edit_name.strip(), edit_unit.strip() or None, float(edit_price),
                        float(edit_vat), edit_date.isoformat(),
                        None if edit_bestellliste == "(none)" else edit_bestellliste, edit_matched_name,
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        load_data.clear()
                        load_orderable.clear()
                        st.rerun()

        @st.dialog("Delete product?")
        def confirm_delete_dialog(product_id, product_number, product_name):
            invoice_count, order_count = get_product_usage(product_id)
            st.write(f"Delete **{product_number} — {product_name}**?")
            if invoice_count or order_count:
                st.warning(
                    f"This product appears on {invoice_count} historical invoice line item(s) and "
                    f"{order_count} past order item(s). Those records will no longer show it "
                    "(the underlying rows aren't deleted, they'll just no longer join to a product)."
                )
            st.caption("This cannot be undone.")
            c_yes, c_no = st.columns(2)
            if c_yes.button("Yes, delete", type="primary", use_container_width=True):
                ok, msg = delete_product(product_id)
                load_data.clear()
                load_orderable.clear()
                st.rerun()
            if c_no.button("No, cancel", use_container_width=True):
                st.rerun()

        if st.button("🗑️ Delete This Product", type="secondary"):
            confirm_delete_dialog(int(row["product_id"]), row["product_number"], row["product_name"])

st.divider()
st.header("📦 Place Inventory Order")

if "order_source" not in st.session_state:
    st.session_state["order_source"] = None

btn_col1, btn_col2 = st.columns(2)
if btn_col1.button("🧊 Feinfood", use_container_width=True, type="primary" if st.session_state["order_source"] == "Feinfood Bestellliste" else "secondary"):
    st.session_state["order_source"] = "Feinfood Bestellliste"
if btn_col2.button("🌮 Nachoking", use_container_width=True, type="primary" if st.session_state["order_source"] == "Nachoking Bestellliste" else "secondary"):
    st.session_state["order_source"] = "Nachoking Bestellliste"

selected_source = st.session_state["order_source"]

if selected_source:
    st.subheader(f"Order from {selected_source}")
    orderable = load_orderable(selected_source)

    edit_df = orderable.copy()
    edit_df["Order Qty"] = 0.0

    display_edit_df = edit_df.rename(
        columns={
            "product_number": "Item #",
            "product_name": "Product",
            "unit": "Unit",
            "unit_price": "Unit Price (EUR)",
            "vat_percent": "VAT %",
        }
    )[["Item #", "Product", "Unit", "Unit Price (EUR)", "VAT %", "Order Qty"]]

    edited = st.data_editor(
        display_edit_df,
        use_container_width=True,
        hide_index=True,
        disabled=["Item #", "Product", "Unit", "Unit Price (EUR)", "VAT %"],
        column_config={
            "Unit Price (EUR)": st.column_config.NumberColumn(format="€%.2f"),
            "VAT %": st.column_config.NumberColumn(format="%.2f%%"),
            "Order Qty": st.column_config.NumberColumn(min_value=0, step=1),
        },
        key=f"editor_{selected_source}",
    )

    # merge edited quantities back with raw (unrenamed) columns needed for order placement
    order_df = orderable.copy()
    order_df["Order Qty"] = edited["Order Qty"].values

    preview_items = [
        {"quantity": float(q), "unit_price": float(p), "vat_percent": float(v)}
        for q, p, v in zip(order_df["Order Qty"], order_df["unit_price"], order_df["vat_percent"])
        if q > 0
    ]
    preview_surcharge = get_surcharge_product(selected_source) if preview_items else None
    preview_calc_items = list(preview_items)
    if preview_surcharge:
        preview_calc_items.append({"quantity": 1.0, "unit_price": preview_surcharge["unit_price"], "vat_percent": preview_surcharge["vat_percent"]})
    preview_netto, preview_vat, preview_brutto = order_utils.compute_totals(preview_calc_items) if preview_calc_items else (0.0, 0.0, 0.0)

    if preview_surcharge:
        st.caption(
            f"➕ A delivery/energy surcharge — **{preview_surcharge['product_name']}** "
            f"(€{preview_surcharge['unit_price']:.2f}, {preview_surcharge['vat_percent']:.0f}% VAT) — "
            f"will be added automatically when this order is placed."
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Items selected", len(preview_items) + (1 if preview_surcharge else 0))
    m2.metric("Netto", f"€{preview_netto:,.2f}")
    m3.metric("USt.", f"€{preview_vat:,.2f}")
    m4.metric("Brutto", f"€{preview_brutto:,.2f}")

    if st.button("✅ Place Order", type="primary", disabled=len(preview_items) == 0):
        result = place_order(selected_source, order_df)
        if result is None:
            st.warning("Enter a quantity greater than 0 for at least one item.")
        else:
            load_data.clear()
            load_orderable.clear()
            load_monthly_costs.clear()
            st.session_state["last_order_result"] = result
            st.rerun()

    last_result = st.session_state.get("last_order_result")
    if last_result and last_result["bestellliste"] == selected_source:
        surcharge_note = f" (includes {last_result['surcharge']['product_name']})" if last_result["surcharge"] else ""
        st.success(
            f"Order #{last_result['order_id']} placed on {last_result['order_date']} — "
            f"{last_result['item_count']} item(s){surcharge_note}. "
            f"Netto: €{last_result['netto_total']:,.2f} · USt.: €{last_result['vat_total']:,.2f} · "
            f"Brutto (estimated order value): €{last_result['brutto_total']:,.2f}"
        )
        dl1, dl2 = st.columns(2)
        if last_result["vendor_path"].exists():
            with open(last_result["vendor_path"], "rb") as f:
                dl1.download_button(
                    "⬇️ Download vendor order file (bestellliste format)",
                    f.read(),
                    file_name=last_result["vendor_path"].name,
                    use_container_width=True,
                    key="last_order_vendor_dl",
                )
        if last_result["invoice_path"].exists():
            with open(last_result["invoice_path"], "rb") as f:
                dl2.download_button(
                    "⬇️ Download order confirmation (invoice pattern)",
                    f.read(),
                    file_name=last_result["invoice_path"].name,
                    use_container_width=True,
                    key="last_order_invoice_dl",
                )

with st.expander("🧾 Order history", expanded=True):
    conn = sqlite3.connect(DB_PATH)
    orders_hist = pd.read_sql_query(
        """SELECT order_id, order_date, bestellliste, netto_total, vat_total, brutto_total,
                  vendor_order_file, invoice_pattern_file, created_at
           FROM orders ORDER BY order_id DESC""",
        conn,
    )
    conn.close()

    if orders_hist.empty:
        st.caption("No orders placed yet.")
    else:
        @st.dialog("Delete order?")
        def confirm_delete_order_dialog(order_id, order_date, bestellliste, brutto_total):
            st.write(f"Delete order **#{order_id}** ({bestellliste}, {order_date}, €{brutto_total:,.2f} brutto)?")
            st.warning(
                "This removes the order and its line items from history, and deletes the two "
                "generated files (vendor order + order confirmation) from disk."
            )
            st.caption("This cannot be undone.")
            c_yes, c_no = st.columns(2)
            if c_yes.button("Yes, delete", type="primary", use_container_width=True, key=f"order_del_yes_{order_id}"):
                delete_order(order_id)
                load_monthly_costs.clear()
                st.rerun()
            if c_no.button("No, cancel", use_container_width=True, key=f"order_del_no_{order_id}"):
                st.rerun()

        header = st.columns([0.6, 1, 1.6, 1, 1, 1, 1.6, 1.6, 0.6])
        for col, label in zip(
            header,
            ["#", "Date", "Bestellliste", "Netto", "USt.", "Brutto", "Vendor file", "Order confirmation", ""],
        ):
            col.markdown(f"**{label}**")

        for row in orders_hist.itertuples():
            c = st.columns([0.6, 1, 1.6, 1, 1, 1, 1.6, 1.6, 0.6])
            c[0].write(row.order_id)
            c[1].write(row.order_date)
            c[2].write(row.bestellliste)
            c[3].write(f"€{row.netto_total:,.2f}")
            c[4].write(f"€{row.vat_total:,.2f}")
            c[5].write(f"€{row.brutto_total:,.2f}")

            vendor_path = Path(row.vendor_order_file) if row.vendor_order_file else None
            if vendor_path and vendor_path.exists():
                with open(vendor_path, "rb") as f:
                    c[6].download_button(
                        "⬇️ Vendor file",
                        f.read(),
                        file_name=vendor_path.name,
                        key=f"vendor_dl_{row.order_id}",
                        use_container_width=True,
                    )
            else:
                c[6].caption("missing")

            invoice_path = Path(row.invoice_pattern_file) if row.invoice_pattern_file else None
            if invoice_path and invoice_path.exists():
                with open(invoice_path, "rb") as f:
                    c[7].download_button(
                        "⬇️ Order confirmation",
                        f.read(),
                        file_name=invoice_path.name,
                        key=f"invoice_dl_{row.order_id}",
                        use_container_width=True,
                    )
            else:
                c[7].caption("missing")

            if c[8].button("🗑️", key=f"order_del_{row.order_id}", help="Delete this order"):
                confirm_delete_order_dialog(row.order_id, row.order_date, row.bestellliste, row.brutto_total)

st.divider()
st.header("🔍 Verify Vendor Invoice")
st.caption(
    "Compare your order confirmation against the vendor's actual invoice PDF to catch "
    "wrong prices, wrong quantities, or unexpected charges."
)

conn = sqlite3.connect(DB_PATH)
orders_for_select = pd.read_sql_query(
    "SELECT order_id, order_date, bestellliste, invoice_pattern_file FROM orders ORDER BY order_id DESC",
    conn,
)
conn.close()

source_choice = st.radio(
    "Order confirmation source",
    ["Select a past order", "Upload order confirmation file"],
    horizontal=True,
)

order_data = None
if source_choice == "Select a past order":
    if orders_for_select.empty:
        st.caption("No orders placed yet — place one above, or upload a file instead.")
    else:
        labels = [f"#{r.order_id} — {r.bestellliste} — {r.order_date}" for r in orders_for_select.itertuples()]
        selected_idx = st.selectbox("Choose an order", range(len(labels)), format_func=lambda i: labels[i])
        chosen = orders_for_select.iloc[selected_idx]
        conf_path = Path(chosen["invoice_pattern_file"]) if chosen["invoice_pattern_file"] else None
        if conf_path and conf_path.exists():
            order_data = compare_utils.parse_order_confirmation_xlsx(conf_path)
        else:
            st.warning("Order confirmation file for this order is missing on disk.")
else:
    order_file = st.file_uploader("Order confirmation (.xlsx)", type=["xlsx"], key="order_conf_upload")
    if order_file:
        order_data = compare_utils.parse_order_confirmation_xlsx(order_file)

invoice_file = st.file_uploader("Vendor invoice (.pdf)", type=["pdf"], key="vendor_invoice_upload")

if st.button("🔍 Compare", type="primary", disabled=(order_data is None or invoice_file is None)):
    invoice_data = invoice_parser.parse_invoice_pdf(invoice_file)
    comparison = compare_utils.compare_order_to_invoice(order_data["items"], invoice_data["items"])
    comp_df = pd.DataFrame(comparison)

    ok_count = int((comp_df["status"] == "✅ OK").sum())
    issue_count = len(comp_df) - ok_count
    total_diff = comp_df["diff"].sum()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Items compared", len(comp_df))
    m2.metric("Matching", ok_count)
    m3.metric("Flagged", issue_count)
    m4.metric("Net value difference", f"€{total_diff:,.2f}")

    if issue_count > 0:
        st.warning(f"{issue_count} discrepanc{'y' if issue_count == 1 else 'ies'} found — review below.")
    else:
        st.success("No discrepancies found. Invoice matches the order.")

    display_comp = comp_df.rename(columns={
        "product_number": "Item #", "product_name": "Product", "status": "Status",
        "ordered_qty": "Ordered Qty", "invoiced_qty": "Invoiced Qty",
        "ordered_price": "Ordered Price", "invoiced_price": "Invoiced Price",
        "ordered_total": "Ordered Total", "invoiced_total": "Invoiced Total",
        "diff": "Diff (EUR)", "notes": "Notes",
    })
    st.dataframe(display_comp, use_container_width=True, hide_index=True)

    st.subheader("Invoice totals vs order")
    order_netto, order_vat, order_brutto = order_utils.compute_totals(order_data["items"])
    invoice_netto = invoice_data["netto_total"]
    invoice_brutto = invoice_data["brutto_total"]
    invoice_vat = (invoice_brutto - invoice_netto) if (invoice_netto is not None and invoice_brutto is not None) else None

    t1, t2, t3 = st.columns(3)
    t1.metric(
        "Netto", f"€{invoice_netto:,.2f}" if invoice_netto is not None else "n/a",
        delta=f"€{(invoice_netto - order_netto):,.2f}" if invoice_netto is not None else None,
    )
    t2.metric(
        "USt.", f"€{invoice_vat:,.2f}" if invoice_vat is not None else "n/a",
        delta=f"€{(invoice_vat - order_vat):,.2f}" if invoice_vat is not None else None,
    )
    t3.metric(
        "Brutto", f"€{invoice_brutto:,.2f}" if invoice_brutto is not None else "n/a",
        delta=f"€{(invoice_brutto - order_brutto):,.2f}" if invoice_brutto is not None else None,
    )
