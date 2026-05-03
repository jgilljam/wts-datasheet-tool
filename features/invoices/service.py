"""Write-Layer für Rechnungen — inkl. Storno-Workflow + GoBD-Festschreibung."""

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
from .constants import INVOICE_LOCKED_STATUSES


def _log(invoice_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    log_event("invoice_events", "invoice_id", invoice_id, event_type, payload)


# ---------- Rechnung ----------

def create_invoice(data: dict[str, Any]) -> str:
    """Lege Rechnungsentwurf an. KEINE Rechnungsnummer im Draft (GoBD!)."""
    payload = {k: ser_value(v) for k, v in data.items() if v is not None and v != ""}
    payload.setdefault("status", "draft")
    # invoice_number bleibt NULL bis zum issue() — vermeidet Lücken bei verworfenen Drafts

    res = supabase().table("invoices").insert(payload).execute()
    new_id = res.data[0]["id"]
    _log(new_id, "created", {})
    return new_id


def update_invoice(invoice_id: str, changes: dict[str, Any]) -> None:
    """Patch-Update. Blockiert wenn Rechnung gesperrt ist."""
    if not changes:
        return
    cur = (
        supabase()
        .table("invoices")
        .select("status, locked_at")
        .eq("id", invoice_id)
        .single()
        .execute()
    )
    if cur.data["status"] in INVOICE_LOCKED_STATUSES or cur.data.get("locked_at"):
        raise PermissionError(
            f"Rechnung im Status '{cur.data['status']}' ist GoBD-gesperrt. "
            "Stornierung erfordert separaten Storno-Beleg."
        )
    payload = {k: ser_value(v) for k, v in changes.items()}
    supabase().table("invoices").update(payload).eq("id", invoice_id).execute()
    _log(invoice_id, "updated", {"fields": list(changes.keys())})


def issue_invoice(invoice_id: str) -> str:
    """Festschreiben: Rechnungsnummer vergeben, Status → issued, locked_at setzen.

    Returns: Die vergebene Rechnungsnummer.
    """
    inv = (
        supabase()
        .table("invoices")
        .select(
            "status, issued_at, service_date, "
            "customer_id, billing_address_id, shipping_address_id"
        )
        .eq("id", invoice_id)
        .single()
        .execute()
    )
    if inv.data["status"] != "draft":
        raise PermissionError(
            f"Festschreiben nur aus Status 'draft' möglich (aktuell: '{inv.data['status']}')."
        )
    if not inv.data.get("service_date"):
        raise ValueError(
            "Leistungsdatum (service_date) ist Pflicht nach UStG §14. Bitte vor Festschreiben setzen."
        )

    # Items prüfen
    items = repo.list_invoice_items(invoice_id)
    if not items:
        raise ValueError("Rechnung hat keine Positionen.")

    issued_at = inv.data.get("issued_at") or date.today()
    year = issued_at.year if isinstance(issued_at, date) else int(str(issued_at)[:4])

    # issued_at ggf. setzen (Status-Validation passiert in der RPC nochmal)
    if not inv.data.get("issued_at"):
        supabase().table("invoices").update({"issued_at": issued_at.isoformat()}).eq(
            "id", invoice_id
        ).execute()

    # GoBD-Snapshots vor der Nummernvergabe bauen
    snapshots = build_invoice_snapshot_payload(
        customer_id=inv.data.get("customer_id"),
        billing_address_id=inv.data.get("billing_address_id"),
        shipping_address_id=inv.data.get("shipping_address_id"),
    )

    # Atomar: Counter-Bump + Status-Update + Snapshot in einer DB-Transaction.
    # Bei Fehler im Update wird der Counter-Inkrement zurückgerollt → keine Lücke.
    new_number = supabase().rpc("issue_invoice_atomic", {
        "p_invoice_id": invoice_id,
        "p_year": year,
        "p_customer_snapshot": snapshots["customer_snapshot"],
        "p_billing_address_snapshot": snapshots["billing_address_snapshot"],
        "p_shipping_address_snapshot": snapshots["shipping_address_snapshot"],
        "p_company_snapshot": snapshots["company_snapshot"],
    }).execute().data
    _log(invoice_id, "issued", {"invoice_number": new_number})
    return new_number


def auto_mark_overdue(*, today: date | None = None) -> int:
    """Setzt Rechnungen mit `due_date < today` und Status (issued|partially_paid)
    auf 'overdue'. Idempotent — schreibt audit-event nur bei tatsächlichem Wechsel.

    Returns: Anzahl der hochgesetzten Rechnungen.
    """
    today = today or date.today()
    rows = (
        supabase()
        .table("invoices")
        .select("id, status, due_date")
        .in_("status", ["issued", "partially_paid"])
        .lt("due_date", today.isoformat())
        .limit(500)
        .execute()
        .data
    ) or []
    count = 0
    for r in rows:
        supabase().table("invoices").update({"status": "overdue"}).eq("id", r["id"]).execute()
        _log(r["id"], "status_change", {
            "old_status": r["status"],
            "new_status": "overdue",
            "comment": "Auto-overdue: Fälligkeit überschritten",
        })
        count += 1
    return count


def update_status(invoice_id: str, new_status: str, comment: str | None = None) -> None:
    from .constants import INVOICE_ALLOWED_TRANSITIONS, INVOICE_STATUSES

    if new_status not in INVOICE_STATUSES:
        raise ValueError(f"Unbekannter Rechnungs-Status: {new_status}")

    cur = (
        supabase()
        .table("invoices")
        .select("status")
        .eq("id", invoice_id)
        .single()
        .execute()
    )
    old_status = cur.data["status"]
    if old_status == new_status:
        return

    allowed = INVOICE_ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise PermissionError(
            f"Status-Übergang '{old_status}' → '{new_status}' nicht erlaubt. "
            f"Mögliche: {sorted(allowed) or 'keine (terminal)'}. "
            "Stornorechnung erfordert reverse_invoice()."
        )

    supabase().table("invoices").update({"status": new_status}).eq("id", invoice_id).execute()
    _log(invoice_id, "status_change", {
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
    })


def record_payment(invoice_id: str, amount_cents: int, paid_at: date | None = None) -> None:
    """Erfasse eine Zahlung. Wechselt Status auto auf partially_paid/paid."""
    inv = (
        supabase()
        .table("invoices")
        .select("status, total_net_cents, tax_total_cents, paid_amount_cents")
        .eq("id", invoice_id)
        .single()
        .execute()
    )
    if inv.data["status"] not in ("issued", "partially_paid", "overdue"):
        raise PermissionError(
            f"Zahlungserfassung nicht im Status '{inv.data['status']}' möglich."
        )
    total_brutto = int(inv.data.get("total_net_cents") or 0) + int(inv.data.get("tax_total_cents") or 0)
    new_paid = int(inv.data.get("paid_amount_cents") or 0) + int(amount_cents)
    new_status = "paid" if new_paid >= total_brutto else "partially_paid"

    supabase().table("invoices").update({
        "paid_amount_cents": new_paid,
        "status": new_status,
        "paid_at": (paid_at or date.today()).isoformat() if new_status == "paid" else None,
    }).eq("id", invoice_id).execute()
    _log(invoice_id, "payment_recorded", {
        "amount_cents": int(amount_cents),
        "new_total_paid": new_paid,
        "new_status": new_status,
    })


# ---------- Items ----------

def replace_items(invoice_id: str, items: list[dict[str, Any]]) -> None:
    """Atomar — delete+insert in einer Transaktion via RPC.

    GoBD-Lock-Check passiert serverseitig (RPC liest invoices.locked_at und
    bricht ab). Bei Insert-Fehler wird der Delete automatisch zurückgerollt.
    Items werden mit article_title/sku-Snapshots angereichert (GoBD P3).
    """
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], start=1):
        if not raw:
            continue
        clean = {k: ser_value(v) for k, v in raw.items() if v is not None and v != ""}
        clean["pos_nr"] = clean.get("pos_nr") or i
        rows.append(clean)

    rows = enrich_items_with_snapshots(rows)

    supabase().rpc("replace_invoice_items", {
        "p_invoice_id": invoice_id,
        "p_items": rows,
    }).execute()

    _log(invoice_id, "items_replaced", {"count": len(rows)})
    _recompute_totals(invoice_id)


def _recompute_totals(invoice_id: str) -> None:
    items = (
        supabase()
        .table("invoice_items")
        .select("qty, unit_price_cents, tax_rate, discount_pct")
        .eq("invoice_id", invoice_id)
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

    supabase().table("invoices").update({
        "total_net_cents": net,
        "tax_total_cents": tax,
        "discount_total_cents": discount,
    }).eq("id", invoice_id).execute()


# ---------- One-Click „Rechnung aus Auftrag" ----------

def create_invoice_from_order(order_id: str, *, mode: str = "complete") -> str:
    """Erzeugt einen Rechnungsentwurf aus einem Auftrag.

    Args:
        order_id: Quell-Auftrag (Status muss shipped/partial/done sein für sinnvolle Rechnung).
        mode: 'complete' (alle offenen Mengen) oder 'remaining' (nur was noch nicht fakturiert).

    Returns: invoice_id (Status 'draft').

    Logik (Teilrechnungs-Tracking):
    - liest aus order_items.qty + qty_invoiced
    - rechnet `qty_remaining = qty - qty_invoiced` für jeden Eintrag
    - kopiert nur Positionen mit qty_remaining > 0
    - aktualisiert nach Speichern: order_items.qty_invoiced += qty_in_invoice
    """
    from features.orders import repo as order_repo

    order = order_repo.get_order(order_id)
    if not order:
        raise ValueError(f"Auftrag {order_id} nicht gefunden")
    if order.get("status") in ("draft",):
        raise PermissionError("Auftrag muss mindestens 'confirmed' sein für Rechnung.")

    order_items = order_repo.list_order_items(order_id)
    customer = order.get("customer") or {}
    rev_charge = bool(customer.get("is_reverse_charge_eligible"))

    # Rechnungs-Header
    invoice_payload: dict[str, Any] = {
        "customer_id": order["customer_id"],
        "shipping_address_id": order.get("shipping_address_id"),
        "billing_address_id": order.get("billing_address_id"),
        "related_order_id": order_id,
        "customer_reference": order.get("customer_reference"),
        "currency": order.get("currency") or "EUR",
        "payment_terms_days": order.get("payment_terms_days"),
        "incoterms": order.get("incoterms"),
        "incoterms_place": order.get("incoterms_place"),
        "is_reverse_charge": rev_charge,
        "issued_at": date.today(),
        "service_date": date.today(),  # Default = heute, sollte vor issue() angepasst werden
        "due_date": (
            date.today() + timedelta(days=int(order.get("payment_terms_days") or 14))
        ),
        "purpose_of_payment": (
            f"Rechnung zu Auftrag {order.get('order_number') or '?'}"
        ),
        "status": "draft",
    }

    invoice_id = create_invoice(invoice_payload)

    # Items kopieren
    invoice_items: list[dict[str, Any]] = []
    items_to_update: list[tuple[str, float]] = []  # (order_item_id, qty_to_invoice)
    pos_counter = 1
    for it in order_items:
        ordered = float(it.get("qty") or 0)
        invoiced = float(it.get("qty_invoiced") or 0)
        remaining = max(0.0, ordered - invoiced)
        if mode == "complete":
            qty_to_invoice = remaining
        else:
            qty_to_invoice = remaining

        if qty_to_invoice <= 0:
            continue  # bereits komplett fakturiert

        unit_price = int(it.get("unit_price_cents") or 0)
        disc_pct = float(it.get("discount_pct") or 0)
        tax_rate = 0.0 if rev_charge else float(it.get("tax_rate") or 19)

        line_gross = qty_to_invoice * unit_price
        line_net = int(round(line_gross * (1 - disc_pct / 100.0)))
        line_tax = int(round(line_net * tax_rate / 100.0))

        invoice_items.append({
            "pos_nr": pos_counter,
            "article_id": it.get("article_id"),
            "description_override": it.get("description_override"),
            "qty": qty_to_invoice,
            "unit": it.get("unit") or "Stk",
            "unit_price_cents": unit_price,
            "tax_rate": tax_rate,
            "discount_pct": disc_pct,
            "tax_amount_cents": line_tax,
            "line_total_cents": line_net,
            "source_order_item_id": it["id"],
        })
        items_to_update.append((it["id"], qty_to_invoice))
        pos_counter += 1

    if not invoice_items:
        # Alles bereits fakturiert
        supabase().table("invoices").delete().eq("id", invoice_id).execute()
        raise ValueError(
            "Alle Auftragspositionen sind bereits vollständig fakturiert. "
            "Keine offene Menge für eine neue Rechnung."
        )

    replace_items(invoice_id, invoice_items)

    # qty_invoiced auf order_items atomar hochzählen via RPC (race-frei)
    for order_item_id, qty in items_to_update:
        supabase().rpc("bump_qty_invoiced", {
            "p_order_item_id": order_item_id,
            "p_delta": qty,
        }).execute()

    _log(invoice_id, "created_from_order", {
        "order_id": order_id,
        "order_number": order.get("order_number"),
        "items": len(invoice_items),
        "mode": mode,
    })
    return invoice_id


# ---------- Storno-Workflow ----------

def reverse_invoice(invoice_id: str, reason: str, reversal_date: date | None = None) -> str:
    """Erzeugt einen Storno-Beleg zu einer ausgestellten Rechnung.

    Pattern:
    1. Original muss issued/partially_paid/paid/overdue sein
    2. Erzeugt neue Rechnung mit:
       - reverses_id = original.id
       - alle Items mit NEGATIVER qty
       - eigener Belegnummer (RE-2026-00XX)
       - Status sofort 'paid' (heben sich rechnerisch auf — kein offener Posten)
    3. Original.status → 'reversed', Original.reversed_by_id → new.id
    4. Audit-Log auf BEIDEN Belegen

    Args:
        invoice_id: Original-Rechnungs-ID
        reason: Pflicht-Begründung (für GoBD-Audit)
        reversal_date: Datum des Storno-Belegs (default: heute)

    Returns: storno_invoice_id
    """
    if not reason or not reason.strip():
        raise ValueError("Stornogrund ist Pflicht (GoBD).")

    original = repo.get_invoice(invoice_id)
    if not original:
        raise ValueError(f"Rechnung {invoice_id} nicht gefunden")
    if original["status"] not in ("issued", "partially_paid", "paid", "overdue"):
        raise PermissionError(
            f"Storno nur für ausgestellte Rechnungen — aktuell: '{original['status']}'."
        )
    if original.get("reversed_by_id"):
        raise PermissionError("Diese Rechnung wurde bereits storniert.")

    rev_date = reversal_date or date.today()
    items = repo.list_invoice_items(invoice_id)

    # UStG §14c: Storno einer Leistung muss das Leistungsdatum der ORIGINALLEISTUNG
    # tragen, nicht das Datum der Stornobuchung. issue_invoice() erzwingt service_date
    # bei Festschreibung, daher MUSS das Original eines hat — wenn nicht, ist die
    # Datenlage korrupt und wir brechen lieber ab als ein falsches Datum zu schreiben.
    original_service_date = original.get("service_date")
    if not original_service_date:
        raise ValueError(
            f"Original-Rechnung {original.get('invoice_number')} hat kein Leistungsdatum. "
            "Storno nicht möglich (UStG §14c)."
        )

    # Storno-Beleg-Header
    storno_payload: dict[str, Any] = {
        "customer_id": original["customer_id"],
        "shipping_address_id": original.get("shipping_address_id"),
        "billing_address_id": original.get("billing_address_id"),
        "related_order_id": original.get("related_order_id"),
        "customer_reference": original.get("customer_reference"),
        "currency": original.get("currency") or "EUR",
        "payment_terms_days": 0,
        "is_reverse_charge": original.get("is_reverse_charge", False),
        "issued_at": rev_date,
        "service_date": original_service_date,
        "due_date": rev_date,
        "reverses_id": invoice_id,
        "cancellation_reason": reason.strip(),
        "purpose_of_payment": (
            f"Stornorechnung zu {original.get('invoice_number') or '?'}"
        ),
        "notes": (
            f"Stornorechnung zur Rechnung {original.get('invoice_number')} "
            f"vom {original.get('issued_at')}.\n\nGrund: {reason.strip()}"
        ),
        "status": "draft",
    }
    storno_id = create_invoice(storno_payload)

    # Negative Items kopieren
    storno_items: list[dict[str, Any]] = []
    for it in items:
        qty = float(it.get("qty") or 0)
        if qty == 0:
            continue
        unit_price = int(it.get("unit_price_cents") or 0)
        tax_rate = float(it.get("tax_rate") or 0)
        disc_pct = float(it.get("discount_pct") or 0)

        neg_qty = -abs(qty)
        line_gross = neg_qty * unit_price
        line_net = int(round(line_gross * (1 - disc_pct / 100.0)))
        line_tax = int(round(line_net * tax_rate / 100.0))

        storno_items.append({
            "pos_nr": it.get("pos_nr"),
            "article_id": it.get("article_id"),
            "description_override": it.get("description_override"),
            "qty": neg_qty,
            "unit": it.get("unit") or "Stk",
            "unit_price_cents": unit_price,
            "tax_rate": tax_rate,
            "discount_pct": disc_pct,
            "tax_amount_cents": line_tax,
            "line_total_cents": line_net,
            "source_order_item_id": it.get("source_order_item_id"),
        })
    replace_items(storno_id, storno_items)

    # Storno-Beleg sofort festschreiben (eigene Belegnummer + locked)
    issue_invoice(storno_id)

    # Storno-Status: bezahlt (heben sich auf)
    storno_total_brutto = abs(int(original.get("total_net_cents") or 0)) + abs(int(original.get("tax_total_cents") or 0))
    supabase().table("invoices").update({
        "status": "paid",
        "paid_at": rev_date.isoformat(),
        "paid_amount_cents": -storno_total_brutto,
    }).eq("id", storno_id).execute()

    # Original auf reversed setzen
    supabase().table("invoices").update({
        "status": "reversed",
        "reversed_by_id": storno_id,
        "cancellation_reason": reason.strip(),
    }).eq("id", invoice_id).execute()

    # qty_invoiced auf order_items rückbuchen — Original-Items haben positive qty,
    # daher delta = -qty (RPC clampt auf 0; Race-frei dank atomarem Increment)
    for it in items:
        if it.get("source_order_item_id"):
            supabase().rpc("bump_qty_invoiced", {
                "p_order_item_id": it["source_order_item_id"],
                "p_delta": -float(it.get("qty") or 0),
            }).execute()

    _log(invoice_id, "reversed", {
        "storno_id": storno_id,
        "reason": reason.strip(),
        "reversal_date": rev_date.isoformat(),
    })
    _log(storno_id, "is_storno_for", {
        "original_id": invoice_id,
        "original_number": original.get("invoice_number"),
    })
    return storno_id
