"""Einkaufs-Bestellungen — Liste / Anlegen / Detail."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.ui.address_picker import render_address_picker
from core.ui.kpi import render_kpis
from core.ui.status import render_status_pill, render_status_stepper
from core.utils import cents_to_eur, eur_to_cents, format_date, parse_date

from features.deliveries import repo as delivery_repo
from features.deliveries.constants import STATUS_LABELS_DE as DELIVERY_STATUS_LABELS
from features.orders import repo as order_repo

from . import repo, service
from .constants import (
    INCOTERMS_2020,
    PO_DONE_STATUSES,
    PO_FLOW,
    PO_LOCKED_STATUSES,
    PO_NEXT_ACTION,
    PO_STATUS_COLORS,
    PO_STATUS_LABELS,
    PO_STATUSES,
    PO_TERMINAL,
    TAX_RATE_DEFAULT,
    TAX_RATE_REVERSE_CHARGE,
)


FREE_ITEM_LABEL = "— freie Position —"
NEW_PARTY_SENTINEL = "__none__"


# =====================================================================
#  Tab 1 — Liste
# =====================================================================

def _kpis(rows: list[dict[str, Any]]) -> None:
    open_rows = [r for r in rows if r.get("status") not in PO_DONE_STATUSES]
    today = date.today()

    overdue = sum(
        1 for r in open_rows
        if (d := parse_date(r.get("expected_at"))) and d < today
    )
    awaiting = sum(1 for r in open_rows if r.get("status") in ("sent", "confirmed", "in_production", "shipped"))

    month_start = today.replace(day=1)
    month_volume_cents = sum(
        int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        for r in rows
        if (d := parse_date(r.get("ordered_at"))) and d >= month_start
    )

    render_kpis([
        ("Offene Bestellungen", len(open_rows)),
        ("Erwartet", awaiting),
        ("Überfällig", overdue),
        ("Volumen Monat", cents_to_eur(month_volume_cents) or "0,00 €"),
    ])


def _table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info("Keine Bestellungen mit diesen Filtern.")
        return
    today = date.today()
    data: list[dict[str, Any]] = []
    for r in rows:
        s = r.get("supplier") or {}
        src = r.get("source_order") or {}
        exp = parse_date(r.get("expected_at"))
        urgency = ""
        if exp and r.get("status") not in PO_DONE_STATUSES:
            delta = (exp - today).days
            if delta < 0:
                urgency = f"⚠️ {-delta} d überfällig"
            elif delta == 0:
                urgency = "🔥 heute"
            elif delta <= 7:
                urgency = f"in {delta} Tagen"
        data.append({
            "Nr.": r.get("po_number") or "",
            "Lieferant": s.get("short_name") or s.get("legal_name") or "—",
            "Bestelldatum": format_date(r.get("ordered_at")),
            "Erwartet": format_date(r.get("expected_at")),
            "Dringlichkeit": urgency,
            "AB-Nr Lieferant": r.get("supplier_reference") or "",
            "Quell-Auftrag": src.get("order_number") or "",
            "Netto": cents_to_eur(r.get("total_net_cents")),
            "Status": PO_STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
        })
    df = pd.DataFrame(data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nr.": st.column_config.TextColumn(width="small"),
            "Bestelldatum": st.column_config.TextColumn(width="small"),
            "Erwartet": st.column_config.TextColumn(width="small"),
            "Netto": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="medium"),
        },
    )


def _render_list_tab() -> None:
    c1, c2 = st.columns([3, 2])
    default_open = [s for s in PO_STATUSES if s not in PO_DONE_STATUSES]
    statuses = c1.multiselect(
        "Status",
        PO_STATUSES,
        default=default_open,
        format_func=lambda v: PO_STATUS_LABELS.get(v, v),
        key="po_list_statuses",
    )
    search = c2.text_input("Suche (Nr., AB-Nr, Notiz)", "", key="po_list_search")

    try:
        rows = repo.list_pos(
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:
        st.error(f"Konnte Bestellungen nicht laden: {exc}")
        return

    _kpis(rows)
    _table(rows)
    st.caption(f"{len(rows)} Bestellungen geladen.")


# =====================================================================
#  Tab 2 — Neu anlegen
# =====================================================================

def _create_party_quick(name: str) -> str:
    res = (
        supabase()
        .table("parties")
        .insert({"legal_name": name.strip(), "type": "supplier"})
        .execute()
    )
    delivery_repo.list_parties.clear()
    return res.data[0]["id"]


def _render_create_tab() -> None:
    st.subheader("Neue Bestellung")
    st.caption(
        "Pflicht: Lieferant + Datum. Items, Drop-Ship, Konditionen werden im Detail erfasst."
    )

    parties = delivery_repo.list_parties(party_type="supplier")
    party_choices = {NEW_PARTY_SENTINEL: "— wählen —"}
    for p in parties:
        party_choices[p["id"]] = p.get("short_name") or p["legal_name"]

    party_id = st.selectbox(
        "Lieferant",
        list(party_choices.keys()),
        format_func=lambda v: party_choices[v],
        key="new_po_party",
    )

    with st.expander("➕ Neuen Lieferanten schnell anlegen"):
        new_name = st.text_input("Firmenname", key="new_po_party_name")
        if st.button("Anlegen", key="new_po_party_submit"):
            if not new_name.strip():
                st.warning("Firmenname darf nicht leer sein.")
            else:
                _create_party_quick(new_name)
                st.success(f"'{new_name}' angelegt — bitte oben auswählen.")
                st.rerun()

    # Optional: aus Auftrag erzeugen (Drop-Ship)
    st.markdown("##### Optional: aus Auftrag erzeugen (Streckengeschäft)")
    orders = order_repo.list_orders(limit=500)
    order_choices = {NEW_PARTY_SENTINEL: "— ohne Auftrag —"}
    for o in orders:
        if o.get("status") in ("done", "cancelled"):
            continue
        c = o.get("customer") or {}
        cname = c.get("short_name") or c.get("legal_name") or "—"
        order_choices[o["id"]] = f"{o.get('order_number')} · {cname}"
    source_order_id = st.selectbox(
        "Quell-Auftrag (für Drop-Ship-Verlinkung)",
        list(order_choices.keys()),
        format_func=lambda v: order_choices[v],
        key="new_po_source_order",
    )

    # Adress-Picker außerhalb der Form
    real_supplier_id = party_id if party_id != NEW_PARTY_SENTINEL else None
    shipping_addr_id = render_address_picker(
        real_supplier_id, "new_po_ship", "Liefer-/Versandadresse Lieferant",
        kinds=["shipping", "registered"],
    )
    billing_addr_id = render_address_picker(
        real_supplier_id, "new_po_bill", "Rechnungsadresse Lieferant",
        kinds=["billing", "registered"],
    )

    with st.form("create_po", clear_on_submit=True):
        c1, c2 = st.columns(2)
        ordered_at = c1.date_input("Bestelldatum", value=date.today(), key="new_po_ordered_at")
        expected_at = c2.date_input(
            "Erwarteter Liefertermin",
            value=date.today() + timedelta(days=21),
            key="new_po_expected",
        )

        c3, c4 = st.columns(2)
        supplier_reference = c3.text_input(
            "AB-Nr. Lieferant (kann später erfasst werden)", key="new_po_ref"
        )
        payment_terms = c4.number_input(
            "Zahlungsziel (Tage)", min_value=0, max_value=180, value=30, step=1,
            key="new_po_payment_terms",
        )

        c5, c6 = st.columns(2)
        incoterms = c5.selectbox(
            "Incoterms",
            ["—"] + INCOTERMS_2020,
            format_func=lambda v: "— wählen —" if v == "—" else v,
            key="new_po_incoterms",
        )
        incoterms_place = c6.text_input("Incoterms-Ort", key="new_po_incoterms_place")

        notes = st.text_area("Notizen (sichtbar auf Beleg)", key="new_po_notes", height=80)
        internal_notes = st.text_area("Interne Notizen", key="new_po_internal_notes", height=80)

        submitted = st.form_submit_button(
            "🛒 Bestellung anlegen", type="primary", use_container_width=True
        )

        if submitted:
            if party_id == NEW_PARTY_SENTINEL:
                st.error("Bitte einen Lieferanten wählen.")
                return

            payload: dict[str, Any] = {
                "supplier_id": party_id,
                "status": "draft",
                "ordered_at": ordered_at,
                "expected_at": expected_at,
                "payment_terms_days": int(payment_terms) if payment_terms else None,
            }
            if source_order_id != NEW_PARTY_SENTINEL:
                payload["source_order_id"] = source_order_id
            if shipping_addr_id:
                payload["shipping_address_id"] = shipping_addr_id
            if billing_addr_id:
                payload["billing_address_id"] = billing_addr_id
            if supplier_reference.strip():
                payload["supplier_reference"] = supplier_reference.strip()
            if incoterms != "—":
                payload["incoterms"] = incoterms
            if incoterms_place.strip():
                payload["incoterms_place"] = incoterms_place.strip()
            if notes.strip():
                payload["notes"] = notes.strip()
            if internal_notes.strip():
                payload["internal_notes"] = internal_notes.strip()

            try:
                new_id = service.create_po(payload)
            except Exception as exc:
                st.error(f"Konnte Bestellung nicht anlegen: {exc}")
                return

            st.success(
                f"Bestellung angelegt (`{new_id[:8]}…`). "
                "Wechsle zum Tab **Detail**, um Positionen zu erfassen."
            )


# =====================================================================
#  Tab 3 — Detail
# =====================================================================

def _render_header_card(p: dict[str, Any]) -> None:
    supplier = p.get("supplier") or {}
    src = p.get("source_order") or {}
    rev_charge = bool(supplier.get("is_reverse_charge_eligible"))

    if src.get("order_number"):
        st.warning(
            f"🚚 **Aus Auftrag {src['order_number']}** — "
            "diese Bestellung kann zur Direktlieferung an den Endkunden konfiguriert werden "
            "(Items mit Drop-Ship-Flag)."
        )

    if rev_charge:
        st.info(
            f"💶 **Reverse-Charge** — Lieferant **{supplier.get('legal_name')}** ist "
            "EU-B2B mit USt-ID. USt-Sätze werden auf 0% vorgeschlagen."
        )

    pill = render_status_pill(
        p.get("status") or "draft",
        PO_STATUS_LABELS,
        PO_STATUS_COLORS,
    )
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
        f"<h3 style='margin:0;'>🛒 {p.get('po_number') or '—'}</h3>"
        f"<div>{pill}</div></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Lieferant**\n\n{supplier.get('legal_name') or '—'}")
    c2.markdown(f"**Bestelldatum**\n\n{format_date(p.get('ordered_at')) or '—'}")
    c3.markdown(
        f"**Erwartet**\n\n{format_date(p.get('confirmed_due_date') or p.get('expected_at')) or '—'}"
    )

    c4, c5, c6 = st.columns(3)
    c4.markdown(f"**AB-Nr Lieferant**\n\n{p.get('supplier_reference') or '—'}")
    c5.markdown(f"**Zahlungsziel**\n\n{p.get('payment_terms_days') or '—'} Tage")
    c6.markdown(
        f"**Incoterms**\n\n{p.get('incoterms') or '—'} {p.get('incoterms_place') or ''}"
    )

    if p.get("notes"):
        st.info(f"📝 {p['notes']}")


def _render_action_buttons(p: dict[str, Any]) -> None:
    po_id = p["id"]
    cur = p.get("status") or "draft"

    if cur in PO_TERMINAL or cur == "received":
        st.caption(f"Bestellung im Endstatus: **{PO_STATUS_LABELS.get(cur, cur)}**.")
        return

    next_action = PO_NEXT_ACTION.get(cur)
    if not next_action:
        return

    next_status, label = next_action

    c1, c2, c3 = st.columns([3, 2, 2])
    primary = c1.button(
        label,
        key=f"po_action_primary_{po_id}",
        type="primary",
        use_container_width=True,
    )

    delivery_btn = False
    if cur in ("sent", "confirmed", "in_production", "shipped", "partial"):
        delivery_btn = c2.button(
            "📦 Wareneingang erstellen",
            key=f"po_action_delivery_{po_id}",
            use_container_width=True,
            help="Erzeugt eine inbound-Lieferung mit den Bestellpositionen "
                 "(bei Drop-Ship: outbound zum Endkunden)",
        )

    cancel_btn = False
    if cur not in ("received", "cancelled"):
        cancel_btn = c3.button(
            "✕ Stornieren",
            key=f"po_action_cancel_{po_id}",
            use_container_width=True,
        )

    if primary:
        if cur == "draft":
            items = repo.list_po_items(po_id)
            if not items:
                st.error("Mindestens 1 Position nötig, bevor die Bestellung versandt werden kann.")
                return
        try:
            service.update_status(po_id, next_status)
        except Exception as exc:
            st.error(f"Status-Update fehlgeschlagen: {exc}")
            return
        st.success(f"Status: {PO_STATUS_LABELS.get(next_status, next_status)}")
        st.rerun()

    if delivery_btn:
        try:
            delivery_id = service.create_inbound_delivery_from_po(po_id)
        except Exception as exc:
            st.error(f"Wareneingang konnte nicht erzeugt werden: {exc}")
            return
        st.success(
            f"Wareneingang angelegt (`{delivery_id[:8]}…`). "
            "Wechsle zur Lieferungen-Page."
        )
        st.rerun()

    if cancel_btn:
        try:
            service.update_status(po_id, "cancelled")
        except Exception as exc:
            st.error(f"Storno fehlgeschlagen: {exc}")
            return
        st.warning("Bestellung storniert.")
        st.rerun()


def _render_smart_buttons(p: dict[str, Any]) -> None:
    deliveries = repo.list_deliveries_for_po(p["id"])
    if not deliveries:
        st.caption("📦 Keine Lieferungen verknüpft.")
        return

    st.markdown("**Verknüpfte Lieferungen / Wareneingänge**")
    rows: list[dict[str, Any]] = []
    for d in deliveries:
        rows.append({
            "Nr.": d.get("delivery_number") or "—",
            "Richtung": "📥 Eingang" if d.get("direction") == "inbound" else "📤 Ausgang",
            "Status": DELIVERY_STATUS_LABELS.get(d.get("status"), d.get("status") or ""),
            "Termin": format_date(d.get("expected_at")),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_items_editor(p: dict[str, Any]) -> None:
    po_id = p["id"]
    is_locked = (p.get("status") or "draft") in PO_LOCKED_STATUSES
    has_source_order = bool(p.get("source_order"))

    items = repo.list_po_items(po_id)
    articles = delivery_repo.list_articles()
    article_by_label = {
        f"{a['sku']} — {a.get('title_de') or ''}".strip(" —"): a for a in articles
    }
    article_options = [FREE_ITEM_LABEL] + sorted(article_by_label.keys())

    supplier = p.get("supplier") or {}
    default_tax = TAX_RATE_REVERSE_CHARGE if supplier.get("is_reverse_charge_eligible") else TAX_RATE_DEFAULT

    rows: list[dict[str, Any]] = []
    for it in items:
        a = it.get("articles") or {}
        if a.get("sku"):
            label = f"{a['sku']} — {a.get('title_de') or ''}".strip(" —")
            if label not in article_by_label:
                article_options.append(label)
        else:
            label = FREE_ITEM_LABEL
        unit_price_eur = float((it.get("unit_price_cents") or 0)) / 100.0
        rows.append({
            "Pos": it.get("pos_nr") or 0,
            "Artikel": label,
            "Beschreibung": it.get("description_override") or (a.get("title_de") or ""),
            "Menge": float(it.get("qty") or 0),
            "Einheit": it.get("unit") or "Stk",
            "EK €": unit_price_eur,
            "Rabatt %": float(it.get("discount_pct") or 0),
            "USt %": float(it.get("tax_rate") if it.get("tax_rate") is not None else default_tax),
            "Drop-Ship": bool(it.get("is_dropship")),
        })

    columns = [
        "Pos", "Artikel", "Beschreibung", "Menge", "Einheit",
        "EK €", "Rabatt %", "USt %", "Drop-Ship",
    ]
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)

    if is_locked:
        st.caption(
            f"🔒 **GoBD-gesperrt** (Status: {PO_STATUS_LABELS.get(p.get('status'), p.get('status'))}). "
            "Positionen sind nur lesbar — Änderungen erfordern Storno + Neuanlage."
        )

    column_config = {
        "Pos": st.column_config.NumberColumn(width="small", format="%d"),
        "Artikel": st.column_config.SelectboxColumn(
            options=article_options, required=False, width="medium"
        ),
        "Beschreibung": st.column_config.TextColumn(width="medium"),
        "Menge": st.column_config.NumberColumn(format="%.2f", width="small"),
        "Einheit": st.column_config.TextColumn(width="small"),
        "EK €": st.column_config.NumberColumn(format="%.2f", width="small"),
        "Rabatt %": st.column_config.NumberColumn(format="%.1f", width="small"),
        "USt %": st.column_config.NumberColumn(format="%.0f", width="small"),
        "Drop-Ship": st.column_config.CheckboxColumn(
            help="Direktlieferung Lieferant → Endkunde (nur sinnvoll bei Quell-Auftrag)",
            disabled=not has_source_order,
        ),
    }

    edited = st.data_editor(
        df,
        num_rows="fixed" if is_locked else "dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=is_locked,
        column_config=column_config,
        key=f"po_items_{po_id}",
    )

    # Live-Summen
    net = 0.0
    tax = 0.0
    for _, row in edited.iterrows():
        qty = float(row.get("Menge") or 0)
        price = float(row.get("EK €") or 0)
        disc = float(row.get("Rabatt %") or 0)
        tax_rate = float(row.get("USt %") or 0)
        gross = qty * price * 100
        net_line = gross * (1 - disc / 100.0)
        net += net_line
        tax += net_line * tax_rate / 100.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Netto", cents_to_eur(int(round(net))) or "0,00 €")
    c2.metric("USt", cents_to_eur(int(round(tax))) or "0,00 €")
    c3.metric("Brutto", cents_to_eur(int(round(net + tax))) or "0,00 €")

    if is_locked:
        return

    if st.button("💾 Positionen speichern", key=f"save_po_items_{po_id}", type="primary"):
        new_items: list[dict[str, Any]] = []
        for i, row in edited.iterrows():
            label = row.get("Artikel") or FREE_ITEM_LABEL
            article_id = None
            if label != FREE_ITEM_LABEL:
                a = article_by_label.get(label)
                article_id = a["id"] if a else None

            description = (row.get("Beschreibung") or "").strip()
            qty = float(row.get("Menge") or 0)
            unit = (row.get("Einheit") or "Stk").strip() or "Stk"

            if not article_id and not description and qty == 0:
                continue

            unit_price_cents = eur_to_cents(row.get("EK €")) or 0
            disc_pct = float(row.get("Rabatt %") or 0)
            tax_rate = float(row.get("USt %") or 0)

            line_gross = qty * unit_price_cents
            line_net = int(round(line_gross * (1 - disc_pct / 100.0)))
            line_tax = int(round(line_net * tax_rate / 100.0))

            item: dict[str, Any] = {
                "pos_nr": int(row.get("Pos") or i + 1),
                "qty": qty,
                "unit": unit,
                "unit_price_cents": unit_price_cents,
                "tax_rate": tax_rate,
                "discount_pct": disc_pct,
                "tax_amount_cents": line_tax,
                "line_total_cents": line_net,
                "is_dropship": bool(row.get("Drop-Ship")),
            }
            if article_id:
                item["article_id"] = article_id
            if description:
                item["description_override"] = description
            new_items.append(item)

        try:
            service.replace_items(po_id, new_items)
        except Exception as exc:
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        st.success(f"{len(new_items)} Position(en) gespeichert.")
        st.rerun()


def _render_supplier_confirmation(p: dict[str, Any]) -> None:
    """Lieferanten-Bestätigung erfassen (AB-Nr + bestätigter Termin)."""
    if (p.get("status") or "draft") not in ("sent", "confirmed", "in_production"):
        return

    with st.expander("📩 Lieferanten-Bestätigung erfassen", expanded=False):
        c1, c2 = st.columns(2)
        ab_nr = c1.text_input(
            "AB-Nr Lieferant",
            value=p.get("supplier_reference") or "",
            key=f"po_ab_nr_{p['id']}",
        )
        confirmed_due = c2.date_input(
            "Bestätigter Liefertermin",
            value=parse_date(p.get("confirmed_due_date") or p.get("expected_at"))
                  or date.today() + timedelta(days=21),
            key=f"po_confirmed_due_{p['id']}",
        )
        if st.button("Bestätigung speichern", key=f"po_confirm_save_{p['id']}"):
            changes: dict[str, Any] = {}
            if ab_nr.strip() and ab_nr.strip() != (p.get("supplier_reference") or ""):
                changes["supplier_reference"] = ab_nr.strip()
            if confirmed_due:
                changes["confirmed_due_date"] = confirmed_due
                changes["confirmed_at"] = date.today()
            try:
                service.update_po(p["id"], changes)
            except Exception as exc:
                st.error(f"Speichern fehlgeschlagen: {exc}")
                return
            st.success("Bestätigung gespeichert.")
            st.rerun()


def _render_history(p: dict[str, Any]) -> None:
    events = repo.list_po_events(p["id"], limit=50)
    with st.expander(f"🕒 Verlauf ({len(events)})", expanded=False):
        if not events:
            st.caption("Keine Ereignisse aufgezeichnet.")
            return
        for e in events[:30]:
            at = format_date(e.get("at")) or "—"
            actor = e.get("actor_label") or "—"
            etype = e.get("event_type") or "?"
            payload = e.get("payload") or {}
            desc = ""
            if etype == "status_change":
                old = PO_STATUS_LABELS.get(payload.get("old_status"), payload.get("old_status") or "?")
                new = PO_STATUS_LABELS.get(payload.get("new_status"), payload.get("new_status") or "?")
                desc = f"{old} → {new}"
            elif etype == "items_replaced":
                desc = f"{payload.get('count', 0)} Positionen"
            elif etype == "delivery_created":
                desc = (
                    f"Lieferung **{payload.get('delivery_number', '—')}** "
                    f"({payload.get('direction', '?')}, {payload.get('items', 0)} Pos.)"
                )
            else:
                desc = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "fields")
            st.caption(f"`{at}` · **{etype}** · {actor} · {desc}")


def _render_detail_tab() -> None:
    pos = repo.list_pos(limit=500)
    if not pos:
        st.info("Noch keine Bestellungen — leg zuerst eine im Tab **Neu anlegen** an.")
        return

    options: dict[str, str] = {}
    for p in pos:
        s = p.get("supplier") or {}
        sname = s.get("short_name") or s.get("legal_name") or "—"
        status_label = PO_STATUS_LABELS.get(p.get("status"), p.get("status") or "")
        options[p["id"]] = f"{p.get('po_number') or '?'} · {sname} · {status_label}"

    selected_id = st.selectbox(
        "Bestellung wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="po_detail_select",
    )
    if not selected_id:
        return

    p = repo.get_po(selected_id)
    if not p:
        st.error("Bestellung nicht gefunden.")
        return

    st.divider()
    _render_header_card(p)
    st.divider()

    st.markdown("### Status")
    cur = p.get("status") or "draft"
    render_status_stepper(
        PO_FLOW, cur, PO_STATUS_LABELS, PO_STATUS_COLORS,
        terminal_states=PO_TERMINAL,
    )
    _render_action_buttons(p)
    _render_supplier_confirmation(p)
    st.divider()

    st.markdown("### Positionen")
    _render_items_editor(p)
    st.divider()

    st.markdown("### Lieferungen / Wareneingänge")
    _render_smart_buttons(p)
    st.divider()

    st.markdown("### Bestellung-PDF")
    _render_pdf_section(p)
    st.divider()

    _render_history(p)


def _render_pdf_section(po: dict[str, Any]) -> None:
    items = repo.list_po_items(po["id"])
    has_items = bool(items)

    c1, c2 = st.columns([3, 2])
    if not has_items:
        c1.caption("ℹ️ Keine Positionen erfasst — die Bestellung wäre leer.")

    if c1.button(
        "📄 Bestellung-PDF generieren",
        key=f"gen_po_pdf_{po['id']}",
        type="primary",
        use_container_width=True,
        disabled=not has_items,
    ):
        try:
            from lib.beleg_generator import render_bestellung_pdf
            pdf_bytes = render_bestellung_pdf(po, items)
        except Exception as exc:
            st.error(f"PDF-Generierung fehlgeschlagen: {exc}")
            return
        st.session_state[f"po_pdf_{po['id']}"] = pdf_bytes
        st.success(f"PDF generiert ({len(pdf_bytes) // 1024} KB).")

    pdf_bytes = st.session_state.get(f"po_pdf_{po['id']}")
    if pdf_bytes:
        nr = po.get("po_number") or "Bestellung"
        c2.download_button(
            "⬇ Download",
            data=pdf_bytes,
            file_name=f"{nr}.pdf",
            mime="application/pdf",
            key=f"dl_po_pdf_{po['id']}",
            use_container_width=True,
        )
        from core.ui.mail import render_mail_link
        body = (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei unsere Bestellung {nr}. "
            "Bitte senden Sie uns Ihre Auftragsbestätigung mit verbindlichem Liefertermin "
            "an info@wts-trading.de.\n\n"
            "Mit freundlichen Grüßen\nWeber Trading & Service"
        )
        render_mail_link(to=None, subject=f"Bestellung {nr}", body=body)


# =====================================================================
#  Entry
# =====================================================================

def render() -> None:
    render_header(
        "Bestellungen",
        "Einkaufs-Bestellungen — Versand, Bestätigung, Wareneingang",
    )

    tab_list, tab_new, tab_detail = st.tabs(
        ["📋 Liste", "➕ Neu anlegen", "🔍 Detail"]
    )
    with tab_list:
        _render_list_tab()
    with tab_new:
        _render_create_tab()
    with tab_detail:
        _render_detail_tab()

    render_footer()
