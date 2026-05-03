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


def _build_totals(items: list[dict[str, Any]], header: dict[str, Any]) -> dict[str, Any]:
    """Liefert formatierte Summen + Pro-Satz-Aufschlüsselung (UStG §14 Abs.4 Nr.8).

    Returns:
        dict mit:
          - net_total_eur, tax_total_eur, gross_total_eur, discount_total_eur
          - tax_breakdown: list of {"rate_label", "net_eur", "tax_eur"} pro Steuersatz
          - tax_rate_summary: kompakter Text (für Backward-Compat / Footer)
    """
    discount = int(header.get("discount_total_cents") or 0)

    # Pro Steuersatz aggregieren (Cent-genau aus Items, nicht aus Header)
    by_rate: dict[float, dict[str, int]] = {}
    for it in items:
        rate = float(it.get("tax_rate") or 0)
        line_net = int(it.get("line_total_cents") or 0)
        line_tax = int(it.get("tax_amount_cents") or 0)
        bucket = by_rate.setdefault(rate, {"net": 0, "tax": 0})
        bucket["net"] += line_net
        bucket["tax"] += line_tax

    # Header-Summen sind Source-of-Truth; Items-Summen für Pro-Satz-Aufschlüsselung
    net = int(header.get("total_net_cents") or 0)
    tax = int(header.get("tax_total_cents") or 0)
    if not net and by_rate:
        net = sum(b["net"] for b in by_rate.values())
    if not tax and by_rate:
        tax = sum(b["tax"] for b in by_rate.values())
    gross = net + tax

    tax_breakdown: list[dict[str, str]] = []
    for rate in sorted(by_rate.keys()):
        b = by_rate[rate]
        if rate == 0:
            label = "0 % (Reverse-Charge)"
        else:
            label = f"{int(rate) if rate.is_integer() else rate:g} %"
        tax_breakdown.append({
            "rate_label": label,
            "net_eur": _eur(b["net"]),
            "tax_eur": _eur(b["tax"]),
        })

    if len(by_rate) == 1:
        only_rate = next(iter(by_rate.keys()))
        if only_rate == 0:
            tax_rate_summary = "0 % (Reverse-Charge)"
        else:
            tax_rate_summary = f"{int(only_rate) if only_rate.is_integer() else only_rate:g} %"
    elif len(by_rate) > 1:
        tax_rate_summary = ", ".join(b["rate_label"] for b in tax_breakdown)
    else:
        tax_rate_summary = ""

    return {
        "net_total_eur": _eur(net),
        "tax_total_eur": _eur(tax),
        "gross_total_eur": _eur(gross),
        "discount_total_eur": _eur(discount) if discount else "",
        "tax_rate_summary": tax_rate_summary,
        "tax_breakdown": tax_breakdown,
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
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    customer = order.get("customer") or {}
    shipping_addr = order.get("shipping_address")
    rev_charge = bool(customer.get("is_reverse_charge_eligible"))

    company = inv_repo.get_company_settings()
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
        "company": company,
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

# =====================================================================
#  Rechnung (Verkaufs-Rechnung an Kunden) + Storno-Variante
# =====================================================================

def render_rechnung_pdf(invoice: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Rechnung als PDF.

    Wenn `invoice.reverses_id` gesetzt ist, wird automatisch eine Stornorechnung
    gerendert (Titel `Stornorechnung`, Verweis auf Original-Beleg-Nr im Footer-Hint).
    """
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    customer = invoice.get("customer") or {}
    shipping_addr = invoice.get("shipping_address") or invoice.get("billing_address")
    rev_charge = bool(invoice.get("is_reverse_charge"))
    is_storno = bool(invoice.get("reverses_id"))
    reverses = invoice.get("reverses") or {}

    company = inv_repo.get_company_settings()
    totals = _build_totals(items, invoice)

    # Beleg-Titel
    if is_storno:
        doc_label = "Stornorechnung"
    else:
        doc_label = "Rechnung"

    # Footer-Hint: Zahlungsdetails + Reverse-Charge + Storno-Verweis
    hint_parts = []
    if is_storno:
        orig_nr = reverses.get("invoice_number") or "?"
        orig_date = _format_date(reverses.get("issued_at"))
        hint_parts.append(
            f"Diese Stornorechnung hebt die Rechnung {orig_nr} vom {orig_date} "
            "vollständig auf. Grund: "
            f"{invoice.get('cancellation_reason') or 'nicht angegeben'}."
        )
    else:
        if invoice.get("due_date"):
            hint_parts.append(f"Zahlbar bis {_format_date(invoice['due_date'])} ohne Abzug.")
        if invoice.get("payment_terms_days"):
            hint_parts.append(
                f"Zahlungsziel: netto {invoice['payment_terms_days']} Tage."
            )
        if rev_charge:
            hint_parts.append(
                "Steuerschuldnerschaft des Leistungsempfängers (Reverse-Charge nach §13b UStG). "
                "Nettorechnung."
            )
        if invoice.get("incoterms"):
            place = f" {invoice.get('incoterms_place')}" if invoice.get("incoterms_place") else ""
            hint_parts.append(f"Lieferung gemäß Incoterms 2020 {invoice['incoterms']}{place}.")

    # Zahlungsdetails-Block (IBAN/BIC/Verwendungszweck)
    payment_block_lines = []
    if not is_storno:
        if company.get("iban"):
            payment_block_lines.append(f"IBAN: {company['iban']}")
        if company.get("bic"):
            payment_block_lines.append(f"BIC: {company['bic']}")
        if company.get("bank_name"):
            payment_block_lines.append(f"Bank: {company['bank_name']}")
        if invoice.get("purpose_of_payment"):
            payment_block_lines.append(f"Verwendungszweck: {invoice['purpose_of_payment']}")
        elif invoice.get("invoice_number"):
            payment_block_lines.append(f"Verwendungszweck: {invoice['invoice_number']}")

    context = {
        "logo_uri": _logo_uri(),
        "doc_label": doc_label,
        "doc_number": invoice.get("invoice_number") or "—",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(invoice.get("issued_at")),
        "doc_date_label": "Rechnungsdatum",
        "due_date": _format_date(invoice.get("service_date")),
        "due_date_label": "Leistungsdatum",
        "reference": invoice.get("customer_reference"),
        "reference_label": "Ihre Best.-Nr.",
        "recipient_label": "Kunde",
        "price_label": "Einzelpreis €",
        "d": invoice,
        "party": customer,
        "shipping_addr": shipping_addr,
        "dropship_note": "",
        "reverse_charge": rev_charge,
        "items": _build_items(items),
        "footer_hint": " ".join(hint_parts) or None,
        "payment_block_lines": payment_block_lines,
        "company": company,
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
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    supplier = po.get("supplier") or {}
    shipping_addr = po.get("shipping_address")
    source_order = po.get("source_order") or {}
    rev_charge = bool(supplier.get("is_reverse_charge_eligible"))
    company = inv_repo.get_company_settings()

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
        "company": company,
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
