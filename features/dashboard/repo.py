"""Dashboard-Aggregationen: KPIs, Zu-tun-Liste, Activity-Feed.

Konsolidiert Zahlen und letzte Events aus orders/purchase_orders/deliveries/invoices,
ohne Duplikat-Logik in der Page.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.db import supabase


# ---------- KPIs (Zähler + Beträge) ----------

def get_kpis() -> dict[str, Any]:
    """Top-level Kennzahlen für KPI-Strip + Action-Cards.

    Returns dict mit:
      - open_orders (int)            — Aufträge in confirmed/in_production/partial/shipped
      - open_invoices (int)          — Rechnungen in issued/partially_paid/overdue
      - open_invoice_amount_cents (int)
      - month_revenue_cents (int)    — Umsatz Brutto im aktuellen Monat
      - prev_month_revenue_cents (int)
      - deliveries_today (int)
      - todo_overdue_invoices (int)
      - todo_orders_to_ship_this_week (int)
      - todo_inbound_pending (int)   — Bestellungen mit Status sent/confirmed/in_production
      - todo_drafts_orders (int)
      - todo_drafts_invoices (int)
    """
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    week_end = today + timedelta(days=7)

    # --- Aufträge ---
    orders = (
        supabase()
        .table("orders")
        .select("id, status, due_date")
        .execute()
        .data
    ) or []
    open_orders = sum(
        1 for o in orders
        if o.get("status") in ("confirmed", "in_production", "partial", "shipped")
    )
    todo_drafts_orders = sum(1 for o in orders if o.get("status") == "draft")
    todo_orders_to_ship_this_week = sum(
        1 for o in orders
        if o.get("status") in ("confirmed", "in_production", "partial")
        and o.get("due_date")
        and date.fromisoformat(o["due_date"]) <= week_end
    )

    # --- Bestellungen ---
    pos = (
        supabase()
        .table("purchase_orders")
        .select("id, status")
        .execute()
        .data
    ) or []
    todo_inbound_pending = sum(
        1 for p in pos if p.get("status") in ("sent", "confirmed", "in_production")
    )

    # --- Rechnungen ---
    invs = (
        supabase()
        .table("invoices")
        .select(
            "id, status, due_date, issued_at, total_net_cents, tax_total_cents, "
            "paid_amount_cents, reverses_id"
        )
        .execute()
        .data
    ) or []
    open_invoice_amount_cents = 0
    open_invoices = 0
    todo_overdue_invoices = 0
    todo_drafts_invoices = 0
    month_revenue_cents = 0
    prev_month_revenue_cents = 0
    for r in invs:
        st_ = r.get("status")
        if st_ == "draft":
            todo_drafts_invoices += 1
        if st_ in ("issued", "partially_paid", "overdue"):
            open_invoices += 1
            brutto = int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
            paid = int(r.get("paid_amount_cents") or 0)
            open_invoice_amount_cents += max(0, brutto - paid)
            if r.get("due_date") and date.fromisoformat(r["due_date"]) < today:
                todo_overdue_invoices += 1
        if st_ in ("issued", "partially_paid", "paid") and not r.get("reverses_id"):
            issued = r.get("issued_at")
            if not issued:
                continue
            d = date.fromisoformat(issued[:10])
            brutto = int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
            if d >= month_start:
                month_revenue_cents += brutto
            elif prev_month_start <= d <= prev_month_end:
                prev_month_revenue_cents += brutto

    # --- Lieferungen heute (expected_at fällt auf heute) ---
    deliveries_today_count = 0
    deliv_rows = (
        supabase()
        .table("deliveries")
        .select("id, expected_at")
        .gte("expected_at", today.isoformat())
        .lt("expected_at", (today + timedelta(days=1)).isoformat())
        .limit(500)
        .execute()
        .data
    ) or []
    deliveries_today_count = len(deliv_rows)

    return {
        "open_orders": open_orders,
        "open_invoices": open_invoices,
        "open_invoice_amount_cents": open_invoice_amount_cents,
        "month_revenue_cents": month_revenue_cents,
        "prev_month_revenue_cents": prev_month_revenue_cents,
        "deliveries_today": deliveries_today_count,
        "todo_overdue_invoices": todo_overdue_invoices,
        "todo_orders_to_ship_this_week": todo_orders_to_ship_this_week,
        "todo_inbound_pending": todo_inbound_pending,
        "todo_drafts_orders": todo_drafts_orders,
        "todo_drafts_invoices": todo_drafts_invoices,
    }


# ---------- Activity-Feed ----------

def list_recent_activity(limit: int = 12) -> list[dict[str, Any]]:
    """Mergt die letzten Events aus allen 4 Belegtabellen, sortiert nach `at` desc.

    Liefert Liste mit Feldern: at, kind ('order'/'po'/'invoice'/'delivery'),
    event_type, actor_label, payload, ref_id, ref_number (Auftragsnr/RE-Nr/...).
    """
    fetch_limit = max(limit * 2, 24)

    # 1) Order-Events + Auftragsnummer joinen
    order_events = (
        supabase()
        .table("order_events")
        .select("id, at, event_type, actor_label, payload, order_id, "
                "orders!order_id(order_number)")
        .order("at", desc=True)
        .limit(fetch_limit)
        .execute()
        .data
    ) or []

    po_events = (
        supabase()
        .table("po_events")
        .select("id, at, event_type, actor_label, payload, po_id, "
                "purchase_orders!po_id(po_number)")
        .order("at", desc=True)
        .limit(fetch_limit)
        .execute()
        .data
    ) or []

    invoice_events = (
        supabase()
        .table("invoice_events")
        .select("id, at, event_type, actor_label, payload, invoice_id, "
                "invoices!invoice_id(invoice_number)")
        .order("at", desc=True)
        .limit(fetch_limit)
        .execute()
        .data
    ) or []

    delivery_events = (
        supabase()
        .table("delivery_events")
        .select("id, at, event_type, actor_label, payload, delivery_id, "
                "deliveries!delivery_id(delivery_number)")
        .order("at", desc=True)
        .limit(fetch_limit)
        .execute()
        .data
    ) or []

    merged: list[dict[str, Any]] = []
    for ev in order_events:
        merged.append({
            **ev,
            "kind": "order",
            "ref_id": ev.get("order_id"),
            "ref_number": (ev.get("orders") or {}).get("order_number"),
        })
    for ev in po_events:
        merged.append({
            **ev,
            "kind": "po",
            "ref_id": ev.get("po_id"),
            "ref_number": (ev.get("purchase_orders") or {}).get("po_number"),
        })
    for ev in invoice_events:
        merged.append({
            **ev,
            "kind": "invoice",
            "ref_id": ev.get("invoice_id"),
            "ref_number": (ev.get("invoices") or {}).get("invoice_number"),
        })
    for ev in delivery_events:
        merged.append({
            **ev,
            "kind": "delivery",
            "ref_id": ev.get("delivery_id"),
            "ref_number": (ev.get("deliveries") or {}).get("delivery_number"),
        })

    merged.sort(key=lambda e: e.get("at") or "", reverse=True)
    return merged[:limit]


# ---------- Stille Hilfen ----------

def _ago(at_iso: str | None) -> str:
    """'vor 5 min' / 'vor 2 h' / 'vor 3 Tagen' / Datum."""
    if not at_iso:
        return ""
    try:
        dt = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return at_iso[:16].replace("T", " ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = (now - dt).total_seconds()
    if diff < 60:
        return "gerade eben"
    if diff < 3600:
        return f"vor {int(diff / 60)} min"
    if diff < 86400:
        return f"vor {int(diff / 3600)} h"
    if diff < 86400 * 7:
        return f"vor {int(diff / 86400)} Tagen"
    return dt.astimezone().strftime("%d.%m. %H:%M")
