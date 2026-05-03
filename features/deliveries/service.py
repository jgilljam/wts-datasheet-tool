"""Write-Layer für Lieferungen — Mutationen + Audit-Log + Storage-Uploads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import streamlit as st

from core.db import supabase

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


def update_status(delivery_id: str, new_status: str, comment: str | None = None) -> dict[str, int]:
    """Setzt Status, schreibt Audit-Eintrag, verbucht ggf. ins Lager.

    Returns: {"booked": N} mit Anzahl der verbuchten Positionen.
    """
    cur = (
        supabase()
        .table("deliveries")
        .select("status, direction")
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

    return {"booked": booked}


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
    """GoBD: nach Finalisierung append-only — keine Updates mehr."""
    supabase().table("deliveries").update({
        "locked_at": datetime.utcnow().isoformat() + "Z",
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
    """Komplett-Ersatz aller Positionen einer Lieferung (nutzt der Editor)."""
    supabase().table("delivery_items").delete().eq("delivery_id", delivery_id).execute()
    if not items:
        return
    rows = []
    for i, raw in enumerate(items, start=1):
        if not raw:
            continue
        clean = {k: _ser(v) for k, v in raw.items() if v is not None and v != ""}
        clean["delivery_id"] = delivery_id
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)
    if rows:
        supabase().table("delivery_items").insert(rows).execute()
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
