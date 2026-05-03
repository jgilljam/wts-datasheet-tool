"""Write-Layer für Lieferungen — Mutationen + Audit-Log + Storage-Uploads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import streamlit as st

from core.db import supabase
from core.snapshots import (
    build_delivery_snapshot_payload,
    enrich_items_with_snapshots,
)

from . import repo


# ---------- Audit ----------

def _actor_label() -> str:
    """Wer ist gerade angemeldet? Bis Google-OIDC nur App-Passwort → "Mitarbeiter"."""
    return st.session_state.get("user_email") or "Mitarbeiter"


def _log_event(delivery_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    supabase().table("delivery_events").insert({
        "delivery_id": delivery_id,
        "event_type": event_type,
        "actor_label": _actor_label(),
        "payload": payload or {},
    }).execute()


# ---------- Deliveries ----------

def create_delivery(data: dict[str, Any]) -> str:
    """Lege eine neue Lieferung an. `delivery_number` wird auto-generiert wenn leer."""
    if not data.get("delivery_number"):
        year = (data.get("expected_at") or date.today()).year if isinstance(
            data.get("expected_at"), date
        ) else date.today().year
        data["delivery_number"] = repo.next_delivery_number(data["direction"], year)

    # Datums-/Datetime-Werte serialisieren (Supabase-Client kann date direkt; sicherheitshalber)
    payload = {k: _ser(v) for k, v in data.items() if v is not None and v != ""}

    res = supabase().table("deliveries").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log_event(new_id, "created", {"delivery_number": payload["delivery_number"]})
    return new_id


def update_delivery(delivery_id: str, changes: dict[str, Any]) -> None:
    """Patch-Update. `status` und `locked_at` haben eigene Funktionen."""
    if not changes:
        return
    payload = {k: _ser(v) for k, v in changes.items()}
    supabase().table("deliveries").update(payload).eq("id", delivery_id).execute()
    _log_event(delivery_id, "updated", {"fields": list(changes.keys())})


STOCK_TRIGGER_INBOUND = {"stored"}      # Wareneingang → Bestand +
STOCK_TRIGGER_OUTBOUND = {"handed_to_carrier"}  # Versand → Bestand −

# Statuse, ab denen Lieferungen als „echt erfüllt" zählen — für Parent-Status-Propagation
DELIVERY_FULFILLED_OUTBOUND = {"handed_to_carrier", "in_transit", "delivered"}
DELIVERY_FULFILLED_INBOUND = {"arrived", "partial_received", "received", "inspected", "stored"}


def update_status(delivery_id: str, new_status: str, comment: str | None = None) -> dict[str, int]:
    """Setzt Status, schreibt Audit-Eintrag, verbucht ggf. ins Lager, propagiert
    bei Bedarf den Status an Auftrag/Bestellung.

    Returns: {"booked": N, "parent_updated": "shipped"|"partial"|"received"|None}.
    """
    cur = (
        supabase()
        .table("deliveries")
        .select("status, direction, related_order_id, related_po_id")
        .eq("id", delivery_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    direction = cur.data["direction"]

    supabase().table("deliveries").update({"status": new_status}).eq("id", delivery_id).execute()
    _log_event(delivery_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })

    # Auto-Verbuchung im Lager
    booked = 0
    if direction == "inbound" and new_status in STOCK_TRIGGER_INBOUND:
        booked = _book_stock_for_delivery(delivery_id, "inbound")
    elif direction == "outbound" and new_status in STOCK_TRIGGER_OUTBOUND:
        booked = _book_stock_for_delivery(delivery_id, "outbound")

    # Auto-Propagation an Parent (Auftrag / Bestellung)
    parent_updated: str | None = None
    if direction == "outbound" and new_status in DELIVERY_FULFILLED_OUTBOUND:
        order_id = cur.data.get("related_order_id")
        if order_id:
            parent_updated = _propagate_to_order(order_id)
    elif direction == "inbound" and new_status in DELIVERY_FULFILLED_INBOUND:
        po_id = cur.data.get("related_po_id")
        if po_id:
            parent_updated = _propagate_to_po(po_id)

    return {"booked": booked, "parent_updated": parent_updated}


def _sum_delivered_by_article(
    *, related_field: str, related_id: str, fulfilled_statuses: set[str]
) -> dict[str, float]:
    """Aufsummierte Liefermengen je article_id über alle qualifizierenden Lieferungen."""
    ds = (
        supabase()
        .table("deliveries")
        .select("id")
        .eq(related_field, related_id)
        .in_("status", list(fulfilled_statuses))
        .execute()
        .data
    ) or []
    if not ds:
        return {}
    delivery_ids = [d["id"] for d in ds]
    items = (
        supabase()
        .table("delivery_items")
        .select("article_id, qty_actual, qty_expected, delivery_id")
        .in_("delivery_id", delivery_ids)
        .execute()
        .data
    ) or []
    by_art: dict[str, float] = {}
    for it in items:
        aid = it.get("article_id")
        if not aid:
            continue
        qty = it.get("qty_actual") or it.get("qty_expected") or 0
        by_art[aid] = by_art.get(aid, 0.0) + float(qty)
    return by_art


def _propagate_to_order(order_id: str) -> str | None:
    """Auftrag auf 'partial' / 'shipped' setzen, wenn Liefermengen es rechtfertigen.
    Returns: neuer Status, falls geändert; sonst None.
    """
    delivered = _sum_delivered_by_article(
        related_field="related_order_id",
        related_id=order_id,
        fulfilled_statuses=DELIVERY_FULFILLED_OUTBOUND,
    )
    o_items = (
        supabase()
        .table("order_items")
        .select("article_id, qty")
        .eq("order_id", order_id)
        .execute()
        .data
    ) or []
    if not o_items:
        return None

    has_articles = any(it.get("article_id") for it in o_items)
    if not has_articles:
        return None

    fully = True
    any_done = False
    for it in o_items:
        aid = it.get("article_id")
        if not aid:
            continue
        ordered = float(it.get("qty") or 0)
        delv = delivered.get(aid, 0.0)
        if delv > 0:
            any_done = True
        if delv < ordered:
            fully = False

    cur = (
        supabase().table("orders").select("status").eq("id", order_id).single().execute().data
    )
    cur_status = cur["status"]

    new_status: str | None = None
    if fully and cur_status in ("confirmed", "in_production", "partial"):
        new_status = "shipped"
    elif any_done and cur_status in ("confirmed", "in_production"):
        new_status = "partial"

    if not new_status or new_status == cur_status:
        return None

    from features.orders import service as order_service
    order_service.update_status(
        order_id, new_status, comment="Auto: aus Lieferungs-Update propagiert"
    )
    return new_status


def _propagate_to_po(po_id: str) -> str | None:
    """Bestellung auf 'partial' / 'received' setzen, wenn Wareneingang es rechtfertigt."""
    delivered = _sum_delivered_by_article(
        related_field="related_po_id",
        related_id=po_id,
        fulfilled_statuses=DELIVERY_FULFILLED_INBOUND,
    )
    p_items = (
        supabase()
        .table("po_items")
        .select("article_id, qty")
        .eq("po_id", po_id)
        .execute()
        .data
    ) or []
    if not p_items:
        return None

    has_articles = any(it.get("article_id") for it in p_items)
    if not has_articles:
        return None

    fully = True
    any_done = False
    for it in p_items:
        aid = it.get("article_id")
        if not aid:
            continue
        ordered = float(it.get("qty") or 0)
        delv = delivered.get(aid, 0.0)
        if delv > 0:
            any_done = True
        if delv < ordered:
            fully = False

    cur = (
        supabase().table("purchase_orders").select("status").eq("id", po_id).single().execute().data
    )
    cur_status = cur["status"]

    new_status: str | None = None
    if fully and cur_status in ("sent", "confirmed", "in_production", "shipped", "partial"):
        new_status = "received"
    elif any_done and cur_status in ("sent", "confirmed", "in_production", "shipped"):
        new_status = "partial"

    if not new_status or new_status == cur_status:
        return None

    from features.purchase_orders import service as po_service
    po_service.update_status(
        po_id, new_status, comment="Auto: aus Wareneingang propagiert"
    )
    return new_status


def _book_stock_for_delivery(delivery_id: str, movement_type: str) -> int:
    """Erzeugt stock_movements für alle Items der Lieferung. Idempotent: skipt
    wenn für diese Lieferung+Typ schon Movements existieren.

    Args:
        movement_type: 'inbound' (qty positiv) oder 'outbound' (qty negativ).

    Returns: Anzahl der angelegten Movements.
    """
    existing = (
        supabase()
        .table("stock_movements")
        .select("id")
        .eq("delivery_id", delivery_id)
        .eq("movement_type", movement_type)
        .limit(1)
        .execute()
    )
    if existing.data:
        return 0  # schon verbucht

    items = (
        supabase()
        .table("delivery_items")
        .select("article_id, qty_actual, qty_expected, batch_lot, mhd")
        .eq("delivery_id", delivery_id)
        .execute()
        .data
    )

    sign = 1 if movement_type == "inbound" else -1
    movements: list[dict[str, Any]] = []
    for item in items:
        article_id = item.get("article_id")
        if not article_id:
            continue  # freie Position ohne Artikel: nicht verbuchbar
        qty = item.get("qty_actual") or item.get("qty_expected")
        if not qty:
            continue
        movements.append({
            "article_id": article_id,
            "qty_delta": sign * float(qty),
            "movement_type": movement_type,
            "delivery_id": delivery_id,
            "batch_lot": item.get("batch_lot"),
            "mhd": item.get("mhd"),
            "actor_label": _actor_label(),
            "note": "Auto-Verbuchung aus Lieferungs-Statuswechsel",
        })

    if not movements:
        return 0

    supabase().table("stock_movements").insert(movements).execute()
    _log_event(delivery_id, "stock_booked", {
        "movement_type": movement_type,
        "items": len(movements),
    })
    return len(movements)


def lock_delivery(delivery_id: str) -> None:
    """GoBD: nach Finalisierung append-only + Stammdaten-Snapshots atomar."""
    cur = (
        supabase()
        .table("deliveries")
        .select("party_id, source_party_id, shipping_address_id")
        .eq("id", delivery_id)
        .single()
        .execute()
    )
    snapshots = build_delivery_snapshot_payload(
        party_id=cur.data.get("party_id"),
        source_party_id=cur.data.get("source_party_id"),
        shipping_address_id=cur.data.get("shipping_address_id"),
    )
    supabase().table("deliveries").update({
        "locked_at": datetime.utcnow().isoformat() + "Z",
        **snapshots,
    }).eq("id", delivery_id).execute()
    _log_event(delivery_id, "locked", {})


# ---------- Items ----------

def add_item(delivery_id: str, item: dict[str, Any]) -> str:
    item["delivery_id"] = delivery_id
    payload = {k: _ser(v) for k, v in item.items() if v is not None and v != ""}
    res = supabase().table("delivery_items").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log_event(delivery_id, "item_added", {"pos_nr": payload.get("pos_nr")})
    return new_id


def replace_items(delivery_id: str, items: list[dict[str, Any]]) -> None:
    """Atomar — delete+insert in einer Transaktion via RPC. Lock-Check serverseitig."""
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: _ser(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)

    rows = enrich_items_with_snapshots(rows)

    supabase().rpc("replace_delivery_items", {
        "p_delivery_id": delivery_id,
        "p_items": rows,
    }).execute()

    _log_event(delivery_id, "items_replaced", {"count": len(rows)})


# ---------- Documents (Supabase Storage) ----------

def upload_document(
    delivery_id: str,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    kind: str,
    notes: str | None = None,
) -> str:
    """PDF/Foto in Bucket `delivery-docs` ablegen + Tabellen-Eintrag."""
    safe_name = filename.replace("/", "_").replace("\\", "_")
    storage_path = f"{delivery_id}/{uuid.uuid4().hex[:8]}_{safe_name}"

    bucket = supabase().storage.from_("delivery-docs")
    bucket.upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )

    res = supabase().table("delivery_documents").insert({
        "delivery_id": delivery_id,
        "kind": kind,
        "filename": safe_name,
        "storage_path": storage_path,
        "content_type": content_type,
        "size_bytes": len(file_bytes),
        "notes": notes,
    }).execute()

    new_id = res.data[0]["id"]
    _log_event(delivery_id, "document_uploaded", {
        "kind": kind, "filename": safe_name, "size": len(file_bytes),
    })
    return new_id


def download_document(storage_path: str) -> bytes:
    return supabase().storage.from_("delivery-docs").download(storage_path)


def delete_document(doc_id: str, delivery_id: str, storage_path: str) -> None:
    supabase().storage.from_("delivery-docs").remove([storage_path])
    supabase().table("delivery_documents").delete().eq("id", doc_id).execute()
    _log_event(delivery_id, "document_deleted", {"storage_path": storage_path})


# ---------- Helpers ----------

def _ser(v: Any) -> Any:
    """Serialisiere Datum/Datetime zu ISO-Strings, lasse Rest durch."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v
