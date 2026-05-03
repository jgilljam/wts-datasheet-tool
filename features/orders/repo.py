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


def get_fulfillment_balance(order_id: str) -> list[dict[str, Any]]:
    """Restmengen-Bilanz pro Auftragsposition.

    Aggregiert alle Lieferungen (delivery_items.qty_actual oder qty_expected) und
    Rechnungen (order_items.qty_invoiced) und stellt sie der bestellten Menge
    gegenüber.

    Returns: Liste von dicts mit:
      - pos_nr, sku, title, qty (bestellt)
      - delivered (Summe qty_actual über alle deliveries)
      - invoiced  (qty_invoiced auf order_items)
      - open_delivery, open_invoice
      - status_delivery, status_invoice ('open' | 'partial' | 'done')
    """
    items = (
        supabase()
        .table("order_items")
        .select("pos_nr, qty, qty_invoiced, article_id, description_override, "
                "articles(id, sku, title_de)")
        .eq("order_id", order_id)
        .order("pos_nr")
        .execute()
        .data
    ) or []
    if not items:
        return []

    # Lieferungen für den Auftrag, die als „erfüllt" zählen
    fulfilled_outbound = ["handed_to_carrier", "in_transit", "delivered"]
    deliveries = (
        supabase()
        .table("deliveries")
        .select("id")
        .eq("related_order_id", order_id)
        .in_("status", fulfilled_outbound)
        .execute()
        .data
    ) or []
    delivery_ids = [d["id"] for d in deliveries]

    delivered_by_article: dict[str, float] = {}
    if delivery_ids:
        delv_items = (
            supabase()
            .table("delivery_items")
            .select("article_id, qty_actual, qty_expected")
            .in_("delivery_id", delivery_ids)
            .execute()
            .data
        ) or []
        for it in delv_items:
            aid = it.get("article_id")
            if not aid:
                continue
            qty = float(it.get("qty_actual") or it.get("qty_expected") or 0)
            delivered_by_article[aid] = delivered_by_article.get(aid, 0.0) + qty

    rows: list[dict[str, Any]] = []
    for it in items:
        a = it.get("articles") or {}
        ordered = float(it.get("qty") or 0)
        delivered = delivered_by_article.get(it.get("article_id"), 0.0) if it.get("article_id") else 0.0
        invoiced = float(it.get("qty_invoiced") or 0)
        open_delv = max(ordered - delivered, 0.0)
        open_inv = max(ordered - invoiced, 0.0)

        def _status(done_qty: float, total: float) -> str:
            if total <= 0:
                return "—"
            if done_qty <= 0:
                return "open"
            if done_qty + 1e-6 >= total:
                return "done"
            return "partial"

        rows.append({
            "pos_nr": it.get("pos_nr"),
            "sku": a.get("sku"),
            "title": it.get("description_override") or a.get("title_de") or "",
            "qty": ordered,
            "delivered": delivered,
            "invoiced": invoiced,
            "open_delivery": open_delv,
            "open_invoice": open_inv,
            "status_delivery": _status(delivered, ordered),
            "status_invoice": _status(invoiced, ordered),
        })
    return rows


def next_order_number(year: int) -> str:
    """`AB-2026-0001` — atomar via Postgres-RPC."""
    res = supabase().rpc("next_belegnummer", {
        "p_belegart": "order", "p_jahr": year,
    }).execute()
    return res.data
