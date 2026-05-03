"""OP-Liste & Mahnungen — Read-Layer."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.db import supabase


def list_open_invoices(
    *,
    customer_id: str | None = None,
    min_days_overdue: int | None = None,
    dunning_level: int | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Liefert offene Rechnungen (issued/partially_paid/overdue) mit positivem Open-Balance.

    Sortiert nach due_date asc — älteste zuerst.
    """
    q = (
        supabase()
        .table("invoices")
        .select(
            "id, invoice_number, status, issued_at, due_date, "
            "total_net_cents, tax_total_cents, paid_amount_cents, "
            "current_dunning_level, last_dunning_at, customer_reference, "
            "customer:parties!customer_id(id, legal_name, short_name)"
        )
        .in_("status", ["issued", "partially_paid", "overdue"])
    )
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if dunning_level is not None:
        q = q.eq("current_dunning_level", dunning_level)
    rows = q.order("due_date", desc=False, nullsfirst=False).limit(limit).execute().data

    today = date.today()
    enriched: list[dict[str, Any]] = []
    for r in rows:
        gross = int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        paid = int(r.get("paid_amount_cents") or 0)
        open_cents = max(0, gross - paid)
        if open_cents <= 0:
            continue
        due = r.get("due_date")
        if due:
            try:
                due_date = date.fromisoformat(due[:10]) if isinstance(due, str) else due
                days_overdue = (today - due_date).days
            except ValueError:
                days_overdue = 0
        else:
            days_overdue = 0
        if min_days_overdue is not None and days_overdue < min_days_overdue:
            continue
        r["_gross_cents"] = gross
        r["_open_cents"] = open_cents
        r["_days_overdue"] = days_overdue
        enriched.append(r)
    return enriched


def list_dunnings_for_invoice(invoice_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("invoice_dunnings")
        .select("*")
        .eq("invoice_id", invoice_id)
        .order("level")
        .execute()
        .data
    )


def get_dunning(dunning_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("invoice_dunnings")
        .select("*")
        .eq("id", dunning_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None
