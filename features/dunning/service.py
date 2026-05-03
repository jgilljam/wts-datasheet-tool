"""Mahnwesen — Service-Layer."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.audit import log_event
from core.db import supabase

from .constants import DEFAULT_FEES_CENTS, DEFAULT_INTEREST_RATE_PCT


def _log(invoice_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("invoice_events", "invoice_id", invoice_id, event_type, payload)


def _calc_interest_cents(
    open_cents: int,
    days_overdue: int,
    rate_pct: float = DEFAULT_INTEREST_RATE_PCT,
) -> int:
    """Verzugszinsen — vereinfacht: open * rate * days/365.

    BGB §288 Abs.2 (B2B): Basiszinssatz + 9 Punkte. Konservativer Default 9 %.
    """
    if days_overdue <= 0:
        return 0
    interest = open_cents * (rate_pct / 100.0) * (days_overdue / 365.0)
    return int(round(interest))


def _get_company_fees() -> dict[int, int]:
    res = (
        supabase()
        .table("company_settings")
        .select("dunning_fee_l1_cents, dunning_fee_l2_cents, dunning_fee_l3_cents")
        .limit(1)
        .execute()
    )
    if res.data:
        s = res.data[0]
        return {
            1: int(s.get("dunning_fee_l1_cents") or DEFAULT_FEES_CENTS[1]),
            2: int(s.get("dunning_fee_l2_cents") or DEFAULT_FEES_CENTS[2]),
            3: int(s.get("dunning_fee_l3_cents") or DEFAULT_FEES_CENTS[3]),
        }
    return DEFAULT_FEES_CENTS


def _get_grace_days() -> int:
    res = (
        supabase()
        .table("company_settings")
        .select("dunning_grace_days")
        .limit(1)
        .execute()
    )
    if res.data and res.data[0].get("dunning_grace_days"):
        return int(res.data[0]["dunning_grace_days"])
    return 7


def create_dunning(invoice_id: str, level: int, notes: str | None = None) -> str:
    """Erstellt eine Mahnstufe für die Rechnung.

    Berechnet automatisch:
      - amount_due_cents = Open-Balance der Rechnung zum Zeitpunkt
      - fees_cents       = Gebühr für die Stufe
      - interest_cents   = Verzugszinsen (open × rate × days/365)
      - due_date         = heute + grace_days

    Setzt invoices.current_dunning_level + last_dunning_at.
    """
    if level not in (1, 2, 3):
        raise ValueError(f"Mahnstufe muss 1-3 sein, nicht {level}")

    inv = (
        supabase()
        .table("invoices")
        .select("id, invoice_number, status, due_date, current_dunning_level, "
                "total_net_cents, tax_total_cents, paid_amount_cents")
        .eq("id", invoice_id)
        .single()
        .execute()
        .data
    )
    if inv.get("status") in ("paid", "cancelled", "reversed"):
        raise ValueError(f"Rechnung {inv['invoice_number']} ist {inv['status']} — keine Mahnung möglich.")

    cur_level = int(inv.get("current_dunning_level") or 0)
    if level <= cur_level:
        raise ValueError(
            f"Mahnstufe {level} ist nicht höher als aktuell ({cur_level}). "
            f"Nächste Stufe wäre {cur_level + 1}."
        )

    gross = int(inv.get("total_net_cents") or 0) + int(inv.get("tax_total_cents") or 0)
    paid = int(inv.get("paid_amount_cents") or 0)
    open_cents = max(0, gross - paid)
    if open_cents <= 0:
        raise ValueError(f"Rechnung {inv['invoice_number']} hat keine offene Forderung.")

    days_overdue = 0
    if inv.get("due_date"):
        due = date.fromisoformat(inv["due_date"][:10]) if isinstance(inv["due_date"], str) else inv["due_date"]
        days_overdue = max(0, (date.today() - due).days)

    fees_map = _get_company_fees()
    grace = _get_grace_days()
    new_due_date = date.today() + timedelta(days=grace)

    interest_cents = _calc_interest_cents(open_cents, days_overdue)

    res = supabase().table("invoice_dunnings").insert({
        "invoice_id": invoice_id,
        "level": level,
        "due_date": new_due_date.isoformat(),
        "amount_due_cents": open_cents,
        "fees_cents": fees_map.get(level, 0),
        "interest_cents": interest_cents,
        "notes": notes,
        "payload": {
            "days_overdue_at_send": days_overdue,
            "interest_rate_pct": DEFAULT_INTEREST_RATE_PCT,
        },
    }).execute()
    new_id = res.data[0]["id"]

    supabase().table("invoices").update({
        "current_dunning_level": level,
        "last_dunning_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", invoice_id).execute()

    _log(invoice_id, "dunning_sent", {
        "level": level,
        "amount_due_cents": open_cents,
        "fees_cents": fees_map.get(level, 0),
        "interest_cents": interest_cents,
        "new_due_date": new_due_date.isoformat(),
    })
    return new_id


def escalate_dunning(invoice_id: str) -> str:
    """Hebt Rechnung um 1 Stufe — ruft create_dunning(level=current+1) auf."""
    cur = (
        supabase()
        .table("invoices")
        .select("current_dunning_level")
        .eq("id", invoice_id)
        .single()
        .execute()
        .data
    )
    next_level = int(cur.get("current_dunning_level") or 0) + 1
    if next_level > 3:
        raise ValueError("Stufe 3 ist die letzte automatische Mahnstufe — Inkasso erforderlich.")
    return create_dunning(invoice_id, next_level)
