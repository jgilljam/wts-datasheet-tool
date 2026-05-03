"""Write-Layer für Einkaufs-Bestellungen — Mutationen + Audit-Log."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from core.audit import log_event
from core.db import supabase
from core.snapshots import (
    build_po_snapshot_payload,
    enrich_items_with_snapshots,
)
from core.utils import ser_value

from . import repo
from .constants import PO_LOCKED_STATUSES


def _log(po_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("po_events", "po_id", po_id, event_type, payload)


# ---------- PO ----------

def create_po(data: dict[str, Any]) -> str:
    """Lege eine Bestellung an. `po_number` wird auto-generiert wenn leer."""
    if not data.get("po_number"):
        year = (data.get("ordered_at") or date.today()).year if isinstance(
            data.get("ordered_at"), date
        ) else date.today().year
        data["po_number"] = repo.next_po_number(year)

    payload = {k: ser_value(v) for k, v in data.items() if v is not None and v != ""}

    res = supabase().table("purchase_orders").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log(new_id, "created", {"po_number": payload["po_number"]})
    return new_id


def update_po(po_id: str, changes: dict[str, Any]) -> None:
    if not changes:
        return
    payload = {k: ser_value(v) for k, v in changes.items()}
    supabase().table("purchase_orders").update(payload).eq("id", po_id).execute()
    _log(po_id, "updated", {"fields": list(changes.keys())})


def update_status(po_id: str, new_status: str, comment: str | None = None) -> None:
    from .constants import PO_ALLOWED_TRANSITIONS, PO_STATUSES

    if new_status not in PO_STATUSES:
        raise ValueError(f"Unbekannter PO-Status: {new_status}")

    cur = (
        supabase()
        .table("purchase_orders")
        .select("status")
        .eq("id", po_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    if old_status == new_status:
        return

    allowed = PO_ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise PermissionError(
            f"Status-Übergang '{old_status}' → '{new_status}' nicht erlaubt. "
            f"Mögliche: {sorted(allowed) or 'keine (terminal)'}"
        )

    supabase().table("purchase_orders").update({"status": new_status}).eq("id", po_id).execute()
    _log(po_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })


def lock_po(po_id: str) -> None:
    """GoBD-Festschreibung: locked_at + Stammdaten-Snapshots atomar."""
    cur = (
        supabase()
        .table("purchase_orders")
        .select("supplier_id, billing_address_id, shipping_address_id")
        .eq("id", po_id)
        .single()
        .execute()
    )
    snapshots = build_po_snapshot_payload(
        supplier_id=cur.data.get("supplier_id"),
        billing_address_id=cur.data.get("billing_address_id"),
        shipping_address_id=cur.data.get("shipping_address_id"),
    )
    supabase().table("purchase_orders").update({
        "locked_at": datetime.now(timezone.utc).isoformat(),
        **snapshots,
    }).eq("id", po_id).execute()
    _log(po_id, "locked", {})


# ---------- Items ----------

def replace_items(po_id: str, items: list[dict[str, Any]]) -> None:
    """Atomar — delete+insert in einer Transaktion via RPC. Lock-Check serverseitig."""
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: ser_value(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)

    rows = enrich_items_with_snapshots(rows)

    supabase().rpc("replace_po_items", {
        "p_po_id": po_id,
        "p_items": rows,
    }).execute()

    _log(po_id, "items_replaced", {"count": len(rows)})
    _recompute_totals(po_id)


def _recompute_totals(po_id: str) -> None:
    items = (
        supabase()
        .table("po_items")
        .select("qty, unit_price_cents, tax_rate, discount_pct")
        .eq("po_id", po_id)
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

    supabase().table("purchase_orders").update({
        "total_net_cents": net,
        "tax_total_cents": tax,
        "discount_total_cents": discount,
    }).eq("id", po_id).execute()


# ---------- Wareneingang aus PO ----------

def create_inbound_delivery_from_po(po_id: str) -> str:
    """Erzeugt eine inbound-Lieferung (Wareneingang) mit allen PO-Items."""
    from features.deliveries import service as delivery_service
    from features.deliveries.repo import next_delivery_number

    po = repo.get_po(po_id)
    if not po:
        raise ValueError(f"Bestellung {po_id} nicht gefunden")
    items = repo.list_po_items(po_id)

    year = (po.get("ordered_at") or date.today().isoformat())[:4]

    # Drop-Ship-Behandlung: wenn source_order existiert UND mind. 1 Item is_dropship=true,
    # ist die Lieferung outbound (Lieferant → Endkunde), nicht inbound.
    has_dropship = any(it.get("is_dropship") for it in items)
    source_order = po.get("source_order")

    if has_dropship and source_order:
        # Streckengeschäft: outbound vom Kunden des verknüpften Auftrags aus Sicht WTS,
        # source_party_id ist der Lieferant
        # Customer aus source_order holen
        order_data = (
            supabase()
            .table("orders")
            .select("customer_id, shipping_address_id, billing_address_id, customer_reference, due_date")
            .eq("id", source_order["id"])
            .single()
            .execute()
            .data
        )
        delivery_payload = {
            "direction": "outbound",
            "delivery_number": next_delivery_number("outbound", int(year)),
            "party_id": order_data["customer_id"],            # Empfänger = Endkunde
            "source_party_id": po["supplier_id"],             # Absender = Lieferant
            "shipping_address_id": order_data.get("shipping_address_id"),
            "billing_address_id": order_data.get("billing_address_id"),
            "related_order_id": source_order["id"],
            "related_po_id": po_id,
            "customer_reference": order_data.get("customer_reference"),
            "expected_at": po.get("confirmed_due_date") or po.get("expected_at") or order_data.get("due_date"),
            "termin_type": "ca",
            "shipping_method": "direktlieferung",
            "incoterms": po.get("incoterms"),
            "incoterms_place": po.get("incoterms_place"),
            "status": "draft",
        }
    else:
        delivery_payload = {
            "direction": "inbound",
            "delivery_number": next_delivery_number("inbound", int(year)),
            "party_id": po["supplier_id"],
            "related_po_id": po_id,
            "customer_reference": po.get("supplier_reference"),
            "expected_at": po.get("confirmed_due_date") or po.get("expected_at"),
            "termin_type": "ca",
            "incoterms": po.get("incoterms"),
            "incoterms_place": po.get("incoterms_place"),
            "status": "announced",
        }

    delivery_id = delivery_service.create_delivery(delivery_payload)

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

    _log(po_id, "delivery_created", {
        "delivery_id": delivery_id,
        "delivery_number": delivery_payload["delivery_number"],
        "direction": delivery_payload["direction"],
        "items": len(delivery_items),
    })
    return delivery_id
