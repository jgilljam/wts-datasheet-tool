"""Read-Layer für Lieferungen — pure Supabase-Queries, keine UI."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from core.db import supabase
from core.utils import sanitize_search
from core.snapshots import apply_snapshot_to_items, apply_snapshot_view


# ---------- Lieferungen ----------

def list_deliveries(
    *,
    directions: list[str] | None = None,
    statuses: list[str] | None = None,
    expected_from: date | None = None,
    expected_to: date | None = None,
    party_id: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Listet Lieferungen mit eingebetteter Party (legal_name)."""
    q = (
        supabase()
        .table("deliveries")
        .select(
            "*, "
            "parties!party_id(id, legal_name, short_name, type), "
            "source_party:parties!source_party_id(id, legal_name, short_name, type)"
        )
    )
    if directions:
        q = q.in_("direction", directions)
    if statuses:
        q = q.in_("status", statuses)
    if expected_from:
        q = q.gte("expected_at", expected_from.isoformat())
    if expected_to:
        q = q.lte("expected_at", expected_to.isoformat())
    if party_id:
        q = q.eq("party_id", party_id)
    if search:
        s = sanitize_search(search)
        q = q.or_(
            f"delivery_number.ilike.%{s}%,"
            f"tracking_number.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%,"
            f"notes.ilike.%{s}%"
        )
    return (
        q.order("expected_at", desc=False, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    )


def get_delivery(delivery_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("deliveries")
        .select(
            "*, "
            "parties!party_id(id, legal_name, short_name, type, vat_id), "
            "source_party:parties!source_party_id(id, legal_name, short_name, type, vat_id), "
            "shipping_address:addresses!shipping_address_id(*), "
            "billing_address:addresses!billing_address_id(*), "
            "related_order:orders!related_order_id(id, order_number, status), "
            "related_po:purchase_orders!related_po_id(id, po_number, status)"
        )
        .eq("id", delivery_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return None
    return apply_snapshot_view(res.data, party_field="parties")


def list_delivery_items(delivery_id: str) -> list[dict[str, Any]]:
    items = (
        supabase()
        .table("delivery_items")
        .select(
            "*, articles(id, sku, title_de, unit, "
            "adr_un_nr, adr_class, adr_proper_name, adr_net_kg_per_unit, "
            "is_pfand, pfand_per_unit_cents)"
        )
        .eq("delivery_id", delivery_id)
        .order("pos_nr")
        .execute()
        .data
    ) or []
    parent = (
        supabase().table("deliveries").select("locked_at").eq("id", delivery_id)
        .maybe_single().execute()
    )
    is_frozen = bool(parent and parent.data and parent.data.get("locked_at"))
    return apply_snapshot_to_items(items, is_frozen=is_frozen)


def list_delivery_documents(delivery_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("delivery_documents")
        .select("*")
        .eq("delivery_id", delivery_id)
        .order("uploaded_at", desc=True)
        .execute()
        .data
    )


def list_delivery_events(delivery_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("delivery_events")
        .select("*")
        .eq("delivery_id", delivery_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def next_delivery_number(direction: str, year: int) -> str:
    """Atomar via Postgres-RPC — `L-2026-0001` outbound, `WE-2026-0001` inbound."""
    belegart = "delivery_outbound" if direction == "outbound" else "delivery_inbound"
    res = supabase().rpc("next_belegnummer", {
        "p_belegart": belegart, "p_jahr": year,
    }).execute()
    return res.data


# ---------- Stammdaten für Dropdowns ----------

@st.cache_data(ttl=60)
def list_parties(party_type: str | None = None, only_active: bool = True) -> list[dict[str, Any]]:
    """Liefert Parties als Liste {id, legal_name, short_name, type}."""
    q = supabase().table("parties").select("id, legal_name, short_name, type, is_active")
    if only_active:
        q = q.eq("is_active", True)
    if party_type:
        # type = 'customer' soll auch 'both' liefern (und umgekehrt)
        if party_type in ("customer", "supplier"):
            q = q.in_("type", [party_type, "both"])
        else:
            q = q.eq("type", party_type)
    return q.order("legal_name").execute().data


@st.cache_data(ttl=60)
def list_addresses_for_party(party_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("addresses")
        .select("*")
        .eq("party_id", party_id)
        .order("is_default", desc=True)
        .execute()
        .data
    )


@st.cache_data(ttl=60)
def list_articles(only_active: bool = True, limit: int = 1000) -> list[dict[str, Any]]:
    q = supabase().table("articles").select(
        "id, sku, title_de, unit, default_location, "
        "adr_un_nr, adr_class, adr_packing_group, adr_net_kg_per_unit, adr_proper_name, "
        "is_pfand, pfand_per_unit_cents"
    )
    if only_active:
        q = q.eq("is_active", True)
    return q.order("sku").limit(limit).execute().data


def clear_caches() -> None:
    """Nach Stammdaten-Änderungen aufrufen, damit Dropdowns frisch sind."""
    list_parties.clear()
    list_addresses_for_party.clear()
    list_articles.clear()
