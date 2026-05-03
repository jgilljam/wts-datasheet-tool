"""Beleg-PDF-Generator: Auftragsbestätigung, Bestellung, Rechnung, Stornorechnung.

Alle Beleg-Typen nutzen dasselbe Template `beleg.html/css` (Wilspec-inspiriertes
Layout mit WTS-Branding) — nur Labels, Master-Daten-Spalten und Pricing-Spalte
(VK/EK) variieren.
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


# ---------- Helpers ----------

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


def _is_overdue(due_date: Any, status: str | None) -> bool:
    """True wenn due_date < heute und Rechnung nicht bezahlt/storniert."""
    if status in {"paid", "cancelled", "reversed"}:
        return False
    if not due_date:
        return False
    if isinstance(due_date, str):
        try:
            due_date = date.fromisoformat(due_date[:10])
        except ValueError:
            return False
    if isinstance(due_date, datetime):
        due_date = due_date.date()
    if not isinstance(due_date, date):
        return False
    return due_date < date.today()


def _ust_mode_label(rev_charge: bool, items: list[dict[str, Any]]) -> str:
    if rev_charge:
        return "Reverse-Charge §13b"
    rates = sorted({int(float(it.get("tax_rate") or 0)) for it in items if it.get("tax_rate") is not None})
    if not rates:
        return "—"
    if len(rates) == 1:
        return f"{rates[0]} % USt"
    return ", ".join(f"{r} %" for r in rates) + " USt"


def _shipping_label(d: dict[str, Any]) -> str:
    parts: list[str] = []
    if d.get("shipping_method"):
        parts.append(str(d["shipping_method"]).replace("_", " ").title())
    if d.get("incoterms"):
        place = f" {d['incoterms_place']}" if d.get("incoterms_place") else ""
        parts.append(f"{d['incoterms']}{place}")
    return " · ".join(parts) if parts else "—"


def _payment_terms_label(d: dict[str, Any]) -> str:
    days = d.get("payment_terms_days")
    if days:
        return f"netto {days} Tage"
    if d.get("payment_method"):
        return str(d["payment_method"]).replace("_", " ").title()
    return "—"


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
    """Liefert formatierte Summen + Pro-Satz-Aufschlüsselung (UStG §14 Abs.4 Nr.8)."""
    discount = int(header.get("discount_total_cents") or 0)

    by_rate: dict[float, dict[str, int]] = {}
    for it in items:
        rate = float(it.get("tax_rate") or 0)
        line_net = int(it.get("line_total_cents") or 0)
        line_tax = int(it.get("tax_amount_cents") or 0)
        bucket = by_rate.setdefault(rate, {"net": 0, "tax": 0})
        bucket["net"] += line_net
        bucket["tax"] += line_tax

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
        "_net_cents": net,
        "_tax_cents": tax,
        "_gross_cents": gross,
    }


def _build_bank_lines(company: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if company.get("bank_name"):
        lines.append(f"Bank: {company['bank_name']}")
    if company.get("iban"):
        lines.append(f"IBAN: {company['iban']}")
    if company.get("bic"):
        lines.append(f"BIC: {company['bic']}")
    return lines


def _customs_lines_from_breakdown(tax_breakdown: list[dict[str, str]]) -> list[str]:
    """Pflicht-Caption (UStG §14 Abs.4 Nr.8) als kompakte Mini-Strings."""
    if not tax_breakdown or len(tax_breakdown) <= 1:
        return []
    parts = ["USt-Aufschlüsselung gemäß §14 UStG:"]
    for b in tax_breakdown:
        parts.append(f"{b['rate_label']} → Netto {b['net_eur']} · USt {b['tax_eur']}")
    return [" · ".join(parts)]


# =====================================================================
#  Auftragsbestätigung
# =====================================================================

def render_auftragsbestaetigung_pdf(order: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Auftragsbestätigung als PDF."""
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    customer = order.get("customer") or {}
    shipping_addr = order.get("shipping_address")
    billing_addr = order.get("billing_address") or shipping_addr
    rev_charge = bool(customer.get("is_reverse_charge_eligible"))

    company = inv_repo.get_company_settings()
    built = _build_items(items)
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
        "doc_number_label": "Auftrags-Nr.",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(order.get("ordered_at")),
        "doc_date_label": "Auftragsdatum",
        "due_date": _format_date(order.get("due_date")),
        "due_date_label": "Liefertermin",
        "due_date_warn": False,
        "service_date": "",
        "service_date_label": "",
        "reference": order.get("customer_reference"),
        "reference_label": "Ihre Best.-Nr.",
        "related_order_number": "",
        "customer_number": customer.get("customer_number") or "",
        "recipient_label_billing": "Rechnungsadresse",
        "recipient_label_shipping": "Lieferadresse",
        "shipping_party_name": order.get("shipping_party_name") or customer.get("legal_name"),
        "price_label": "VK €",
        "total_label": "Auftragssumme",
        "currency_label": "EUR",
        "d": order,
        "party": customer,
        "shipping_addr": shipping_addr,
        "billing_addr": billing_addr,
        "storno_banner": "",
        "dropship_note": "",
        "reverse_charge": rev_charge,
        "items": built,
        "master_label_1": "Ihre Best.-Nr.",
        "master_value_1": order.get("customer_reference") or "",
        "master_label_2": "Versand",
        "master_value_2": _shipping_label(order),
        "master_label_3": "Zahlungsziel",
        "master_value_3": _payment_terms_label(order),
        "master_label_4": "USt-Modus",
        "master_value_4": _ust_mode_label(rev_charge, items),
        "customs_lines": _customs_lines_from_breakdown(totals["tax_breakdown"]),
        "footer_help": "Fragen zu dieser Auftragsbestätigung? Wir helfen gern weiter:",
        "footer_hint": " ".join(payment_hint_parts) or None,
        "bank_lines": _build_bank_lines(company),
        "open_balance_eur": "",
        "company": company,
        **totals,
    }
    return _render(context)


# =====================================================================
#  Rechnung (Verkaufs-Rechnung an Kunden) + Storno-Variante
# =====================================================================

def render_rechnung_pdf(invoice: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Rechnung als PDF."""
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    customer = invoice.get("customer") or {}
    shipping_addr = invoice.get("shipping_address") or invoice.get("billing_address")
    billing_addr = invoice.get("billing_address") or shipping_addr
    rev_charge = bool(invoice.get("is_reverse_charge"))
    is_storno = bool(invoice.get("reverses_id"))
    reverses = invoice.get("reverses") or {}
    related_order = invoice.get("related_order") or {}

    company = inv_repo.get_company_settings()
    built = _build_items(items)
    totals = _build_totals(items, invoice)

    doc_label = "Stornorechnung" if is_storno else "Rechnung"

    storno_banner = ""
    if is_storno:
        orig_nr = reverses.get("invoice_number") or "?"
        orig_date = _format_date(reverses.get("issued_at"))
        storno_banner = (
            f"STORNORECHNUNG — hebt Rechnung {orig_nr} vom {orig_date} "
            f"vollständig auf. Grund: {invoice.get('cancellation_reason') or 'nicht angegeben'}."
        )

    hint_parts = []
    if not is_storno:
        if invoice.get("due_date"):
            hint_parts.append(f"Zahlbar bis {_format_date(invoice['due_date'])} ohne Abzug.")
        if invoice.get("payment_terms_days"):
            hint_parts.append(f"Zahlungsziel: netto {invoice['payment_terms_days']} Tage.")
        if rev_charge:
            hint_parts.append(
                "Steuerschuldnerschaft des Leistungsempfängers (Reverse-Charge nach §13b UStG). "
                "Nettorechnung."
            )
        if invoice.get("incoterms"):
            place = f" {invoice.get('incoterms_place')}" if invoice.get("incoterms_place") else ""
            hint_parts.append(f"Lieferung gemäß Incoterms 2020 {invoice['incoterms']}{place}.")
        if invoice.get("purpose_of_payment"):
            hint_parts.append(f"Verwendungszweck: {invoice['purpose_of_payment']}.")
        elif invoice.get("invoice_number"):
            hint_parts.append(f"Verwendungszweck: {invoice['invoice_number']}.")

    paid = int(invoice.get("paid_amount_cents") or 0)
    open_cents = max(0, totals["_gross_cents"] - paid)
    show_open = (not is_storno) and invoice.get("status") in {"partially_paid", "overdue", "issued"} and open_cents > 0

    overdue_flag = _is_overdue(invoice.get("due_date"), invoice.get("status"))

    context = {
        "logo_uri": _logo_uri(),
        "doc_label": doc_label,
        "doc_number": invoice.get("invoice_number") or "—",
        "doc_number_label": "Rechnungs-Nr.",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(invoice.get("issued_at")),
        "doc_date_label": "Rechnungsdatum",
        "due_date": _format_date(invoice.get("due_date")),
        "due_date_label": "Fällig am",
        "due_date_warn": overdue_flag,
        "service_date": _format_date(invoice.get("service_date")),
        "service_date_label": "Leistungsdatum",
        "reference": invoice.get("customer_reference"),
        "reference_label": "Ihre Best.-Nr.",
        "related_order_number": related_order.get("order_number") or "",
        "customer_number": customer.get("customer_number") or "",
        "recipient_label_billing": "Rechnungsadresse",
        "recipient_label_shipping": "Lieferadresse",
        "shipping_party_name": invoice.get("shipping_party_name") or customer.get("legal_name"),
        "price_label": "Einzelpreis €",
        "total_label": "Rechnungsbetrag" if not is_storno else "Gutschrift",
        "currency_label": "EUR",
        "d": invoice,
        "party": customer,
        "shipping_addr": shipping_addr,
        "billing_addr": billing_addr,
        "storno_banner": storno_banner,
        "dropship_note": "",
        "reverse_charge": rev_charge,
        "items": built,
        "master_label_1": "Ihre Best.-Nr.",
        "master_value_1": invoice.get("customer_reference") or "",
        "master_label_2": "Versand",
        "master_value_2": _shipping_label(invoice),
        "master_label_3": "Zahlungsziel",
        "master_value_3": _payment_terms_label(invoice),
        "master_label_4": "USt-Modus",
        "master_value_4": _ust_mode_label(rev_charge, items),
        "customs_lines": _customs_lines_from_breakdown(totals["tax_breakdown"]),
        "footer_help": "Fragen zur Rechnung? Bitte Rechnungs-Nr. bei Rückfragen angeben:",
        "footer_hint": " ".join(hint_parts) or None,
        "bank_lines": _build_bank_lines(company) if not is_storno else [],
        "open_balance_eur": _eur(open_cents) if show_open else "",
        "company": company,
        **{k: v for k, v in totals.items() if not k.startswith("_")},
    }
    return _render(context)


# =====================================================================
#  Bestellung (PO an Lieferant)
# =====================================================================

def render_bestellung_pdf(po: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Rendert eine Bestellung als PDF (von WTS an Lieferanten)."""
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    supplier = po.get("supplier") or {}
    shipping_addr = po.get("shipping_address")
    billing_addr = po.get("billing_address") or shipping_addr
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

    built = _build_items(items)
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
        "doc_number_label": "Bestell-Nr.",
        "today": date.today().strftime("%d.%m.%Y"),
        "doc_date": _format_date(po.get("ordered_at")),
        "doc_date_label": "Bestelldatum",
        "due_date": _format_date(po.get("expected_at")),
        "due_date_label": "Wunschtermin",
        "due_date_warn": False,
        "service_date": "",
        "service_date_label": "",
        "reference": po.get("supplier_reference"),
        "reference_label": "Ihre AB-Nr.",
        "related_order_number": source_order.get("order_number") or "",
        "customer_number": supplier.get("supplier_number") or supplier.get("customer_number") or "",
        "recipient_label_billing": "Lieferant (Rechnung an WTS)",
        "recipient_label_shipping": "Lieferadresse",
        "shipping_party_name": po.get("shipping_party_name") or company.get("legal_name") or "Weber Trading & Service",
        "price_label": "EK €",
        "total_label": "Bestellsumme",
        "currency_label": "EUR",
        "d": po,
        "party": supplier,
        "shipping_addr": shipping_addr,
        "billing_addr": billing_addr,
        "storno_banner": "",
        "dropship_note": dropship_note,
        "reverse_charge": rev_charge,
        "items": built,
        "master_label_1": "Ihre AB-Nr.",
        "master_value_1": po.get("supplier_reference") or "",
        "master_label_2": "Versand",
        "master_value_2": _shipping_label(po),
        "master_label_3": "Zahlungsziel",
        "master_value_3": _payment_terms_label(po),
        "master_label_4": "USt-Modus",
        "master_value_4": _ust_mode_label(rev_charge, items),
        "customs_lines": _customs_lines_from_breakdown(totals["tax_breakdown"]),
        "footer_help": "Rückfragen zur Bestellung an info@wts-trading.de:",
        "footer_hint": " ".join(hint_parts) or None,
        "bank_lines": [],  # auf einer outgoing PO keine eigene Bank-Verbindung nötig
        "open_balance_eur": "",
        "company": company,
        **{k: v for k, v in totals.items() if not k.startswith("_")},
    }
    return _render(context)


# =====================================================================
#  Renderer (gemeinsame Endstrecke)
# =====================================================================

def _render(context: dict[str, Any]) -> bytes:
    template = _jinja.get_template("beleg.html")
    html_str = template.render(**context)
    css_path = TEMPLATES_DIR / "beleg.css"
    css = CSS(filename=str(css_path))

    buf = BytesIO()
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        target=buf, stylesheets=[css],
    )
    return buf.getvalue()
