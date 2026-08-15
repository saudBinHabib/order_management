import re

import pdfplumber

# Product/deposit rows, e.g.:
#   "015231.4 4337182232636 100 MPRO VERP.DECKEL PP ECK.F. PG 2,090 1 2,09 4 8,36 A 2,090"
#   "+ 028977.7 MW LEERGUT EURO 3,30 KA 3,300 1 3,30 2 6,60 A 3,300"
# EINZEL PREIS / STÜCK PREIS always have 3 decimals, which is what lets the
# (non-greedy) description text stop in the right place even though the
# description itself often contains 2-decimal numbers (e.g. "EURO 3,30").
ITEM_RE = re.compile(
    r"^(?:\+\s+)?"
    r"(?P<artnr>\d{6}\.\d)\s+"
    r"(?:(?P<ean>\d{8,14})\s+)?"
    r"(?P<text>.+?)\s+"
    r"(?P<pack>[A-Z]{2,4})\s+"
    r"(?P<einzelpreis>\d+,\d{3})\s+"
    r"(?P<inhalt_kolli>\d+)\s+"
    r"(?P<kolli_preis>\d+,\d{2})\s+"
    r"(?P<menge>\d+)\s+"
    r"(?P<gesamt_preis>\d+,\d{2})\s+"
    r"[A-Z]\s+"
    r"\d+,\d{3}"
    r"(?:\s+[*W])?"
    r"\s*$"
)

# The grand-total row right before "SUMME EUR", e.g. "464,73  88,30  553,03"
TOTALS_RE = re.compile(r"([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*\nSUMME EUR")

HEADER_MARKER = "ART.-NR EAN ARTIKEL"

STOP_PREFIXES = (
    "-", "NETTOWERT", "LEERGUT:", "SUMME", "ÜBERTRAG", "KOLLI GES",
    "SEITENSUMME", "Mengenrabatt", "NOCH ZU ZAHLEN", "TRANSAKTIONSENDE",
)

TABLE_END_MARKERS = ("SUMME EUR", "NOCH ZU ZAHLEN", "TRANSAKTIONSENDE")


def _to_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def parse_metro_invoice_pdf(file) -> dict:
    """Parse a METRO Deutschland GmbH invoice PDF. `file` is a path or file-like object.

    Item-level extraction is best-effort: METRO's invoice is a dense POS ledger
    (category headers, per-item deposit sub-lines, quantity-discount rows all
    interleaved), unlike a conventional itemized invoice. The header totals
    (Netto/USt./Brutto) are parsed from a fixed-format summary block and are
    reliable; treat the item list as a helpful record, not a verified one.
    """
    with pdfplumber.open(file) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    lines = full_text.split("\n")

    invoice_number = None
    m = re.search(r"R\s?ECHNUNGS-NR\.\s*(\S+)", full_text)
    if m:
        invoice_number = m.group(1)

    invoice_date = None
    m = re.search(r"RECHNUNGSDATUM:\s*(\d{2}\.\d{2}\.\d{4})", full_text)
    if m:
        d, mo, y = m.group(1).split(".")
        invoice_date = f"{y}-{mo}-{d}"

    delivery_date = None
    m = re.search(r"LIEFERDATUM:\s*(\d{2}\.\d{2}\.\d{4})", full_text)
    if m:
        d, mo, y = m.group(1).split(".")
        delivery_date = f"{y}-{mo}-{d}"

    items = []
    in_table = False
    table_done = False
    active_item = None
    pos = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line or table_done:
            continue

        if any(marker in line for marker in TABLE_END_MARKERS):
            table_done = True
            in_table = False
            continue

        if HEADER_MARKER in line:
            in_table = True
            active_item = None
            continue

        if not in_table:
            continue

        if "****" in line or any(line.startswith(p) for p in STOP_PREFIXES):
            active_item = None
            continue

        match = ITEM_RE.match(line)
        if match:
            g = match.groupdict()
            pos += 1
            item = {
                "pos": pos,
                "description": g["text"].strip(),
                "product_number": g["artnr"],
                "quantity": float(g["menge"]),
                "unit": g["pack"],
                "unit_price": _to_float(g["kolli_preis"]),
                "line_total": _to_float(g["gesamt_preis"]),
            }
            items.append(item)
            active_item = item
        elif active_item is not None:
            active_item["description"] = f"{active_item['description']} {line}".strip()

    netto_total = vat_total = brutto_total = None
    m = TOTALS_RE.search(full_text)
    if m:
        netto_total = _to_float(m.group(1))
        vat_total = _to_float(m.group(2))
        brutto_total = _to_float(m.group(3))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "delivery_date": delivery_date,
        "items": items,
        "netto_total": netto_total,
        "vat_total": vat_total,
        "brutto_total": brutto_total,
    }
