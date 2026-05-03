"""Lieferschein-PDF aus Supabase-Daten via WeasyPrint im WTS-Branding."""

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


SHIPPING_METHOD_LABELS = {
    "paket": "Paket",
    "stueckgut": "Stückgut",
    "spedition": "Spedition",
    "kurier": "Kurier",
    "abholung": "Abholung",
    "direktlieferung": "Direktlieferung (Strecke)",
}


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


def _build_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        a = it.get("articles") or {}
        qty = it.get("qty_actual") if it.get("qty_actual") is not None else it.get("qty_expected")
        out.append({
            "pos_nr": it.get("pos_nr") or "",
            "sku": a.get("sku") or "",
            "title": a.get("title_de") or "",
            "description_override": it.get("description_override") or "",
            "qty_display": _qty_display(qty),
            "unit": it.get("unit") or "Stk",
            "batch_lot": it.get("batch_lot") or "",
            "mhd": _format_date(it.get("mhd")),
            "adr_un_nr": a.get("adr_un_nr") or "",
            "adr_class": a.get("adr_class") or "",
        })
    return out


def _build_pfand_summary(items: list[dict[str, Any]]) -> tuple[list[dict], float]:
    lines = []
    total_eur = 0.0
    for it in items:
        a = it.get("articles") or {}
        if not a.get("is_pfand") or not a.get("pfand_per_unit_cents"):
            continue
        qty = it.get("qty_actual") if it.get("qty_actual") is not None else it.get("qty_expected")
        if not qty:
            continue
        per_unit_cents = int(a["pfand_per_unit_cents"])
        sum_eur = per_unit_cents / 100 * float(qty)
        total_eur += sum_eur
        lines.append({
            "sku": a.get("sku") or "",
            "qty": _qty_display(qty),
            "per_unit": f"{per_unit_cents/100:.2f}",
            "sum": f"{sum_eur:.2f}",
        })
    return lines, total_eur


def _build_adr_summary(items: list[dict[str, Any]]) -> list[dict]:
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for it in items:
        a = it.get("articles") or {}
        if not a.get("adr_un_nr"):
            continue
        qty = it.get("qty_actual") if it.get("qty_actual") is not None else it.get("qty_expected")
        if not qty:
            continue
        key = (a["adr_un_nr"], a.get("adr_class") or "")
        cur = agg.setdefault(key, {
            "un_nr": a["adr_un_nr"],
            "cls": a.get("adr_class") or "",
            "qty_raw": 0.0,
            "kg_raw": 0.0,
            "proper_name": a.get("adr_proper_name") or "",
        })
        cur["qty_raw"] += float(qty)
        if a.get("adr_net_kg_per_unit"):
            cur["kg_raw"] += float(a["adr_net_kg_per_unit"]) * float(qty)
    return [
        {
            "un_nr": v["un_nr"],
            "cls": v["cls"],
            "qty": _qty_display(v["qty_raw"]),
            "kg": f"{v['kg_raw']:.3f}" if v["kg_raw"] else "",
            "proper_name": v["proper_name"],
        }
        for v in agg.values()
    ]


def render_lieferschein_pdf(
    delivery: dict[str, Any],
    items: list[dict[str, Any]],
) -> bytes:
    """Rendert einen Lieferschein als PDF (BytesIO) im Wilspec-Layout.

    Args:
        delivery: dict mit allen Feldern aus `repo.get_delivery` (inkl.
                  joined `parties`, `source_party`, `shipping_address`).
        items:    Liste aus `repo.list_delivery_items`.
    """
    from features.invoices import repo as inv_repo  # lazy import wg. cycle

    party = delivery.get("parties") or {}
    source_party = delivery.get("source_party")
    shipping_addr = delivery.get("shipping_address")
    direction = delivery.get("direction")
    is_dropship = bool(source_party)
    company = inv_repo.get_company_settings()

    if direction == "outbound":
        doc_label = "Lieferschein"
        doc_number_label = "Lieferschein-Nr."
        recipient_label_main = "Empfänger"
    else:
        doc_label = "Wareneingang"
        doc_number_label = "Wareneingangs-Nr."
        recipient_label_main = "Absender"

    if is_dropship:
        recipient_label_secondary = "Absender (Lieferant)"
        secondary_party_name = source_party.get("legal_name") if source_party else ""
        secondary_addr = None
    else:
        recipient_label_secondary = "Absender" if direction == "outbound" else "Empfänger (WTS)"
        secondary_party_name = company.get("legal_name") or "Weber Trading & Service"
        secondary_addr = {
            "street": company.get("street") or "",
            "zip": company.get("zip") or "",
            "city": company.get("city") or "",
            "country_code": company.get("country_code") or "DE",
        }

    pfand_lines, pfand_total_eur = _build_pfand_summary(items)
    adr_lines = _build_adr_summary(items)
    built_items = _build_items(items)

    total_qty = 0.0
    for it in items:
        q = it.get("qty_actual") if it.get("qty_actual") is not None else it.get("qty_expected")
        try:
            total_qty += float(q or 0)
        except (TypeError, ValueError):
            pass
    total_qty_str = _qty_display(total_qty) if total_qty else ""

    shipping_method_label = SHIPPING_METHOD_LABELS.get(
        delivery.get("shipping_method"), delivery.get("shipping_method") or ""
    )
    incoterms_str = ""
    if delivery.get("incoterms"):
        place = f" {delivery['incoterms_place']}" if delivery.get("incoterms_place") else ""
        incoterms_str = f"{delivery['incoterms']}{place}"

    related_order_number = ""
    related_order = delivery.get("related_order") or {}
    if isinstance(related_order, dict):
        related_order_number = related_order.get("order_number") or ""

    context = {
        "logo_uri": _logo_uri(),
        "doc_label": doc_label,
        "doc_number_label": doc_number_label,
        "recipient_label_main": recipient_label_main,
        "recipient_label_secondary": recipient_label_secondary,
        "secondary_party_name": secondary_party_name,
        "secondary_addr": secondary_addr,
        "today": date.today().strftime("%d.%m.%Y"),
        "termin": _format_date(delivery.get("expected_at")),
        "related_order_number": related_order_number,
        "d": delivery,
        "party": party,
        "source_party": source_party,
        "shipping_addr": shipping_addr,
        "dropshipping": is_dropship,
        "items": built_items,
        "pfand_lines": pfand_lines,
        "pfand_total_eur": pfand_total_eur,
        "adr_lines": adr_lines,
        "total_qty": total_qty_str,
        "total_packages": delivery.get("packages_count") or "",
        "company": company,
        "master_label_1": "Versandart",
        "master_value_1": shipping_method_label or "—",
        "master_label_2": "Incoterms",
        "master_value_2": incoterms_str or "—",
        "master_label_3": "Tracking",
        "master_value_3": delivery.get("tracking_number") or "—",
        "master_label_4": "Gewicht",
        "master_value_4": (f"{delivery['total_weight_kg']} kg" if delivery.get("total_weight_kg") else "—"),
    }

    template = _jinja.get_template("lieferschein.html")
    html_str = template.render(**context)

    css = CSS(filename=str(TEMPLATES_DIR / "lieferschein.css"))
    css_beleg = CSS(filename=str(TEMPLATES_DIR / "beleg.css"))

    buf = BytesIO()
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        target=buf,
        stylesheets=[css_beleg, css],
    )
    return buf.getvalue()
