"""Eingangsrechnungen — Write-Layer."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from core.audit import log_event
from core.db import supabase
from core.utils import ser_value

from . import repo
from .constants import INCOMING_ALLOWED_TRANSITIONS, INCOMING_STATUSES


def _log(invoice_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("incoming_invoice_events", "incoming_invoice_id", invoice_id, event_type, payload)


# ---------- Storage ----------

def _eur_to_cents(eur: float | None) -> int | None:
    if eur is None:
        return None
    return int(round(float(eur) * 100))


def upload_pdf(invoice_id: str, pdf_bytes: bytes, original_filename: str) -> str:
    """Lädt das Original-PDF in den Bucket. Returns Storage-Pfad."""
    path = f"{invoice_id}/{uuid.uuid4().hex}_{original_filename}"
    supabase().storage.from_("incoming-invoices").upload(
        path=path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf", "upsert": "false"},
    )
    return path


def get_pdf_signed_url(storage_path: str, expires_in: int = 3600) -> str:
    res = supabase().storage.from_("incoming-invoices").create_signed_url(
        storage_path, expires_in
    )
    return res.get("signedURL") or res.get("signed_url") or ""


# ---------- Parsen aus OCR-Output ----------

def _parse_date_iso(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        # Zulässige Formate: YYYY-MM-DD oder DD.MM.YYYY
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s
        if re.match(r"^\d{2}\.\d{2}\.\d{4}$", s):
            d, m, y = s.split(".")
            return f"{y}-{m}-{d}"
    except Exception:
        return None
    return None


def create_from_ocr(
    *,
    parsed: Any,
    pdf_bytes: bytes,
    pdf_filename: str,
    auto_match: bool = True,
) -> str:
    """Erstellt eine Eingangsrechnung aus dem OCR-Parse-Ergebnis.

    Args:
        parsed: IncomingInvoiceParsed (Pydantic)
        pdf_bytes: rohe PDF-Bytes
        pdf_filename: Dateiname
        auto_match: wenn True, versuch Lieferant + Items + PO automatisch zu mappen

    Returns: ID der neuen Eingangsrechnung
    """
    parsed_dict = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)

    # Lieferant matchen oder anlegen
    supplier_id: str | None = None
    if auto_match:
        if vat := parsed_dict.get("supplier_vat_id"):
            existing = repo.find_supplier_by_vat_id(vat)
            if existing:
                supplier_id = existing["id"]
        if not supplier_id and parsed_dict.get("supplier_name"):
            existing = repo.find_supplier_by_name(parsed_dict["supplier_name"])
            if existing:
                supplier_id = existing["id"]
    if not supplier_id:
        # Fallback: neuen Lieferanten anlegen
        new_supplier = (
            supabase()
            .table("parties")
            .insert({
                "legal_name": parsed_dict.get("supplier_name") or "Unbekannter Lieferant",
                "type": "supplier",
                "vat_id": parsed_dict.get("supplier_vat_id") or None,
                "notes": (parsed_dict.get("supplier_address") or None),
            })
            .execute()
        )
        supplier_id = new_supplier.data[0]["id"]

    # Verknüpfte PO suchen
    related_po_id: str | None = None
    if auto_match:
        ref = parsed_dict.get("customer_reference") or ""
        # Suche nach BE-Pattern
        match = re.search(r"BE[- ]?\d{4}[- ]?\d{4}", ref)
        if match:
            po_nr = match.group(0).replace(" ", "-")
            po = repo.find_po_by_number(po_nr)
            if po:
                related_po_id = po["id"]

    # Header-Insert
    invoice_date = _parse_date_iso(parsed_dict.get("invoice_date"))
    due_date = _parse_date_iso(parsed_dict.get("due_date"))
    service_date = _parse_date_iso(parsed_dict.get("service_date"))

    payload = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": parsed_dict.get("invoice_number") or "?",
        "invoice_date": invoice_date,
        "due_date": due_date,
        "service_date": service_date,
        "currency": parsed_dict.get("currency") or "EUR",
        "total_net_cents": _eur_to_cents(parsed_dict.get("total_net_eur")),
        "tax_total_cents": _eur_to_cents(parsed_dict.get("tax_total_eur")),
        "gross_total_cents": _eur_to_cents(parsed_dict.get("gross_total_eur")),
        "status": "received",
        "related_po_id": related_po_id,
        "supplier_reference": parsed_dict.get("supplier_reference"),
        "customer_reference": parsed_dict.get("customer_reference"),
        "ocr_payload": parsed_dict,
        "ocr_confidence": parsed_dict.get("confidence") or "medium",
    }
    payload = {k: ser_value(v) for k, v in payload.items() if v is not None and v != ""}

    res = supabase().table("incoming_invoices").insert(payload).execute()
    invoice_id = res.data[0]["id"]

    # PDF hochladen
    try:
        path = upload_pdf(invoice_id, pdf_bytes, pdf_filename)
        supabase().table("incoming_invoices").update({
            "pdf_storage_path": path,
            "pdf_filename": pdf_filename,
        }).eq("id", invoice_id).execute()
    except Exception as exc:
        # Beim Storage-Fehler nicht abbrechen — Header bleibt erhalten
        _log(invoice_id, "pdf_upload_failed", {"error": str(exc)})

    # Items mit Article-Match
    item_rows: list[dict[str, Any]] = []
    for it in parsed_dict.get("items") or []:
        sku = (it.get("sku") or "").strip()
        article_id = None
        match_conf = None
        if auto_match and sku:
            article = repo.find_article_by_sku(sku)
            if article:
                article_id = article["id"]
                match_conf = "exact_sku"
        unit_price_cents = _eur_to_cents(it.get("unit_price_eur"))
        line_total_cents = _eur_to_cents(it.get("line_total_eur"))
        tax_rate = float(it.get("tax_rate_pct") or 19)
        line_net = line_total_cents or 0
        tax_amount = int(round(line_net * tax_rate / 100.0))
        item_rows.append({
            "pos_nr": int(it.get("pos_nr") or len(item_rows) + 1),
            "sku": sku or None,
            "description": it.get("description"),
            "qty": float(it.get("qty") or 0),
            "unit": it.get("unit") or "Stk",
            "unit_price_cents": unit_price_cents,
            "line_total_cents": line_total_cents,
            "tax_rate": tax_rate,
            "tax_amount_cents": tax_amount,
            "discount_pct": float(it.get("discount_pct") or 0),
            "matched_article_id": article_id,
            "match_confidence": match_conf,
        })

    if item_rows:
        supabase().rpc("replace_incoming_invoice_items", {
            "p_invoice_id": invoice_id,
            "p_items": item_rows,
        }).execute()

    _log(invoice_id, "ocr_imported", {
        "supplier_invoice_number": payload.get("supplier_invoice_number"),
        "supplier_id": supplier_id,
        "related_po_id": related_po_id,
        "items_count": len(item_rows),
        "confidence": payload.get("ocr_confidence"),
    })
    return invoice_id


# ---------- Mutationen ----------

def update_invoice(invoice_id: str, changes: dict[str, Any]) -> None:
    if not changes:
        return
    payload = {k: ser_value(v) for k, v in changes.items()}
    supabase().table("incoming_invoices").update(payload).eq("id", invoice_id).execute()
    _log(invoice_id, "updated", {"fields": list(changes.keys())})


def replace_items(invoice_id: str, items: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: ser_value(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)
    supabase().rpc("replace_incoming_invoice_items", {
        "p_invoice_id": invoice_id,
        "p_items": rows,
    }).execute()
    _log(invoice_id, "items_replaced", {"count": len(rows)})


def update_status(invoice_id: str, new_status: str, comment: str | None = None) -> None:
    if new_status not in INCOMING_STATUSES:
        raise ValueError(f"Unbekannter Status: {new_status}")
    cur = (
        supabase()
        .table("incoming_invoices")
        .select("status")
        .eq("id", invoice_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    if old_status == new_status:
        return
    allowed = INCOMING_ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise PermissionError(
            f"Übergang '{old_status}' → '{new_status}' nicht erlaubt. "
            f"Mögliche: {sorted(allowed) or 'keine (terminal)'}"
        )
    extra: dict[str, Any] = {"status": new_status}
    if new_status == "paid":
        extra["paid_at"] = datetime.now(timezone.utc).isoformat()
    supabase().table("incoming_invoices").update(extra).eq("id", invoice_id).execute()
    _log(invoice_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })
