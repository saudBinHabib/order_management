import re

import pdfplumber

ITEM_RE = re.compile(
    r"^(?P<pos>\d+)\s+"
    r"(?P<qty>\d+,\d{3})\s+"
    r"(?P<unit>[A-Za-zÀ-ÿ]+)\s+"
    r"(?P<nummer>\d+)\s+"
    r"(?P<text>.+?)\s+"
    r"(?P<price>\d{1,3}(?:\.\d{3})*,\d{3})\s+"
    r"(?P<total>\d{1,3}(?:\.\d{3})*,\d{2})$"
)

VAT_LINE_RE = re.compile(r"zzgl\.\s*[\d,]+\s*%\s*USt\.\s*auf\s*[\d.]+,\d{2}\s+([\d.]+,\d{2})")

STOP_PREFIXES = (
    "Gesamt Netto", "zzgl.", "Gesamtbetrag", "Bankverbindung", "Volksbank",
    "IBAN", "BIC", "HRB", "Amtsgericht", "Steuernr", "Zu Lieferschein",
    "Kundengruppe", "Lieferdatum", "Datum:", "Rechnung Nr", "Kunden Nr",
    "Seite:", "Pos ", "EUR",
)

HEADER_MARKER = "Pos Menge Nummer"


def _to_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def parse_fatihet_invoice_pdf(file) -> dict:
    """Parse a Fatih Et GmbH (meat vendor) invoice PDF. `file` is a path or file-like object."""
    with pdfplumber.open(file) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    lines = full_text.split("\n")

    invoice_number = None
    m = re.search(r"Rechnung Nr\.\s*(\S+)", full_text)
    if m:
        invoice_number = m.group(1)

    invoice_date = None
    m = re.search(r"^Datum:\s*(\d{2}\.\d{2}\.\d{4})", full_text, re.MULTILINE)
    if m:
        d, mo, y = m.group(1).split(".")
        invoice_date = f"{y}-{mo}-{d}"

    delivery_date = None
    m = re.search(r"^Lieferdatum:\s*(\d{2}\.\d{2}\.\d{4})", full_text, re.MULTILINE)
    if m:
        d, mo, y = m.group(1).split(".")
        delivery_date = f"{y}-{mo}-{d}"

    items = []
    in_table = False
    active_item = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith(HEADER_MARKER):
            in_table = True
            active_item = None
            continue

        if not in_table:
            continue

        if any(line.startswith(p) for p in STOP_PREFIXES):
            active_item = None
            continue

        match = ITEM_RE.match(line)
        if match:
            g = match.groupdict()
            item = {
                "pos": int(g["pos"]),
                "quantity": _to_float(g["qty"]),
                "unit": g["unit"],
                "product_number": g["nummer"],
                "description": g["text"].strip(),
                "unit_price": _to_float(g["price"]),
                "line_total": _to_float(g["total"]),
            }
            items.append(item)
            active_item = item
        elif active_item is not None:
            active_item["description"] = f"{active_item['description']} {line}".strip()

    netto_total = None
    m = re.search(r"Gesamt Netto\s+([\d.]+,\d{2})", full_text)
    if m:
        netto_total = _to_float(m.group(1))

    vat_total = sum(_to_float(v) for v in VAT_LINE_RE.findall(full_text))

    brutto_total = None
    m = re.search(r"Gesamtbetrag\s+([\d.]+,\d{2})", full_text)
    if m:
        brutto_total = _to_float(m.group(1))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "delivery_date": delivery_date,
        "items": items,
        "netto_total": netto_total,
        "vat_total": round(vat_total, 2),
        "brutto_total": brutto_total,
    }
