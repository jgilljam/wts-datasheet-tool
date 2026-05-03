"""Read-Layer für Angebote — pure Supabase-Queries."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.db import supabase


def list_quotations(
    *,
    statuses: list[str] | None = None,
    customer_id: str | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("quotations")
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
    if valid_from:
        q = q.gte("valid_until", valid_from.isoformat())
    if valid_to:
        q = q.lte("valid_until", valid_to.isoformat())
    if search:
        s = search.replace("%", r"\%").replace(",", " ")
        q = q.or_(
            f"quotation_number.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    return (
        q.order("quoted_at", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )


def get_quotation(quotation_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("quotations")
        .select(
            "*, "
            "customer:parties!customer_id(id, legal_name, short_name, type, vat_id, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "shipping_address:addresses!shipping_address_id(*), "
            "billing_address:addresses!billing_address_id(*), "
            "converted_to_order:orders!converted_to_order_id(id, order_number, status)"
        )
        .eq("id", quotation_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def list_quotation_items(quotation_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("quotation_items")
        .select("*, articles(id, sku, title_de, unit, default_price_cents)")
        .eq("quotation_id", quotation_id)
        .order("pos_nr")
        .execute()
        .data
    )


def list_quotation_events(quotation_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("quotation_events")
        .select("*")
        .eq("quotation_id", quotation_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def next_quotation_number(year: int) -> str:
    """`AN-2026-0001` — atomar via belegnummer_counter (siehe 0007_gobd_hardening.sql)."""
    res = supabase().rpc("next_belegnummer", {
        "p_belegart": "quotation", "p_jahr": year,
    }).execute()
    return res.data
