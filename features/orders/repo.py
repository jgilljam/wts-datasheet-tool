"""Read-Layer für Verkaufs-Aufträge — pure Supabase-Queries."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from core.db import supabase


def list_orders(
    *,
    statuses: list[str] | None = None,
    customer_id: str | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("orders")
        .select(
            "*, "
            "customer:parties!customer_id(id, legal_name, short_name, type, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days)"
        )
    )
    if statuses:
        q = q.in_("status", statuses)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if due_from:
        q = q.gte("due_date", due_from.isoformat())
    if due_to:
        q = q.lte("due_date", due_to.isoformat())
    if search:
        s = search.replace("%", r"\%").replace(",", " ")
        q = q.or_(
            f"order_number.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    return (
        q.order("ordered_at", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )


def get_order(order_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("orders")
        .select(
            "*, "
            "customer:parties!customer_id(id, legal_name, short_name, type, vat_id, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "shipping_address:addresses!shipping_address_id(*), "
            "billing_address:addresses!billing_address_id(*)"
        )
        .eq("id", order_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def list_order_items(order_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("order_items")
        .select(
            "*, articles(id, sku, title_de, unit, default_price_cents)"
        )
        .eq("order_id", order_id)
        .order("pos_nr")
        .execute()
        .data
    )


def list_order_events(order_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("order_events")
        .select("*")
        .eq("order_id", order_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def list_deliveries_for_order(order_id: str) -> list[dict[str, Any]]:
    """Alle Lieferungen, die diesem Auftrag verknüpft sind (Smart-Button-Counter)."""
    return (
        supabase()
        .table("deliveries")
        .select("id, delivery_number, direction, status, expected_at")
        .eq("related_order_id", order_id)
        .order("expected_at", desc=False, nullsfirst=False)
        .execute()
        .data
    )


def next_order_number(year: int) -> str:
    """`AB-2026-0001` (AB = Auftragsbestätigung)."""
    prefix = f"AB-{year}-"
    res = (
        supabase()
        .table("orders")
        .select("order_number")
        .like("order_number", f"{prefix}%")
        .order("order_number", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return f"{prefix}0001"
    last = res.data[0]["order_number"]
    try:
        n = int(last.rsplit("-", 1)[-1]) + 1
    except (ValueError, IndexError):
        n = 1
    return f"{prefix}{n:04d}"
