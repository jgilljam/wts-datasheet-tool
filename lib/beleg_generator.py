"""Beleg-PDF-Generator: Auftragsbestätigung + Bestellung.

Beide Beleg-Typen nutzen dasselbe Template `beleg.html/css` — nur die Labels
und Pricing-Spalte (VK/EK) variieren. Branding ist identisch zum Lieferschein.
"""

from __future__ import annotations

import base64
import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

if os.uname().sysname == "Darwin":
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

from weasyprint import HTML, CSS  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

_jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _logo_uri() -> str:
    logo_path = ASSETS_DIR / "logo.png"
    data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def _format_date(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    return str(v)


def _qty_display(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.is_integer():
        return f"{int(f)}"
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _eur(cents: int | float | None) -> str:
    """1234 → '12,34 €' (DE-Format)."""
    if cents is None:
        return ""
    val = float(cents) / 100.0
    formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def _build_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        a = it.get("articles") or {}
        qty = it.get("qty")
        out.append({
            "pos_nr": it.get("pos_nr") or "",
            "sku": a.get("sku") or "",
            "title": a.get("title_de") or "",
            "description_override": it.get("description_override") or "",
            "qty_display": _qty_display(qty),
            "unit": it.get("unit") or "Stk",
            "unit_price_eur": _eur(it.get("unit_price_cents") or 0),
            "discount_pct": (
                f"{float(it['discount_pct']):.1f}".rstrip("0").rstrip(".")
                if it.get("discount_pct") else ""
            ),
            "tax_rate": int(float(it.get("tax_rate") or 0)),
            "line_total_eur": _eur(it.get("line_total_cents") or 0),
            "is_dropship": bool(it.get("is_dropship")),
        })
    return out


def _build_totals(items: list[dict[str, Any]], header: dict[str, Any]) -> dict[str, str]:
    """Liefert formatierte Summen-Strings + 'tax_rate_summary' (z.B. '19 %' oder '0% (Reverse-Charge)')."""
    net = int(header.get("total_net_cents") or 0)
    tax = int(header.get("tax_total_cents") or 0)
    discount = int(header.get("discount_total_cents") or 0)
    gross = net + tax

    # Tax-Rate-Summary: einheitlich falls alle Positionen denselben Satz haben, sonst „gemischt"
    rates = {float(it.get("tax_rate") or 0) for it in items}
    if len(rates) == 1:
        rate = rates.pop()
        if rate == 0:
            tax_label = "0 % (Reverse-Charge)"
        else:
            tax_label = f"{int(rate)} %"
    elif len(rates) > 1:
        tax_label = "gemischt"
    else:
        tax_label = ""

    return {
        "net_total_eur": _eur(net),
        "tax_total_eur": _eur(tax),
        "gross_total_eur": _eur(gross),
        "discount_total_eur": _eur(discount) if discount else "",
        "tax_rate_summary": tax_label,
    }


# =====================================================================
#  Auftragsbestätigung
# =====================================================================

def render_auftragsbestaetigung_pdf(order: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Auftragsbestätigung als PDF.

    Args:
        order: dict aus `features.orders.repo.get_order` (inkl. `customer`,
               `shipping_address`, `billing_address`).
        items: aus `features.orders.repo.list_order_items`.
    """
    customer = order.get("customer") or {}
    shipping_addr = order.get("shipping_address")
    rev_charge = bool(customer.get("is_reverse_charge_eligible"))

    totals = _build_totals(items, order)

    payment_hint_parts = []
    if order.get("payment_terms_days"):
        payment_hint_parts.append(f"Zahlbar netto innerhalb {order['payment_terms_days']} Tagen.")
    if rev_charge:
        payment_hint_parts.append(
            "Steuerschuldnerschaft des Leistungsempfängers (Reverse-Charge nach §13b UStG)."
        )
    if order.get("incoterms"):
        place = f" {order.get('incoterms_place')}" if order.get("incoterms_place") else ""
        payment_hint_parts.append(f"Lieferung gemäß Incoterms 2020 {order['incoterms']}{place}.")

    context = {
        "logo_uri": _logo_uri(),
        "doc_label": "Auftragsbestätigung",
        "doc_number": order.get("order_number") or "—",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(order.get("ordered_at")),
        "doc_date_label": "Auftragsdatum",
        "due_date": _format_date(order.get("due_date")),
        "due_date_label": "Liefertermin",
        "reference": order.get("customer_reference"),
        "reference_label": "Ihre Best.-Nr.",
        "recipient_label": "Kunde",
        "price_label": "VK €",
        "d": order,
        "party": customer,
        "shipping_addr": shipping_addr,
        "dropship_note": "",
        "reverse_charge": rev_charge,
        "items": _build_items(items),
        "footer_hint": " ".join(payment_hint_parts) or None,
        "show_signature": False,  # AB ist meist beidseitig per E-Mail bestätigt
        **totals,
    }

    template = _jinja.get_template("beleg.html")
    html_str = template.render(**context)
    css_path = TEMPLATES_DIR / "beleg.css"
    css = CSS(filename=str(css_path))

    buf = BytesIO()
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        target=buf, stylesheets=[css],
    )
    return buf.getvalue()


# =====================================================================
#  Bestellung (PO an Lieferant)
# =====================================================================

def render_bestellung_pdf(po: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Bestellung als PDF (von WTS an Lieferanten).

    Args:
        po: dict aus `features.purchase_orders.repo.get_po` (inkl. `supplier`,
            `source_order`, `shipping_address`).
        items: aus `features.purchase_orders.repo.list_po_items`.
    """
    supplier = po.get("supplier") or {}
    shipping_addr = po.get("shipping_address")
    source_order = po.get("source_order") or {}
    rev_charge = bool(supplier.get("is_reverse_charge_eligible"))

    has_dropship_items = any(it.get("is_dropship") for it in items)
    dropship_note = ""
    if has_dropship_items and source_order.get("order_number"):
        dropship_note = (
            f"Bezogen auf unseren Auftrag {source_order['order_number']} — "
            "Direktlieferung an unseren Endkunden gemäß separater Lieferadresse."
        )

    totals = _build_totals(items, po)

    hint_parts = []
    if po.get("payment_terms_days"):
        hint_parts.append(f"Zahlungsziel: netto {po['payment_terms_days']} Tage.")
    if rev_charge:
        hint_parts.append(
            "Steuerschuldnerschaft des Leistungsempfängers (Reverse-Charge nach §13b UStG)."
        )
    if po.get("incoterms"):
        place = f" {po.get('incoterms_place')}" if po.get("incoterms_place") else ""
        hint_parts.append(f"Lieferbedingungen: Incoterms 2020 {po['incoterms']}{place}.")
    hint_parts.append(
        "Bitte senden Sie uns Ihre Auftragsbestätigung mit verbindlichem "
        "Liefertermin per E-Mail an info@wts-trading.de."
    )

    context = {
        "logo_uri": _logo_uri(),
        "doc_label": "Bestellung",
        "doc_number": po.get("po_number") or "—",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(po.get("ordered_at")),
        "doc_date_label": "Bestelldatum",
        "due_date": _format_date(po.get("expected_at")),
        "due_date_label": "Wunschtermin",
        "reference": po.get("supplier_reference"),
        "reference_label": "Ihre AB-Nr.",
        "recipient_label": "Lieferant",
        "price_label": "EK €",
        "d": po,
        "party": supplier,
        "shipping_addr": shipping_addr,
        "dropship_note": dropship_note,
        "reverse_charge": rev_charge,
        "items": _build_items(items),
        "footer_hint": " ".join(hint_parts) or None,
        "show_signature": False,
        **totals,
    }

    template = _jinja.get_template("beleg.html")
    html_str = template.render(**context)
    css_path = TEMPLATES_DIR / "beleg.css"
    css = CSS(filename=str(css_path))

    buf = BytesIO()
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        target=buf, stylesheets=[css],
    )
    return buf.getvalue()
