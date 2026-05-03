"""Read-Layer für Rechnungen + Company-Settings."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from core.db import supabase


# ---------- Rechnungen ----------

def list_invoices(
    *,
    statuses: list[str] | None = None,
    customer_id: str | None = None,
    issued_from: date | None = None,
    issued_to: date | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("invoices")
        .select(
            "*, "
            "customer:parties!customer_id(id, legal_name, short_name, type, vat_id, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "related_order:orders!related_order_id(id, order_number)"
        )
    )
    if statuses:
        q = q.in_("status", statuses)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if issued_from:
        q = q.gte("issued_at", issued_from.isoformat())
    if issued_to:
        q = q.lte("issued_at", issued_to.isoformat())
    if search:
        s = search.replace("%", r"\%").replace(",", " ")
        q = q.or_(
            f"invoice_number.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    rows = (
        q.order("issued_at", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )
    # Storno-Verkettung separat laden (Self-Join-Workaround)
    reverses_ids = {r["reverses_id"] for r in rows if r.get("reverses_id")}
    if reverses_ids:
        refs = (
            supabase()
            .table("invoices")
            .select("id, invoice_number")
            .in_("id", list(reverses_ids))
            .execute()
            .data
        )
        ref_map = {r["id"]: r for r in refs}
        for r in rows:
            if r.get("reverses_id") and r["reverses_id"] in ref_map:
                r["reverses"] = ref_map[r["reverses_id"]]
    return rows


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("invoices")
        .select(
            "*, "
            "customer:parties!customer_id(id, legal_name, short_name, type, vat_id, tax_number, "
            "is_reverse_charge_eligible, default_currency, payment_terms_days), "
            "shipping_address:addresses!shipping_address_id(*), "
            "billing_address:addresses!billing_address_id(*), "
            "related_order:orders!related_order_id(id, order_number, status)"
        )
        .eq("id", invoice_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return None
    inv = res.data

    # Self-Joins (reverses/reversed_by) separat laden — PostgREST kann zwei FKs auf
    # dieselbe Tabelle nicht ohne expliziten Constraint-Namen embeden
    if inv.get("reverses_id"):
        ref = (
            supabase()
            .table("invoices")
            .select("id, invoice_number, issued_at")
            .eq("id", inv["reverses_id"])
            .maybe_single()
            .execute()
        )
        inv["reverses"] = ref.data if ref else None
    if inv.get("reversed_by_id"):
        ref = (
            supabase()
            .table("invoices")
            .select("id, invoice_number, issued_at")
            .eq("id", inv["reversed_by_id"])
            .maybe_single()
            .execute()
        )
        inv["reversed_by"] = ref.data if ref else None
    return inv


def list_invoice_items(invoice_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("invoice_items")
        .select("*, articles(id, sku, title_de, unit, default_price_cents)")
        .eq("invoice_id", invoice_id)
        .order("pos_nr")
        .execute()
        .data
    )


def list_invoice_events(invoice_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("invoice_events")
        .select("*")
        .eq("invoice_id", invoice_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def next_invoice_number(year: int) -> str:
    """`RE-2026-0001` — atomar via Postgres-RPC, race-condition-frei.

    Vergibt fortlaufend pro Jahr; lückenlos solange die Nummernvergabe in
    derselben Transaktion wie der Issue-Update liegt (siehe service.issue_invoice).
    """
    res = supabase().rpc("next_belegnummer", {
        "p_belegart": "invoice", "p_jahr": year,
    }).execute()
    return res.data


def list_invoices_for_order(order_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("invoices")
        .select("id, invoice_number, status, issued_at, total_net_cents, tax_total_cents, reverses_id")
        .eq("related_order_id", order_id)
        .order("issued_at", desc=False, nullsfirst=False)
        .execute()
        .data
    )


# ---------- Company-Settings ----------

@st.cache_data(ttl=300)
def get_company_settings() -> dict[str, Any]:
    """Lädt die Company-Settings (single-row).

    Returns: dict mit Defaults wenn Tabelle leer ist.
    """
    res = (
        supabase()
        .table("company_settings")
        .select("*")
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]
    return {
        "legal_name": "Weber Trading & Service",
        "street": "Kaiserstraße 35",
        "zip": "41061",
        "city": "Mönchengladbach",
        "country_code": "DE",
        "email": "info@wts-trading.de",
    }


def clear_company_settings_cache() -> None:
    get_company_settings.clear()
