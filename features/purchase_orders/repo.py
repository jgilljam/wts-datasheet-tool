"""Read-Layer für Einkaufs-Bestellungen — pure Supabase-Queries."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.db import supabase
from core.snapshots import apply_snapshot_to_items, apply_snapshot_view


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
    if not res or not res.data:
        return None
    return apply_snapshot_view(res.data, party_field="supplier")


def list_po_items(po_id: str) -> list[dict[str, Any]]:
    items = (
        supabase()
        .table("po_items")
        .select(
            "*, articles(id, sku, title_de, unit, default_price_cents, manufacturer_sku)"
        )
        .eq("po_id", po_id)
        .order("pos_nr")
        .execute()
        .data
    ) or []
    parent = (
        supabase().table("purchase_orders").select("locked_at").eq("id", po_id)
        .maybe_single().execute()
    )
    is_frozen = bool(parent and parent.data and parent.data.get("locked_at"))
    return apply_snapshot_to_items(items, is_frozen=is_frozen)


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
    """`BE-2026-0001` — atomar via Postgres-RPC."""
    res = supabase().rpc("next_belegnummer", {
        "p_belegart": "po", "p_jahr": year,
    }).execute()
    return res.data
