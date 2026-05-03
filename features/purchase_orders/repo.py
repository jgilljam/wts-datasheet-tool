"""Read-Layer für Einkaufs-Bestellungen — pure Supabase-Queries."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.db import supabase


def list_pos(
    *,
    statuses: list[str] | None = None,
    supplier_id: str | None = None,
    expected_from: date | None = None,
    expected_to: date | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("purchase_orders")
        .select(
            "*, "
            "supplier:parties!supplier_id(id, legal_name, short_name, type, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "source_order:orders!source_order_id(id, order_number)"
        )
    )
    if statuses:
        q = q.in_("status", statuses)
    if supplier_id:
        q = q.eq("supplier_id", supplier_id)
    if expected_from:
        q = q.gte("expected_at", expected_from.isoformat())
    if expected_to:
        q = q.lte("expected_at", expected_to.isoformat())
    if search:
        s = search.replace("%", r"\%").replace(",", " ")
        q = q.or_(
            f"po_number.ilike.%{s}%,"
            f"supplier_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    return (
        q.order("ordered_at", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )


def get_po(po_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("purchase_orders")
        .select(
            "*, "
            "supplier:parties!supplier_id(id, legal_name, short_name, type, vat_id, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "source_order:orders!source_order_id(id, order_number, customer_id)"
        )
        .eq("id", po_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def list_po_items(po_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("po_items")
        .select(
            "*, articles(id, sku, title_de, unit, default_price_cents, manufacturer_sku)"
        )
        .eq("po_id", po_id)
        .order("pos_nr")
        .execute()
        .data
    )


def list_po_events(po_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("po_events")
        .select("*")
        .eq("po_id", po_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def list_deliveries_for_po(po_id: str) -> list[dict[str, Any]]:
    """Alle Wareneingänge (inbound-Lieferungen), die dieser PO verknüpft sind."""
    return (
        supabase()
        .table("deliveries")
        .select("id, delivery_number, direction, status, expected_at")
        .eq("related_po_id", po_id)
        .order("expected_at", desc=False, nullsfirst=False)
        .execute()
        .data
    )


def next_po_number(year: int) -> str:
    """`BE-2026-0001` (BE = Bestellung)."""
    prefix = f"BE-{year}-"
    res = (
        supabase()
        .table("purchase_orders")
        .select("po_number")
        .like("po_number", f"{prefix}%")
        .order("po_number", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return f"{prefix}0001"
    last = res.data[0]["po_number"]
    try:
        n = int(last.rsplit("-", 1)[-1]) + 1
    except (ValueError, IndexError):
        n = 1
    return f"{prefix}{n:04d}"
