"""Lieferübersicht — Listenansicht + Anlege-Form (Phase 0, Schritt 1+2a).

Tab 1 „Liste": offene Lieferungen mit Filter (Richtung / Status / Suche),
KPI-Zeile (offen / überfällig / heute / diese Woche) und Tabelle.

Tab 2 „Neu anlegen": Header-only Form (Items + Documents folgen im Detail-View
in Schritt 2b).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.ui.address_picker import render_address_picker
from core.ui.kpi import render_kpis
from core.ui.empty import render_empty_data, render_empty_filter
from core.ui.status import render_status_stepper
from core.utils import format_date as _format_date_util, parse_date

from . import repo, service
from .constants import (
    DOCUMENT_KIND_LABELS,
    INBOUND_STATUSES,
    LOCATION_LABELS,
    LOCATIONS,
    OUTBOUND_STATUSES,
    SHIPPING_METHOD_LABELS,
    SHIPPING_METHODS,
    STATUS_COLORS,
    STATUS_LABELS_DE,
    TERMIN_LABELS,
    TERMIN_TYPES,
    INCOTERMS_2020,
)


DIRECTION_LABELS = {
    "beide": "Beide",
    "outbound": "📤 Ausgehend",
    "inbound": "📥 Eingehend",
}

DONE_STATUSES = {"delivered", "received", "stored", "cancelled", "returned"}

NEW_PARTY_SENTINEL = "__none__"


def _expected_date(row: dict[str, Any]) -> date | None:
    return parse_date(row.get("expected_at"))


def _format_date(v: Any) -> str:
    return _format_date_util(v)


def _kpis(rows: list[dict[str, Any]]) -> None:
    open_rows = [r for r in rows if r.get("status") not in DONE_STATUSES]
    today = date.today()
    week_end = today + timedelta(days=7)

    overdue = sum(1 for r in open_rows if (d := _expected_date(r)) and d < today)
    today_due = sum(1 for r in open_rows if _expected_date(r) == today)
    this_week = sum(
        1 for r in open_rows if (d := _expected_date(r)) and today < d <= week_end
    )

    render_kpis([
        ("Offen", len(open_rows)),
        ("Überfällig", overdue),
        ("Heute fällig", today_due),
        ("Diese Woche", this_week),
    ])


def _urgency(row: dict[str, Any], today: date) -> str:
    if row.get("status") in DONE_STATUSES:
        return ""
    d = _expected_date(row)
    if not d:
        return ""
    delta = (d - today).days
    if delta < 0:
        return f"⚠️ {-delta} Tage überfällig"
    if delta == 0:
        return "🔥 heute"
    if delta <= 7:
        return f"in {delta} Tagen"
    return ""


def _table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_filter(
            label="Keine Lieferungen mit diesen Filtern.",
            reset_keys=["list_statuses", "list_direction", "list_search"],
        )
        return

    today = date.today()
    data: list[dict[str, Any]] = []
    for r in rows:
        party = r.get("parties") or {}
        data.append(
            {
                "Nr.": r.get("delivery_number") or "",
                "Richtung": DIRECTION_LABELS.get(r.get("direction"), r.get("direction") or ""),
                "Partei": party.get("short_name") or party.get("legal_name") or "",
                "Status": STATUS_LABELS_DE.get(r.get("status"), r.get("status") or ""),
                "Termin": _format_date(r.get("expected_at")),
                "Dringlichkeit": _urgency(r, today),
                "Methode": SHIPPING_METHOD_LABELS.get(
                    r.get("shipping_method"), r.get("shipping_method") or ""
                ),
                "Tracking": r.get("tracking_number") or "",
                "Kunden-Ref": r.get("customer_reference") or "",
            }
        )

    df = pd.DataFrame(data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nr.": st.column_config.TextColumn(width="small"),
            "Richtung": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="medium"),
            "Termin": st.column_config.TextColumn(width="small"),
            "Dringlichkeit": st.column_config.TextColumn(width="small"),
        },
    )


def _render_list_tab() -> None:
    c1, c2 = st.columns([1, 3])
    direction = c1.pills(
        "Richtung",
        ["beide", "outbound", "inbound"],
        selection_mode="single",
        default="beide",
        format_func=lambda v: DIRECTION_LABELS[v],
        key="list_direction",
    )

    all_statuses = sorted(set(OUTBOUND_STATUSES + INBOUND_STATUSES))
    default_open = [s for s in all_statuses if s not in DONE_STATUSES]
    if "list_statuses" not in st.session_state:
        st.session_state["list_statuses"] = default_open
    statuses = c2.pills(
        "Status",
        all_statuses,
        selection_mode="multi",
        format_func=lambda v: STATUS_LABELS_DE.get(v, v),
        key="list_statuses",
    )
    search = st.text_input("Suche (Nr., Tracking, Ref, Notiz)", "", key="list_search")

    directions = None if direction == "beide" else [direction]

    try:
        rows = repo.list_deliveries(
            directions=directions,
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Lieferungen nicht laden: {exc}")
        return

    _kpis(rows)
    _table(rows)
    st.caption(
        f"{len(rows)} Lieferungen geladen · sortiert nach Termin (frühster zuerst)."
    )


def _create_party_quick(name: str, party_type: str) -> str:
    """Inline-Mini-Anlage einer Partei (nur Pflichtfelder). Gibt party_id zurück."""
    res = (
        supabase()
        .table("parties")
        .insert({"legal_name": name.strip(), "type": party_type})
        .execute()
    )
    repo.list_parties.clear()
    return res.data[0]["id"]


def _render_create_tab() -> None:
    st.subheader("Neue Lieferung anlegen")

    direction = st.radio(
        "Richtung",
        ["outbound", "inbound"],
        format_func=lambda v: DIRECTION_LABELS[v],
        horizontal=True,
        key="new_direction",
    )

    # Streckengeschäft = Direktlieferung Lieferant → Endkunde, WTS koordiniert nur
    is_dropshipping = False
    source_party_id_raw = NEW_PARTY_SENTINEL
    if direction == "outbound":
        is_dropshipping = st.checkbox(
            "🚚 Streckengeschäft (Direktlieferung)",
            key="new_dropshipping",
            help="Lieferant liefert direkt an den Endkunden. Beide Parteien werden erfasst.",
        )

    party_type = "customer" if direction == "outbound" else "supplier"
    parties = repo.list_parties(party_type=party_type)
    party_choices: dict[str, str] = {NEW_PARTY_SENTINEL: "— ohne Partei —"}
    for p in parties:
        party_choices[p["id"]] = p.get("short_name") or p["legal_name"]

    role_label = "Kunde" if direction == "outbound" else "Lieferant"

    if is_dropshipping:
        c_to, c_from = st.columns(2)
        with c_to:
            party_id_raw = st.selectbox(
                f"📥 Empfänger ({role_label})",
                options=list(party_choices.keys()),
                format_func=lambda v: party_choices[v],
                key="new_party",
            )
        with c_from:
            suppliers = repo.list_parties(party_type="supplier")
            supplier_choices: dict[str, str] = {NEW_PARTY_SENTINEL: "— ohne Partei —"}
            for p in suppliers:
                supplier_choices[p["id"]] = p.get("short_name") or p["legal_name"]
            source_party_id_raw = st.selectbox(
                "📤 Absender (Lieferant)",
                options=list(supplier_choices.keys()),
                format_func=lambda v: supplier_choices[v],
                key="new_source_party",
            )
    else:
        party_id_raw = st.selectbox(
            role_label,
            options=list(party_choices.keys()),
            format_func=lambda v: party_choices[v],
            key="new_party",
        )

    with st.expander(f"➕ Neuen {role_label} schnell anlegen"):
        new_party_name = st.text_input("Firmenname", key="new_party_name")
        if st.button("Anlegen", key="new_party_submit"):
            if not new_party_name.strip():
                st.warning("Firmenname darf nicht leer sein.")
            else:
                pid = _create_party_quick(new_party_name, party_type)
                st.success(f"'{new_party_name}' angelegt - bitte oben auswählen.")
                # Manuell rerun, damit Dropdown frisch lädt
                st.rerun()

    # Optional: Verknüpfung zu Auftrag (outbound) / Bestellung (inbound)
    related_order_id = NEW_PARTY_SENTINEL
    related_po_id = NEW_PARTY_SENTINEL
    if direction == "outbound":
        from features.orders import repo as order_repo
        orders = order_repo.list_orders(limit=500)
        order_choices = {NEW_PARTY_SENTINEL: "— ohne Auftrag —"}
        for o in orders:
            if o.get("status") in ("done", "cancelled"):
                continue
            c = o.get("customer") or {}
            cname = c.get("short_name") or c.get("legal_name") or "—"
            order_choices[o["id"]] = f"{o.get('order_number')} · {cname}"
        related_order_id = st.selectbox(
            "📑 An Auftrag koppeln (optional)",
            list(order_choices.keys()),
            format_func=lambda v: order_choices[v],
            key="new_related_order",
        )
    else:
        from features.purchase_orders import repo as po_repo
        pos = po_repo.list_pos(limit=500)
        po_choices = {NEW_PARTY_SENTINEL: "— ohne Bestellung —"}
        for p in pos:
            if p.get("status") in ("received", "cancelled"):
                continue
            s = p.get("supplier") or {}
            sname = s.get("short_name") or s.get("legal_name") or "—"
            po_choices[p["id"]] = f"{p.get('po_number')} · {sname}"
        related_po_id = st.selectbox(
            "🛒 An Bestellung koppeln (optional)",
            list(po_choices.keys()),
            format_func=lambda v: po_choices[v],
            key="new_related_po",
        )

    # Adress-Picker außerhalb der Form
    real_party_id_for_addr = (
        party_id_raw if party_id_raw != NEW_PARTY_SENTINEL else None
    )
    shipping_addr_id_picked = render_address_picker(
        real_party_id_for_addr, "new_delivery_ship",
        "Lieferadresse" if direction == "outbound" else "Versandadresse Lieferant",
        kinds=["shipping", "registered"],
    )

    with st.form("create_delivery", clear_on_submit=True):
        c1, c2 = st.columns(2)
        expected_at = c1.date_input("Erwarteter Termin", value=date.today(), key="new_expected")
        termin_type = c2.selectbox(
            "Termin-Art",
            TERMIN_TYPES,
            format_func=lambda v: TERMIN_LABELS[v],
            index=0,
            key="new_termin_type",
        )

        c3, c4 = st.columns(2)
        shipping_method = c3.selectbox(
            "Versandmethode",
            ["—"] + SHIPPING_METHODS,
            format_func=lambda v: "— wählen —" if v == "—" else SHIPPING_METHOD_LABELS[v],
            key="new_shipping_method",
        )
        carrier = c4.text_input("Spediteur / Carrier", key="new_carrier")

        c5, c6 = st.columns(2)
        tracking_number = c5.text_input("Tracking-Nummer", key="new_tracking")
        customer_reference = c6.text_input(
            f"{'Kunden' if direction == 'outbound' else 'Lieferanten'}-Referenz",
            key="new_ref",
        )

        c7, c8 = st.columns(2)
        incoterms = c7.selectbox(
            "Incoterms",
            ["—"] + INCOTERMS_2020,
            format_func=lambda v: "— wählen —" if v == "—" else v,
            key="new_incoterms",
        )
        incoterms_place = c8.text_input("Incoterms-Ort", key="new_incoterms_place")

        notes = st.text_area("Notizen (sichtbar auf Lieferschein)", key="new_notes", height=80)
        internal_notes = st.text_area("Interne Notizen", key="new_internal_notes", height=80)

        submitted = st.form_submit_button("📦 Lieferung anlegen", type="primary", use_container_width=True)

        if submitted:
            payload: dict[str, Any] = {
                "direction": direction,
                "expected_at": expected_at,
                "termin_type": termin_type,
                "status": "draft" if direction == "outbound" else "announced",
            }
            if party_id_raw != NEW_PARTY_SENTINEL:
                payload["party_id"] = party_id_raw
            if is_dropshipping:
                if source_party_id_raw != NEW_PARTY_SENTINEL:
                    payload["source_party_id"] = source_party_id_raw
                payload["shipping_method"] = "direktlieferung"
            elif shipping_method != "—":
                payload["shipping_method"] = shipping_method
            if carrier.strip():
                payload["carrier"] = carrier.strip()
            if tracking_number.strip():
                payload["tracking_number"] = tracking_number.strip()
            if customer_reference.strip():
                payload["customer_reference"] = customer_reference.strip()
            if incoterms != "—":
                payload["incoterms"] = incoterms
            if incoterms_place.strip():
                payload["incoterms_place"] = incoterms_place.strip()
            if notes.strip():
                payload["notes"] = notes.strip()
            if internal_notes.strip():
                payload["internal_notes"] = internal_notes.strip()
            if direction == "outbound" and related_order_id != NEW_PARTY_SENTINEL:
                payload["related_order_id"] = related_order_id
            if direction == "inbound" and related_po_id != NEW_PARTY_SENTINEL:
                payload["related_po_id"] = related_po_id
            if shipping_addr_id_picked:
                payload["shipping_address_id"] = shipping_addr_id_picked

            try:
                new_id = service.create_delivery(payload)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Konnte Lieferung nicht anlegen: {exc}")
                return

            st.success(
                f"Lieferung angelegt (ID: `{new_id[:8]}...`). "
                "Wechsle zum Tab 'Liste', um sie zu sehen."
            )


FREE_ITEM_LABEL = "— freie Position —"
LOCATION_NONE_LABEL = "— ohne —"


def _render_header_card(d: dict[str, Any]) -> None:
    party = d.get("parties") or {}
    source_party = d.get("source_party") or {}
    direction = d.get("direction")
    role = "Kunde" if direction == "outbound" else "Lieferant"

    if source_party.get("legal_name"):
        st.warning(
            f"🚚 **Streckengeschäft (Direktlieferung)** — "
            f"Lieferant **{source_party['legal_name']}** liefert direkt an "
            f"Kunden **{party.get('legal_name') or '—'}**."
        )

    # Smart-Buttons: Verknüpfung zu Auftrag/PO
    related_order = d.get("related_order") or {}
    related_po = d.get("related_po") or {}
    if related_order.get("order_number") or related_po.get("po_number"):
        smart_lines = []
        if related_order.get("order_number"):
            smart_lines.append(
                f"📑 **Auftrag {related_order['order_number']}** "
                f"(siehe Page **Aufträge** → Detail)"
            )
        if related_po.get("po_number"):
            smart_lines.append(
                f"🛒 **Bestellung {related_po['po_number']}** "
                f"(siehe Page **Bestellungen** → Detail)"
            )
        st.info("Verknüpft mit:\n\n" + "\n\n".join(smart_lines))

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Nr.**\n\n`{d.get('delivery_number') or '?'}`")
    c2.markdown(f"**{role}**\n\n{party.get('legal_name') or '—'}")
    c3.markdown(f"**Termin**\n\n{_format_date(d.get('expected_at')) or '—'}")

    c4, c5, c6 = st.columns(3)
    c4.markdown(
        f"**Methode**\n\n{SHIPPING_METHOD_LABELS.get(d.get('shipping_method'), '—')}"
    )
    c5.markdown(f"**Tracking**\n\n{d.get('tracking_number') or '—'}")
    c6.markdown(f"**Kunden-Ref.**\n\n{d.get('customer_reference') or '—'}")

    if d.get("notes"):
        st.info(f"📝 {d['notes']}")


def _render_status_control(d: dict[str, Any]) -> None:
    direction = d.get("direction")
    statuses = OUTBOUND_STATUSES if direction == "outbound" else INBOUND_STATUSES
    current = d.get("status") or statuses[0]
    delivery_id = d["id"]

    # Visueller Status-Stepper über der Selectbox
    render_status_stepper(
        statuses,
        current,
        STATUS_LABELS_DE,
        STATUS_COLORS,
        terminal_states={"cancelled", "returned", "complaint"},
    )

    c1, c2 = st.columns([3, 1])
    new_status = c1.selectbox(
        "Status",
        statuses,
        index=statuses.index(current) if current in statuses else 0,
        format_func=lambda v: STATUS_LABELS_DE.get(v, v),
        key=f"status_select_{delivery_id}",
    )
    c2.write("")
    if c2.button(
        "Status setzen",
        key=f"status_btn_{delivery_id}",
        disabled=(new_status == current),
        use_container_width=True,
    ):
        try:
            result = service.update_status(delivery_id, new_status)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Status-Update fehlgeschlagen: {exc}")
            return
        msg = f"Status: {STATUS_LABELS_DE.get(new_status, new_status)}"
        booked = (result or {}).get("booked", 0)
        if booked:
            verb = "eingebucht" if d.get("direction") == "inbound" else "ausgebucht"
            msg += f" — {booked} Position(en) im Lager {verb}."
        parent_updated = (result or {}).get("parent_updated")
        if parent_updated:
            parent_label = {
                "shipped": "Auftrag → Geliefert",
                "partial": "Parent → Teilweise erfüllt",
                "received": "Bestellung → Empfangen",
            }.get(parent_updated, parent_updated)
            st.toast(f"📑 Auto-Update: {parent_label}", icon="✓")
        st.success(msg)
        st.rerun()

    # Hinweis-Banner: erkläre Auto-Verbuchung
    if d.get("direction") == "inbound" and current != "stored":
        st.caption("ℹ️ Beim Wechsel auf Status **'Eingelagert'** werden alle Items mit Artikel + Menge automatisch im Lager eingebucht.")
    elif d.get("direction") == "outbound" and current != "handed_to_carrier":
        st.caption("ℹ️ Beim Wechsel auf Status **'An Spediteur übergeben'** werden alle Items mit Artikel + Menge automatisch im Lager ausgebucht.")


def _render_items_editor(d: dict[str, Any]) -> None:
    delivery_id = d["id"]
    items = repo.list_delivery_items(delivery_id)
    articles = repo.list_articles()
    article_by_label = {
        f"{a['sku']} — {a.get('title_de') or ''}".strip(" —"): a for a in articles
    }
    article_options = [FREE_ITEM_LABEL] + sorted(article_by_label.keys())
    location_options = [LOCATION_NONE_LABEL] + LOCATIONS

    rows: list[dict[str, Any]] = []
    for it in items:
        a = it.get("articles") or {}
        if a.get("sku"):
            label = f"{a['sku']} — {a.get('title_de') or ''}".strip(" —")
            if label not in article_by_label:
                article_options.append(label)
        else:
            label = FREE_ITEM_LABEL
        rows.append(
            {
                "Pos": it.get("pos_nr") or 0,
                "Artikel": label,
                "Beschreibung": it.get("description_override") or "",
                "Menge erw.": float(it.get("qty_expected") or 0),
                "Menge tats.": float(it.get("qty_actual") or 0),
                "Einheit": it.get("unit") or "Stk",
                "Lager": it.get("storage_location") or LOCATION_NONE_LABEL,
                "Charge": it.get("batch_lot") or "",
                "ADR": a.get("adr_un_nr") or "",
                "Pfand": "✓" if a.get("is_pfand") else "",
            }
        )

    columns = [
        "Pos", "Artikel", "Beschreibung", "Menge erw.", "Menge tats.",
        "Einheit", "Lager", "Charge", "ADR", "Pfand",
    ]
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)

    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "Pos": st.column_config.NumberColumn(width="small", format="%d"),
            "Artikel": st.column_config.SelectboxColumn(
                options=article_options, required=False, width="medium"
            ),
            "Beschreibung": st.column_config.TextColumn(width="large"),
            "Menge erw.": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Menge tats.": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Einheit": st.column_config.TextColumn(width="small"),
            "Lager": st.column_config.SelectboxColumn(
                options=location_options, required=False, width="small"
            ),
            "Charge": st.column_config.TextColumn(width="small"),
            "ADR": st.column_config.TextColumn(width="small", disabled=True, help="UN-Nr aus Artikel-Stammdaten"),
            "Pfand": st.column_config.TextColumn(width="small", disabled=True, help="Pfand aus Artikel-Stammdaten"),
        },
        key=f"items_editor_{delivery_id}",
    )

    # ADR + Pfand Summary (basiert auf gespeicherten Items, nicht auf Editor-State)
    pfand_total_cents = 0
    pfand_lines: list[str] = []
    adr_agg: dict[tuple[str, str], dict[str, Any]] = {}
    for it in items:
        a = it.get("articles") or {}
        qty = float(it.get("qty_actual") or it.get("qty_expected") or 0)
        if not qty:
            continue
        if a.get("is_pfand") and a.get("pfand_per_unit_cents"):
            cents = int(a["pfand_per_unit_cents"]) * qty
            pfand_total_cents += int(cents)
            pfand_lines.append(
                f"- {a.get('sku') or ''} · {qty:g} × {a['pfand_per_unit_cents']/100:.2f} € = {cents/100:.2f} €"
            )
        if a.get("adr_un_nr"):
            key = (a["adr_un_nr"], a.get("adr_class") or "")
            agg = adr_agg.setdefault(
                key,
                {
                    "un_nr": a["adr_un_nr"],
                    "class": a.get("adr_class") or "",
                    "proper_name": a.get("adr_proper_name") or "",
                    "kg": 0.0,
                    "qty": 0.0,
                },
            )
            agg["qty"] += qty
            if a.get("adr_net_kg_per_unit"):
                agg["kg"] += float(a["adr_net_kg_per_unit"]) * qty

    if pfand_lines or adr_agg:
        c_pfand, c_adr = st.columns(2)
        with c_pfand:
            if pfand_lines:
                st.markdown(f"**🛢 Pfand-Übersicht** · Σ {pfand_total_cents/100:.2f} €")
                st.markdown("\n".join(pfand_lines))
            else:
                st.caption("Kein Pfand in dieser Lieferung.")
        with c_adr:
            if adr_agg:
                st.markdown("**⚠ Gefahrgut (ADR-Übersicht)**")
                for agg in adr_agg.values():
                    st.markdown(
                        f"- **{agg['un_nr']}** Klasse {agg['class']} · "
                        f"{agg['qty']:g} Einheiten"
                        + (f" · {agg['kg']:.3f} kg netto" if agg['kg'] else "")
                        + (f" — _{agg['proper_name']}_" if agg['proper_name'] else "")
                    )
                st.caption("Für 1000-Punkte-Regel im ADR-Beförderungspapier nutzen.")
            else:
                st.caption("Keine ADR-Position in dieser Lieferung.")

    if st.button("💾 Positionen speichern", key=f"save_items_{delivery_id}", type="primary"):
        new_items: list[dict[str, Any]] = []
        for i, row in edited.iterrows():
            label = (row.get("Artikel") or FREE_ITEM_LABEL)
            if label == FREE_ITEM_LABEL:
                article_id = None
            else:
                a = article_by_label.get(label)
                article_id = a["id"] if a else None

            description = (row.get("Beschreibung") or "").strip()
            qty_expected = float(row.get("Menge erw.") or 0)
            qty_actual = float(row.get("Menge tats.") or 0)

            if not article_id and not description and qty_expected == 0 and qty_actual == 0:
                continue  # leere Zeile

            location = row.get("Lager") or LOCATION_NONE_LABEL
            item: dict[str, Any] = {
                "pos_nr": int(row.get("Pos") or i + 1),
                "unit": (row.get("Einheit") or "Stk").strip() or "Stk",
            }
            if article_id:
                item["article_id"] = article_id
            if description:
                item["description_override"] = description
            if qty_expected:
                item["qty_expected"] = qty_expected
            if qty_actual:
                item["qty_actual"] = qty_actual
            if location and location != LOCATION_NONE_LABEL:
                item["storage_location"] = location
            charge = (row.get("Charge") or "").strip()
            if charge:
                item["batch_lot"] = charge

            new_items.append(item)

        try:
            service.replace_items(delivery_id, new_items)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        st.success(f"{len(new_items)} Position(en) gespeichert.")
        st.rerun()


def _render_documents(d: dict[str, Any]) -> None:
    docs = repo.list_delivery_documents(d["id"])
    if not docs:
        st.caption("Keine Dokumente angehängt.")
        return
    for doc in docs:
        st.markdown(
            f"- **{DOCUMENT_KIND_LABELS.get(doc.get('kind'), doc.get('kind') or '?')}** "
            f"· `{doc.get('filename')}` "
            f"· {_format_date(doc.get('uploaded_at'))}"
        )


def _render_detail_tab() -> None:
    deliveries = repo.list_deliveries(limit=500)
    if not deliveries:
        render_empty_data(
            title="Noch keine Lieferungen",
            description="Leg deine erste Lieferung manuell an oder erstelle sie automatisch aus einem Auftrag/einer Bestellung.",
            icon="📦",
        )
        return

    options: dict[str, str] = {}
    for d in deliveries:
        party = d.get("parties") or {}
        party_label = party.get("short_name") or party.get("legal_name") or "—"
        date_label = _format_date(d.get("expected_at")) or "—"
        direction_arrow = "📤" if d.get("direction") == "outbound" else "📥"
        options[d["id"]] = (
            f"{direction_arrow} {d.get('delivery_number') or '?'} · {party_label} · {date_label}"
        )

    selected_id = st.selectbox(
        "Lieferung wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="detail_select",
    )
    if not selected_id:
        return

    delivery = repo.get_delivery(selected_id)
    if not delivery:
        st.error("Lieferung nicht gefunden.")
        return

    st.divider()
    _render_header_card(delivery)
    st.divider()

    st.markdown("### Status")
    _render_status_control(delivery)
    st.divider()

    st.markdown("### Positionen")
    _render_items_editor(delivery)
    st.divider()

    st.markdown("### Lieferschein-PDF")
    _render_pdf_section(delivery)
    st.divider()

    st.markdown("### Dokumente")
    _render_documents(delivery)


def _render_pdf_section(delivery: dict[str, Any]) -> None:
    items = repo.list_delivery_items(delivery["id"])
    has_items = bool(items)

    c1, c2 = st.columns([3, 2])
    if not has_items:
        c1.caption("ℹ️ Keine Positionen erfasst — der Lieferschein wäre leer. Erst Items speichern.")

    if c1.button(
        "📄 Lieferschein-PDF generieren",
        key=f"gen_pdf_{delivery['id']}",
        type="primary",
        use_container_width=True,
        disabled=not has_items,
    ):
        try:
            from lib.lieferschein_generator import render_lieferschein_pdf
            pdf_bytes = render_lieferschein_pdf(delivery, items)
        except Exception as exc:  # noqa: BLE001
            st.error(f"PDF-Generierung fehlgeschlagen: {exc}")
            return
        st.session_state[f"pdf_{delivery['id']}"] = pdf_bytes
        st.success(f"PDF generiert ({len(pdf_bytes) // 1024} KB).")

    pdf_bytes = st.session_state.get(f"pdf_{delivery['id']}")
    if pdf_bytes:
        nr = delivery.get("delivery_number") or "Lieferschein"
        c2.download_button(
            "⬇ Download",
            data=pdf_bytes,
            file_name=f"{nr}.pdf",
            mime="application/pdf",
            key=f"dl_pdf_{delivery['id']}",
            use_container_width=True,
        )


def render() -> None:
    """Komplette Lieferübersicht-Page rendern."""
    render_header(
        "Lieferungen",
        "Überblick: was muss raus, was kommt rein",
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
