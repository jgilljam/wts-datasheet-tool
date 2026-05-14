"""Vorgangs-Aggregation + Wartung (Sent-Mail-Rematch).

Ein "Vorgang" ist die Klammer um einen Kunden-Auftrag (orders) und alle
daran hängenden Belege:
- Bestellungen bei Lieferanten (purchase_orders.source_order_id)
- Lieferanten-Bestellbestätigungen (po.confirmed_at oder po.attachments)
- Lieferanten-Rechnungen (incoming_invoices.related_po_id → po)
- Ausgangsrechnungen an Kunden (invoices.related_order_id — Tool-generiert)
- Ausgangsmails aus Sent-Folder (outgoing_mails wo linked_beleg_id = order_id)

Pro Vorgang: Status-Ampeln + alle PDF-Pfade gesammelt.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from core.db import supabase


# Status-Werte aus orders die als "abgeschlossen" gelten
ORDER_DONE_STATUSES = {"done", "cancelled", "rejected"}


def list_vorgaenge(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    only_open: bool = True,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Hauptfunktion: aggregiert Vorgänge im Zeitraum.

    Filter `date_from` / `date_to` wird auf `orders.ordered_at` angewandt.
    `only_open=True` (Default) blendet Aufträge mit Status done/cancelled aus.

    Returns: Liste von Vorgangs-Dicts mit Status-Ampeln + Sub-Listen.
    """
    sb = supabase()

    # 1) Aufträge im Zeitraum
    q = (
        sb.table("orders")
        .select(
            "id, order_number, customer_reference, status, ordered_at, due_date, "
            "currency, total_net_cents, "
            "customer:parties!customer_id(id, legal_name, short_name)"
        )
    )
    if date_from:
        q = q.gte("ordered_at", date_from.isoformat())
    if date_to:
        q = q.lte("ordered_at", date_to.isoformat())
    # only_open wird Python-side gefiltert (siehe unten) — der Supabase-Python-Client
    # hat kein zuverlässiges NOT-IN, und Aufträge pro Zeitraum sind überschaubar.
    if search:
        s = search.strip().replace("%", "")
        q = q.or_(
            f"order_number.ilike.%{s}%,"
            f"customer_reference.ilike.%{s}%"
        )
    orders = (
        q.order("ordered_at", desc=True, nullsfirst=False)
         .limit(limit)
         .execute()
         .data
    ) or []
    if only_open:
        orders = [o for o in orders if o.get("status") not in ORDER_DONE_STATUSES]
    if not orders:
        return []

    order_ids = [o["id"] for o in orders]

    # 2) Lieferanten-Bestellungen (PO) für diese Aufträge
    pos = (
        sb.table("purchase_orders")
        .select(
            "id, po_number, status, ordered_at, confirmed_at, source_order_id, "
            "currency, total_net_cents, "
            "pdf_storage_path, "
            "supplier:parties!supplier_id(id, legal_name, short_name)"
        )
        .in_("source_order_id", order_ids)
        .execute()
        .data
    ) or []
    pos_by_order: dict[str, list[dict[str, Any]]] = {}
    po_ids = []
    for p in pos:
        pos_by_order.setdefault(p["source_order_id"], []).append(p)
        po_ids.append(p["id"])

    # 3) Eingangsrechnungen (von Lieferanten) — verknüpft via related_po_id
    inc_invs_by_po: dict[str, list[dict[str, Any]]] = {}
    if po_ids:
        inc_invs = (
            sb.table("incoming_invoices")
            .select(
                "id, invoice_number, status, invoice_date, due_date, "
                "currency, total_net_cents, "
                "pdf_storage_path, related_po_id, "
                "supplier:parties!supplier_id(id, legal_name, short_name)"
            )
            .in_("related_po_id", po_ids)
            .execute()
            .data
        ) or []
        for inv in inc_invs:
            inc_invs_by_po.setdefault(inv["related_po_id"], []).append(inv)

    # 4) Tool-generierte Ausgangsrechnungen (invoices.related_order_id)
    out_invs = (
        sb.table("invoices")
        .select(
            "id, invoice_number, status, issued_at, service_date, due_date, "
            "currency, total_net_cents, "
            "pdf_storage_path, related_order_id"
        )
        .in_("related_order_id", order_ids)
        .execute()
        .data
    ) or []
    out_invs_by_order: dict[str, list[dict[str, Any]]] = {}
    for inv in out_invs:
        out_invs_by_order.setdefault(inv["related_order_id"], []).append(inv)

    # 5) IMAP-gepullte Sent-Mails verknüpft mit Auftrag (linked_beleg_type='order')
    sent_mails_by_order: dict[str, list[dict[str, Any]]] = {}
    try:
        sent_mails = (
            sb.table("outgoing_mails")
            .select(
                "id, message_id, subject, date_sent_hdr, to_email, "
                "ai_category, attachments_meta, linked_beleg_id, linked_beleg_type"
            )
            .eq("source", "imap_pull")
            .eq("linked_beleg_type", "order")
            .in_("linked_beleg_id", order_ids)
            .execute()
            .data
        ) or []
        for m in sent_mails:
            sent_mails_by_order.setdefault(m["linked_beleg_id"], []).append(m)
    except Exception:
        # Falls outgoing_mails-Migration noch nicht durch ist, leise weiterlaufen
        pass

    # 6) Lieferungen (deliveries) als zusätzliche Statusanzeige
    deliveries = (
        sb.table("deliveries")
        .select(
            "id, delivery_number, status, issued_at, shipped_at, delivered_at, "
            "pdf_storage_path, related_order_id"
        )
        .in_("related_order_id", order_ids)
        .execute()
        .data
    ) or []
    deliveries_by_order: dict[str, list[dict[str, Any]]] = {}
    for d in deliveries:
        deliveries_by_order.setdefault(d["related_order_id"], []).append(d)

    # --- Aggregation pro Vorgang ---
    out: list[dict[str, Any]] = []
    for o in orders:
        oid = o["id"]
        order_pos = pos_by_order.get(oid, [])
        order_inc_invs = [
            inv for p in order_pos for inv in inc_invs_by_po.get(p["id"], [])
        ]
        order_out_invs_tool = out_invs_by_order.get(oid, [])
        order_sent_mails = sent_mails_by_order.get(oid, [])
        order_deliveries = deliveries_by_order.get(oid, [])

        # Status-Ampeln (bool)
        has_order_pdf = bool(o.get("status") and o["status"] not in {"draft"})
        has_pos = bool(order_pos)
        all_pos_confirmed = (
            bool(order_pos) and all(p.get("confirmed_at") for p in order_pos)
        )
        all_pos_invoiced = (
            bool(order_pos)
            and all(inc_invs_by_po.get(p["id"]) for p in order_pos)
        )
        has_outgoing_invoice = bool(order_out_invs_tool) or any(
            m.get("ai_category") == "outgoing_invoice" for m in order_sent_mails
        )
        is_done = o.get("status") in ORDER_DONE_STATUSES

        # Geldbeträge — nur Netto verfügbar in Schema
        order_net = (o.get("total_net_cents") or 0) / 100.0
        inc_inv_net = sum(
            (inv.get("total_net_cents") or 0) for inv in order_inc_invs
        ) / 100.0
        out_inv_net = sum(
            (inv.get("total_net_cents") or 0) for inv in order_out_invs_tool
        ) / 100.0

        # Margin (grob): Verkauf-Netto − Einkauf-Netto
        margin = (order_net - inc_inv_net) if order_inc_invs else None

        out.append({
            "order_id": oid,
            "order_number": o.get("order_number") or "—",
            "customer_reference": o.get("customer_reference"),
            "customer": (o.get("customer") or {}),
            "customer_name": (
                (o.get("customer") or {}).get("legal_name")
                or (o.get("customer") or {}).get("short_name")
                or "—"
            ),
            "ordered_at": o.get("ordered_at"),
            "due_date": o.get("due_date"),
            "status": o.get("status"),
            "currency": o.get("currency") or "EUR",
            "order_net_eur": order_net,
            "inc_inv_net_eur": inc_inv_net,
            "out_inv_net_eur": out_inv_net,
            "margin_eur": margin,
            # Sub-Listen
            "purchase_orders": order_pos,
            "incoming_invoices": order_inc_invs,
            "outgoing_invoices_tool": order_out_invs_tool,
            "outgoing_mails_sent": order_sent_mails,
            "deliveries": order_deliveries,
            # Status-Ampeln (für UI)
            "flags": {
                "has_order_pdf": has_order_pdf,
                "has_pos": has_pos,
                "all_pos_confirmed": all_pos_confirmed,
                "all_pos_invoiced": all_pos_invoiced,
                "has_outgoing_invoice": has_outgoing_invoice,
                "is_done": is_done,
            },
        })
    return out


def rematch_all_unlinked_sent_mails() -> dict[str, int]:
    """Wartung: alle outgoing_mails ohne linked_beleg_id durch das Stufe-1-
    Matching schicken. Nützlich nach Deploy einer neuen Matching-Logik
    oder wenn alte Pulls noch unverlinkt sind.
    """
    from lib import mail_to_beleg
    sb = supabase()
    rows = (
        sb.table("outgoing_mails")
        .select("id")
        .eq("source", "imap_pull")
        .is_("linked_beleg_id", "null")
        .limit(2000)
        .execute().data
    ) or []
    linked = 0
    failed = 0
    for r in rows:
        try:
            res = mail_to_beleg.link_outgoing_mail(outgoing_mail_id=r["id"])
            if res.get("linked"):
                linked += 1
        except Exception:
            failed += 1
    return {"processed": len(rows), "linked": linked, "failed": failed}


def collect_pdf_paths(vorgang: dict[str, Any]) -> list[dict[str, Any]]:
    """Sammelt alle PDF-Storage-Pfade eines Vorgangs für Download/ZIP.

    Returns: Liste von {label, bucket, path, kind}.
    """
    out: list[dict[str, Any]] = []

    # Hinweis: Auftragsbeleg-PDFs liegen in `order_attachments` (separate Tabelle),
    # nicht direkt in orders.pdf_storage_path. Wird in Task #5 (PDF-Bundle)
    # vollständig integriert.

    for po in vorgang.get("purchase_orders") or []:
        if po.get("pdf_storage_path"):
            out.append({
                "label": f"Bestellung {po.get('po_number') or '—'}",
                "bucket": "belege",
                "path": po["pdf_storage_path"],
                "kind": "purchase_order",
            })
    for inv in vorgang.get("incoming_invoices") or []:
        if inv.get("pdf_storage_path"):
            out.append({
                "label": f"Eingangsrechnung {inv.get('invoice_number') or '—'}",
                "bucket": "belege",
                "path": inv["pdf_storage_path"],
                "kind": "incoming_invoice",
            })
    for inv in vorgang.get("outgoing_invoices_tool") or []:
        if inv.get("pdf_storage_path"):
            out.append({
                "label": f"Ausgangsrechnung {inv.get('invoice_number') or '—'}",
                "bucket": "belege",
                "path": inv["pdf_storage_path"],
                "kind": "outgoing_invoice_tool",
            })
    for d in vorgang.get("deliveries") or []:
        if d.get("pdf_storage_path"):
            out.append({
                "label": f"Lieferschein {d.get('delivery_number') or '—'}",
                "bucket": "belege",
                "path": d["pdf_storage_path"],
                "kind": "delivery",
            })
    # IMAP-Sent-Mail-Attachments
    for m in vorgang.get("outgoing_mails_sent") or []:
        for att in m.get("attachments_meta") or []:
            if (att.get("path") or att.get("storage_path")):
                out.append({
                    "label": f"Mail-Anhang: {att.get('filename') or '—'}",
                    "bucket": att.get("bucket") or "mail-outgoing",
                    "path": att.get("storage_path") or att.get("path"),
                    "kind": "sent_mail_attachment",
                })

    return out
