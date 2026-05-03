"""Write-Layer für Angebote — Mutationen, Audit-Log + Convert-to-Order."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from core.audit import log_event
from core.db import supabase
from core.snapshots import (
    build_invoice_snapshot_payload,
    enrich_items_with_snapshots,
)
from core.utils import ser_value

from . import repo
from .constants import (
    DEFAULT_VALIDITY_DAYS,
    QUOTATION_ALLOWED_TRANSITIONS,
    QUOTATION_STATUSES,
)


def _log(quotation_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("quotation_events", "quotation_id", quotation_id, event_type, payload)


# ---------- Angebot ----------

def create_quotation(data: dict[str, Any]) -> str:
    """Lege ein Angebot an. `quotation_number` wird auto-generiert wenn leer."""
    if not data.get("quotation_number"):
        year = (data.get("quoted_at") or date.today()).year if isinstance(
            data.get("quoted_at"), date
        ) else date.today().year
        data["quotation_number"] = repo.next_quotation_number(year)

    if not data.get("quoted_at"):
        data["quoted_at"] = date.today()
    if not data.get("valid_until"):
        quoted = data["quoted_at"]
        if isinstance(quoted, str):
            quoted = date.fromisoformat(quoted[:10])
        data["valid_until"] = quoted + timedelta(days=DEFAULT_VALIDITY_DAYS)

    payload = {k: ser_value(v) for k, v in data.items() if v is not None and v != ""}

    res = supabase().table("quotations").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log(new_id, "created", {"quotation_number": payload["quotation_number"]})
    return new_id


def update_quotation(quotation_id: str, changes: dict[str, Any]) -> None:
    if not changes:
        return
    payload = {k: ser_value(v) for k, v in changes.items()}
    supabase().table("quotations").update(payload).eq("id", quotation_id).execute()
    _log(quotation_id, "updated", {"fields": list(changes.keys())})


def update_status(quotation_id: str, new_status: str, comment: str | None = None) -> None:
    if new_status not in QUOTATION_STATUSES:
        raise ValueError(f"Unbekannter Angebots-Status: {new_status}")

    cur = (
        supabase()
        .table("quotations")
        .select("status")
        .eq("id", quotation_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    if old_status == new_status:
        return

    allowed = QUOTATION_ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise PermissionError(
            f"Status-Übergang '{old_status}' → '{new_status}' ist nicht erlaubt. "
            f"Mögliche Übergänge: {sorted(allowed) or 'keine (terminal)'}"
        )

    extra: dict[str, Any] = {"status": new_status}
    if new_status == "rejected":
        extra["rejected_at"] = datetime.utcnow().isoformat() + "Z"

    # GoBD-Snapshot beim ersten Versand einfrieren — Angebote sind zwar keine
    # steuerbindenden Belege, aber für Konsistenz halten wir die Adresse stabil
    # ab dem Moment, ab dem das Angebot beim Kunden ist.
    if new_status == "sent" and old_status == "draft":
        cur_full = (
            supabase()
            .table("quotations")
            .select(
                "customer_id, billing_address_id, shipping_address_id, customer_snapshot"
            )
            .eq("id", quotation_id)
            .single()
            .execute()
        )
        if not cur_full.data.get("customer_snapshot"):
            snapshots = build_invoice_snapshot_payload(
                customer_id=cur_full.data.get("customer_id"),
                billing_address_id=cur_full.data.get("billing_address_id"),
                shipping_address_id=cur_full.data.get("shipping_address_id"),
            )
            extra.update(snapshots)

    supabase().table("quotations").update(extra).eq("id", quotation_id).execute()
    _log(quotation_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })


# ---------- Items ----------

def replace_items(quotation_id: str, items: list[dict[str, Any]]) -> None:
    """Atomar — delete+insert via RPC."""
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: ser_value(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)

    rows = enrich_items_with_snapshots(rows)

    supabase().rpc("replace_quotation_items", {
        "p_quotation_id": quotation_id,
        "p_items": rows,
    }).execute()

    _log(quotation_id, "items_replaced", {"count": len(rows)})
    _recompute_totals(quotation_id)


def _recompute_totals(quotation_id: str) -> None:
    items = (
        supabase()
        .table("quotation_items")
        .select("qty, unit_price_cents, tax_rate, discount_pct")
        .eq("quotation_id", quotation_id)
        .execute()
        .data
    )
    net = 0
    tax = 0
    discount = 0
    for it in items:
        qty = float(it.get("qty") or 0)
        unit_price = int(it.get("unit_price_cents") or 0)
        disc_pct = float(it.get("discount_pct") or 0)
        tax_rate = float(it.get("tax_rate") or 0)

        gross_line = qty * unit_price
        disc_cents = gross_line * disc_pct / 100.0
        net_line = gross_line - disc_cents
        tax_line = net_line * tax_rate / 100.0

        net += int(round(net_line))
        tax += int(round(tax_line))
        discount += int(round(disc_cents))

    supabase().table("quotations").update({
        "total_net_cents": net,
        "tax_total_cents": tax,
        "discount_total_cents": discount,
    }).eq("id", quotation_id).execute()


# ---------- Convert-to-Order ----------

def convert_to_order(quotation_id: str) -> str:
    """Wandelt das Angebot in einen Auftrag um.

    Lädt das Angebot + alle Items, erzeugt einen Auftrag mit denselben
    Header-Feldern und Items, verlinkt converted_to_order_id und setzt
    Angebots-Status auf 'converted'.

    Returns: ID des neu erstellten Auftrags.
    """
    from features.orders import service as order_service

    q = repo.get_quotation(quotation_id)
    if not q:
        raise ValueError(f"Angebot {quotation_id} nicht gefunden")
    if q.get("converted_to_order_id"):
        raise ValueError(f"Angebot {q['quotation_number']} wurde bereits konvertiert.")

    items = repo.list_quotation_items(quotation_id)

    order_data = {
        "customer_id": q["customer_id"],
        "ordered_at": date.today(),
        "due_date": q.get("valid_until"),
        "customer_reference": q.get("customer_reference"),
        "currency": q.get("currency") or "EUR",
        "incoterms": q.get("incoterms"),
        "incoterms_place": q.get("incoterms_place"),
        "payment_terms_days": q.get("payment_terms_days"),
        "shipping_address_id": q.get("shipping_address_id"),
        "billing_address_id": q.get("billing_address_id"),
        "notes": q.get("notes"),
        "internal_notes": (q.get("internal_notes") or "")
            + (f"\n[aus Angebot {q['quotation_number']}]" if q.get("quotation_number") else ""),
    }
    order_id = order_service.create_order(order_data)

    if items:
        order_items = [
            {
                "pos_nr": it.get("pos_nr"),
                "article_id": it.get("article_id"),
                "description_override": it.get("description_override"),
                "qty": it.get("qty"),
                "unit": it.get("unit") or "Stk",
                "unit_price_cents": it.get("unit_price_cents"),
                "line_total_cents": it.get("line_total_cents"),
                "tax_rate": it.get("tax_rate"),
                "tax_amount_cents": it.get("tax_amount_cents"),
                "discount_pct": it.get("discount_pct"),
            }
            for it in items
        ]
        order_service.replace_items(order_id, order_items)

    supabase().table("quotations").update({
        "status": "converted",
        "converted_to_order_id": order_id,
        "converted_at": datetime.utcnow().isoformat() + "Z",
    }).eq("id", quotation_id).execute()

    _log(quotation_id, "converted", {
        "order_id": order_id,
    })
    return order_id


def reject_quotation(quotation_id: str, reason: str | None = None) -> None:
    """Markiert das Angebot als abgelehnt."""
    supabase().table("quotations").update({
        "status": "rejected",
        "rejected_at": datetime.utcnow().isoformat() + "Z",
        "rejected_reason": reason,
    }).eq("id", quotation_id).execute()
    _log(quotation_id, "rejected", {"reason": reason})


# ---------- Cron: Expired-Markierung ----------

def auto_expire_quotations() -> int:
    """Markiert sent-Angebote, deren valid_until in der Vergangenheit liegt, als expired.

    Returns: Anzahl betroffener Zeilen.
    """
    today = date.today().isoformat()
    res = (
        supabase()
        .table("quotations")
        .update({"status": "expired"})
        .eq("status", "sent")
        .lt("valid_until", today)
        .execute()
    )
    return len(res.data or [])
