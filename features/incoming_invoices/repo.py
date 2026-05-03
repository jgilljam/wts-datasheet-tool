"""Eingangsrechnungen — Read-Layer."""

from __future__ import annotations

from typing import Any

from core.db import supabase
from core.utils import sanitize_search


def list_incoming_invoices(
    *,
    statuses: list[str] | None = None,
    supplier_id: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("incoming_invoices")
        .select(
            "*, "
            "supplier:parties!supplier_id(id, legal_name, short_name, type, vat_id), "
            "related_po:purchase_orders!related_po_id(id, po_number, status)"
        )
    )
    if statuses:
        q = q.in_("status", statuses)
    if supplier_id:
        q = q.eq("supplier_id", supplier_id)
    if search:
        s = sanitize_search(search)
        q = q.or_(
            f"supplier_invoice_number.ilike.%{s}%,"
            f"supplier_reference.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    return (
        q.order("invoice_date", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )


def get_incoming_invoice(invoice_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("incoming_invoices")
        .select(
            "*, "
            "supplier:parties!supplier_id(id, legal_name, short_name, type, vat_id), "
            "related_po:purchase_orders!related_po_id(id, po_number, status, ordered_at)"
        )
        .eq("id", invoice_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def list_items(invoice_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("incoming_invoice_items")
        .select("*, matched_article:articles!matched_article_id(id, sku, title_de, unit)")
        .eq("incoming_invoice_id", invoice_id)
        .order("pos_nr")
        .execute()
        .data
    )


def list_events(invoice_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("incoming_invoice_events")
        .select("*")
        .eq("incoming_invoice_id", invoice_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def find_supplier_by_vat_id(vat_id: str) -> dict[str, Any] | None:
    if not vat_id:
        return None
    res = (
        supabase()
        .table("parties")
        .select("id, legal_name, short_name, type, vat_id")
        .eq("vat_id", vat_id.strip().upper().replace(" ", ""))
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def find_supplier_by_name(name: str) -> dict[str, Any] | None:
    """Fuzzy-Suche per ILIKE — gibt den ersten Treffer zurück.

    Fragt legal_name + short_name in zwei separaten Queries ab (statt or_()),
    weil PostgREST-or-Syntax bei Kommata im Suchstring kollidiert.
    """
    if not name or len(name.strip()) < 3:
        return None
    n = name.strip()
    sb = supabase()
    for col in ("legal_name", "short_name"):
        res = (
            sb.table("parties")
            .select("id, legal_name, short_name, type, vat_id")
            .ilike(col, f"%{n}%")
            .eq("type", "supplier")
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    return None


def find_article_by_sku(sku: str) -> dict[str, Any] | None:
    if not sku or len(sku.strip()) < 2:
        return None
    res = (
        supabase()
        .table("articles")
        .select("id, sku, title_de, unit, default_price_cents")
        .eq("sku", sku.strip())
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def find_po_by_number(po_number: str) -> dict[str, Any] | None:
    """Sucht eigene PO anhand der Bestellnummer (z.B. 'BE-2026-0007')."""
    if not po_number or len(po_number.strip()) < 5:
        return None
    res = (
        supabase()
        .table("purchase_orders")
        .select("id, po_number, supplier_id, status, ordered_at")
        .eq("po_number", po_number.strip())
        .maybe_single()
        .execute()
    )
    return res.data if res else None
