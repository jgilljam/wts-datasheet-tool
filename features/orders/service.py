"""Write-Layer für Verkaufs-Aufträge — Mutationen + Audit-Log."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from core.audit import log_event
from core.db import supabase
from core.snapshots import (
    build_invoice_snapshot_payload,
    enrich_items_with_snapshots,
)
from core.utils import ser_value

from . import repo
from .constants import ORDER_LOCKED_STATUSES


def _log(order_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("order_events", "order_id", order_id, event_type, payload)


# ---------- Auftrag ----------

def create_order(data: dict[str, Any]) -> str:
    """Lege einen Auftrag an. `order_number` wird auto-generiert wenn leer."""
    if not data.get("order_number"):
        year = (data.get("ordered_at") or date.today()).year if isinstance(
            data.get("ordered_at"), date
        ) else date.today().year
        data["order_number"] = repo.next_order_number(year)

    payload = {k: ser_value(v) for k, v in data.items() if v is not None and v != ""}

    res = supabase().table("orders").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log(new_id, "created", {"order_number": payload["order_number"]})
    return new_id


def update_order(order_id: str, changes: dict[str, Any]) -> None:
    if not changes:
        return
    payload = {k: ser_value(v) for k, v in changes.items()}
    supabase().table("orders").update(payload).eq("id", order_id).execute()
    _log(order_id, "updated", {"fields": list(changes.keys())})


def update_status(order_id: str, new_status: str, comment: str | None = None) -> None:
    from .constants import ORDER_ALLOWED_TRANSITIONS, ORDER_STATUSES

    if new_status not in ORDER_STATUSES:
        raise ValueError(f"Unbekannter Auftrags-Status: {new_status}")

    cur = (
        supabase()
        .table("orders")
        .select("status")
        .eq("id", order_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    if old_status == new_status:
        return

    allowed = ORDER_ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise PermissionError(
            f"Status-Übergang '{old_status}' → '{new_status}' ist nicht erlaubt. "
            f"Mögliche Übergänge: {sorted(allowed) or 'keine (terminal)'}"
        )

    supabase().table("orders").update({"status": new_status}).eq("id", order_id).execute()
    _log(order_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })


def lock_order(order_id: str) -> None:
    """GoBD-Festschreibung: locked_at + Stammdaten-Snapshots atomar."""
    cur = (
        supabase()
        .table("orders")
        .select("customer_id, billing_address_id, shipping_address_id")
        .eq("id", order_id)
        .single()
        .execute()
    )
    snapshots = build_invoice_snapshot_payload(
        customer_id=cur.data.get("customer_id"),
        billing_address_id=cur.data.get("billing_address_id"),
        shipping_address_id=cur.data.get("shipping_address_id"),
    )
    supabase().table("orders").update({
        "locked_at": datetime.now(timezone.utc).isoformat(),
        **snapshots,
    }).eq("id", order_id).execute()
    _log(order_id, "locked", {})


# ---------- Items ----------

def replace_items(order_id: str, items: list[dict[str, Any]]) -> None:
    """Atomar — delete+insert in einer Transaktion via RPC. Lock-Check serverseitig."""
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: ser_value(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)

    rows = enrich_items_with_snapshots(rows)

    supabase().rpc("replace_order_items", {
        "p_order_id": order_id,
        "p_items": rows,
    }).execute()

    _log(order_id, "items_replaced", {"count": len(rows)})
    _recompute_totals(order_id)


def _recompute_totals(order_id: str) -> None:
    """Berechnet total_net_cents, tax_total_cents, discount_total_cents aus den Items."""
    items = (
        supabase()
        .table("order_items")
        .select("qty, unit_price_cents, tax_rate, discount_pct, line_total_cents, tax_amount_cents")
        .eq("order_id", order_id)
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

        gross_line = qty * unit_price                    # ohne Rabatt, ohne USt (in Cents)
        disc_cents = gross_line * disc_pct / 100.0
        net_line = gross_line - disc_cents
        tax_line = net_line * tax_rate / 100.0

        net += int(round(net_line))
        tax += int(round(tax_line))
        discount += int(round(disc_cents))

    supabase().table("orders").update({
        "total_net_cents": net,
        "tax_total_cents": tax,
        "discount_total_cents": discount,
    }).eq("id", order_id).execute()


# ---------- Lieferung aus Auftrag erzeugen ----------

def create_delivery_from_order(order_id: str) -> str:
    """Erzeugt eine outbound-Lieferung mit allen Items des Auftrags.

    Returns: delivery_id
    """
    from features.deliveries import service as delivery_service
    from features.deliveries.repo import next_delivery_number

    order = repo.get_order(order_id)
    if not order:
        raise ValueError(f"Auftrag {order_id} nicht gefunden")
    items = repo.list_order_items(order_id)

    year = (order.get("ordered_at") or date.today().isoformat())[:4]
    delivery_payload = {
        "direction": "outbound",
        "delivery_number": next_delivery_number("outbound", int(year)),
        "party_id": order["customer_id"],
        "shipping_address_id": order.get("shipping_address_id"),
        "billing_address_id": order.get("billing_address_id"),
        "related_order_id": order_id,
        "customer_reference": order.get("customer_reference"),
        "expected_at": order.get("due_date"),
        "termin_type": "fix",
        "incoterms": order.get("incoterms"),
        "incoterms_place": order.get("incoterms_place"),
        "status": "draft",
    }
    delivery_id = delivery_service.create_delivery(delivery_payload)

    # Items aus Auftrag in delivery_items übernehmen
    delivery_items: list[dict[str, Any]] = []
    for it in items:
        delivery_items.append({
            "pos_nr": it.get("pos_nr"),
            "article_id": it.get("article_id"),
            "description_override": it.get("description_override"),
            "qty_expected": float(it.get("qty") or 0),
            "unit": it.get("unit") or "Stk",
        })
    if delivery_items:
        delivery_service.replace_items(delivery_id, delivery_items)

    _log(order_id, "delivery_created", {
        "delivery_id": delivery_id,
        "delivery_number": delivery_payload["delivery_number"],
        "items": len(delivery_items),
    })
    return delivery_id
