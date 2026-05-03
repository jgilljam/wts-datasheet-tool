"""Verkaufs-Aufträge — Liste / Anlegen / Detail."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.ui.address_picker import render_address_picker
from core.ui.empty import render_empty_data, render_empty_filter
from core.ui.kpi import render_kpis
from core.ui.status import render_status_pill, render_status_stepper
from core.utils import cents_to_eur, eur_to_cents, format_date, parse_date

from features.deliveries import repo as delivery_repo
from features.deliveries.constants import STATUS_LABELS_DE as DELIVERY_STATUS_LABELS

from . import repo, service
from .constants import (
    INCOTERMS_2020,
    ORDER_DONE_STATUSES,
    ORDER_FLOW,
    ORDER_LOCKED_STATUSES,
    ORDER_NEXT_ACTION,
    ORDER_STATUS_COLORS,
    ORDER_STATUS_LABELS,
    ORDER_STATUSES,
    ORDER_TERMINAL,
    TAX_RATE_DEFAULT,
    TAX_RATE_REVERSE_CHARGE,
)


FREE_ITEM_LABEL = "— freie Position —"
NEW_PARTY_SENTINEL = "__none__"


# =====================================================================
#  Tab 1 — Liste
# =====================================================================

def _kpis(rows: list[dict[str, Any]]) -> None:
    open_rows = [r for r in rows if r.get("status") not in ORDER_DONE_STATUSES]
    today = date.today()

    overdue = sum(
        1 for r in open_rows
        if (d := parse_date(r.get("due_date"))) and d < today
    )
    in_delivery = sum(1 for r in open_rows if r.get("status") in ("partial", "in_production"))

    # Umsatz Monat: net + tax aller Aufträge mit ordered_at im aktuellen Monat
    month_start = today.replace(day=1)
    month_revenue_cents = sum(
        int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        for r in rows
        if (d := parse_date(r.get("ordered_at"))) and d >= month_start
    )

    render_kpis([
        ("Offene Aufträge", len(open_rows)),
        ("In Lieferung", in_delivery),
        ("Überfällig", overdue),
        ("Umsatz Monat", cents_to_eur(month_revenue_cents) or "0,00 €"),
    ])


def _table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_filter(
            label="Keine Aufträge mit diesen Filtern.",
            reset_keys=["orders_list_statuses", "orders_list_search"],
        )
        return
    today = date.today()
    data: list[dict[str, Any]] = []
    ids: list[str] = []
    for r in rows:
        c = r.get("customer") or {}
        due = parse_date(r.get("due_date"))
        urgency = ""
        if due and r.get("status") not in ORDER_DONE_STATUSES:
            delta = (due - today).days
            if delta < 0:
                urgency = f"⚠️ {-delta} d überfällig"
            elif delta == 0:
                urgency = "🔥 heute"
            elif delta <= 7:
                urgency = f"in {delta} Tagen"
        ids.append(r["id"])
        data.append({
            "Nr.": r.get("order_number") or "",
            "Kunde": c.get("short_name") or c.get("legal_name") or "—",
            "Datum": format_date(r.get("ordered_at")),
            "Liefertermin": format_date(r.get("due_date")),
            "Dringlichkeit": urgency,
            "Kunden-Best.-Nr.": r.get("customer_reference") or "",
            "Netto": cents_to_eur(r.get("total_net_cents")),
            "Status": ORDER_STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
        })
    df = pd.DataFrame(data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="orders_list_table",
        column_config={
            "Nr.": st.column_config.TextColumn(width="small"),
            "Datum": st.column_config.TextColumn(width="small"),
            "Liefertermin": st.column_config.TextColumn(width="small"),
            "Netto": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="small"),
        },
    )
    selected_indices = (sel.selection.rows if sel and sel.selection else []) or []
    if selected_indices:
        _render_bulk_actions([(ids[i], rows[i]) for i in selected_indices])


def _render_bulk_actions(selected: list[tuple[str, dict[str, Any]]]) -> None:
    """Bulk-Aktionen für markierte Aufträge."""
    n = len(selected)
    box = st.container(border=True)
    with box:
        st.markdown(f"**{n} Auftrag(e) markiert**")
        c1, c2, c3 = st.columns([2, 2, 2])

        # Bulk-Bestätigen: nur Drafts kommen in Frage
        drafts = [(oid, o) for oid, o in selected if o.get("status") == "draft"]
        if c1.button(
            f"✓ {len(drafts)} Entwurf/Entwürfe bestätigen",
            disabled=not drafts,
            key="bulk_orders_confirm",
            use_container_width=True,
            type="primary",
        ):
            errors: list[str] = []
            for oid, _ in drafts:
                try:
                    service.update_status(oid, "confirmed", comment="Bulk-Bestätigung")
                except Exception as exc:
                    errors.append(f"{oid[:8]}…: {exc}")
            if errors:
                st.error("Fehler bei: " + " · ".join(errors))
            else:
                st.toast(f"✓ {len(drafts)} Auftrag/Aufträge bestätigt", icon="✓")
            st.rerun()

        # Bulk-In-Produktion-Setzen: alle confirmed
        confirmed = [(oid, o) for oid, o in selected if o.get("status") == "confirmed"]
        if c2.button(
            f"🔧 {len(confirmed)} → In Produktion",
            disabled=not confirmed,
            key="bulk_orders_in_prod",
            use_container_width=True,
        ):
            errors = []
            for oid, _ in confirmed:
                try:
                    service.update_status(oid, "in_production", comment="Bulk-Update")
                except Exception as exc:
                    errors.append(f"{oid[:8]}…: {exc}")
            if errors:
                st.error("Fehler bei: " + " · ".join(errors))
            else:
                st.toast(f"🔧 {len(confirmed)} → In Produktion", icon="✓")
            st.rerun()

        # Bulk-Abschließen: alle shipped
        shipped = [(oid, o) for oid, o in selected if o.get("status") == "shipped"]
        if c3.button(
            f"✓ {len(shipped)} Abschließen",
            disabled=not shipped,
            key="bulk_orders_done",
            use_container_width=True,
        ):
            errors = []
            for oid, _ in shipped:
                try:
                    service.update_status(oid, "done", comment="Bulk-Abschluss")
                except Exception as exc:
                    errors.append(f"{oid[:8]}…: {exc}")
            if errors:
                st.error("Fehler bei: " + " · ".join(errors))
            else:
                st.toast(f"✓ {len(shipped)} abgeschlossen", icon="✓")
            st.rerun()


def _render_list_tab() -> None:
    default_open = [s for s in ORDER_STATUSES if s not in ORDER_DONE_STATUSES]
    if "orders_list_statuses" not in st.session_state:
        st.session_state["orders_list_statuses"] = default_open
    statuses = st.pills(
        "Status",
        ORDER_STATUSES,
        selection_mode="multi",
        format_func=lambda v: ORDER_STATUS_LABELS.get(v, v),
        key="orders_list_statuses",
    )
    search = st.text_input("Suche (Nr., Kunden-Best.-Nr., Notiz)", "", key="orders_list_search")

    try:
        rows = repo.list_orders(
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:
        st.error(f"Konnte Aufträge nicht laden: {exc}")
        return

    _kpis(rows)
    _table(rows)
    st.caption(f"{len(rows)} Aufträge geladen.")


# =====================================================================
#  Tab 2 — Neu anlegen
# =====================================================================

def _create_party_quick(name: str) -> str:
    res = (
        supabase()
        .table("parties")
        .insert({"legal_name": name.strip(), "type": "customer"})
        .execute()
    )
    delivery_repo.list_parties.clear()
    return res.data[0]["id"]


def _render_create_tab() -> None:
    st.subheader("Neuer Auftrag")
    st.caption(
        "Pflicht: Kunde + Datum. Items, Adresse, Konditionen werden im Detail erfasst."
    )

    parties = delivery_repo.list_parties(party_type="customer")
    party_choices = {NEW_PARTY_SENTINEL: "— wählen —"}
    for p in parties:
        party_choices[p["id"]] = p.get("short_name") or p["legal_name"]

    party_id = st.selectbox(
        "Kunde",
        list(party_choices.keys()),
        format_func=lambda v: party_choices[v],
        key="new_order_party",
    )

    with st.expander("➕ Neuen Kunden schnell anlegen"):
        new_name = st.text_input("Firmenname", key="new_order_party_name")
        if st.button("Anlegen", key="new_order_party_submit"):
            if not new_name.strip():
                st.warning("Firmenname darf nicht leer sein.")
            else:
                _create_party_quick(new_name)
                st.success(f"'{new_name}' angelegt — bitte oben auswählen.")
                st.rerun()

    # Adress-Picker außerhalb der Form (Streamlit reagiert sonst nicht auf Party-Wechsel)
    real_party_id = party_id if party_id != NEW_PARTY_SENTINEL else None
    shipping_addr_id = render_address_picker(
        real_party_id, "new_order_ship", "Lieferadresse",
        kinds=["shipping", "registered"],
    )
    billing_addr_id = render_address_picker(
        real_party_id, "new_order_bill", "Rechnungsadresse",
        kinds=["billing", "registered"],
    )

    with st.form("create_order", clear_on_submit=True):
        c1, c2 = st.columns(2)
        ordered_at = c1.date_input("Auftragsdatum", value=date.today(), key="new_order_ordered_at")
        due_date = c2.date_input("Liefertermin", value=date.today() + timedelta(days=14), key="new_order_due")

        c3, c4 = st.columns(2)
        customer_reference = c3.text_input("Kunden-Bestell-Nr.", key="new_order_ref")
        payment_terms = c4.number_input(
            "Zahlungsziel (Tage)", min_value=0, max_value=180, value=14, step=1,
            key="new_order_payment_terms",
        )

        c5, c6 = st.columns(2)
        incoterms = c5.selectbox(
            "Incoterms",
            ["—"] + INCOTERMS_2020,
            format_func=lambda v: "— wählen —" if v == "—" else v,
            key="new_order_incoterms",
        )
        incoterms_place = c6.text_input("Incoterms-Ort", key="new_order_incoterms_place")

        notes = st.text_area("Notizen (sichtbar auf Beleg)", key="new_order_notes", height=80)
        internal_notes = st.text_area("Interne Notizen", key="new_order_internal_notes", height=80)

        submitted = st.form_submit_button(
            "📑 Auftrag anlegen", type="primary", use_container_width=True
        )

        if submitted:
            if party_id == NEW_PARTY_SENTINEL:
                st.error("Bitte einen Kunden wählen.")
                return

            payload: dict[str, Any] = {
                "customer_id": party_id,
                "status": "draft",
                "ordered_at": ordered_at,
                "due_date": due_date,
                "payment_terms_days": int(payment_terms) if payment_terms else None,
            }
            if customer_reference.strip():
                payload["customer_reference"] = customer_reference.strip()
            if shipping_addr_id:
                payload["shipping_address_id"] = shipping_addr_id
            if billing_addr_id:
                payload["billing_address_id"] = billing_addr_id
            if incoterms != "—":
                payload["incoterms"] = incoterms
            if incoterms_place.strip():
                payload["incoterms_place"] = incoterms_place.strip()
            if notes.strip():
                payload["notes"] = notes.strip()
            if internal_notes.strip():
                payload["internal_notes"] = internal_notes.strip()

            try:
                new_id = service.create_order(payload)
            except Exception as exc:
                st.error(f"Konnte Auftrag nicht anlegen: {exc}")
                return

            st.success(
                f"Auftrag angelegt (`{new_id[:8]}…`). "
                "Wechsle zum Tab **Detail**, um Positionen zu erfassen."
            )


# =====================================================================
#  Tab 3 — Detail
# =====================================================================

def _render_header_card(o: dict[str, Any]) -> None:
    customer = o.get("customer") or {}
    rev_charge = bool(customer.get("is_reverse_charge_eligible"))

    if rev_charge:
        st.info(
            f"💶 **Reverse-Charge** — Kunde **{customer.get('legal_name')}** ist "
            "EU-B2B mit USt-ID. USt-Sätze werden auf 0% vorgeschlagen."
        )

    pill = render_status_pill(
        o.get("status") or "draft",
        ORDER_STATUS_LABELS,
        ORDER_STATUS_COLORS,
    )
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
        f"<h3 style='margin:0;'>📑 {o.get('order_number') or '—'}</h3>"
        f"<div>{pill}</div></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Kunde**\n\n{customer.get('legal_name') or '—'}")
    c2.markdown(f"**Auftragsdatum**\n\n{format_date(o.get('ordered_at')) or '—'}")
    c3.markdown(f"**Liefertermin**\n\n{format_date(o.get('due_date')) or '—'}")

    c4, c5, c6 = st.columns(3)
    c4.markdown(f"**Kunden-Best.-Nr.**\n\n{o.get('customer_reference') or '—'}")
    c5.markdown(f"**Zahlungsziel**\n\n{o.get('payment_terms_days') or '—'} Tage")
    c6.markdown(
        f"**Incoterms**\n\n{o.get('incoterms') or '—'} "
        f"{o.get('incoterms_place') or ''}"
    )

    if o.get("notes"):
        st.info(f"📝 {o['notes']}")


def _render_action_buttons(o: dict[str, Any]) -> None:
    """Dominanter Aktions-Button für nächsten Schritt + Overflow."""
    order_id = o["id"]
    cur = o.get("status") or "draft"

    if cur in ORDER_TERMINAL:
        st.caption(f"Auftrag im Endstatus: **{ORDER_STATUS_LABELS.get(cur, cur)}**.")
        return

    next_action = ORDER_NEXT_ACTION.get(cur)

    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])

    primary = False
    if next_action:
        next_status, label = next_action
        primary = c1.button(
            label,
            key=f"action_primary_{order_id}",
            type="primary",
            use_container_width=True,
        )
    else:
        c1.caption(f"Status: **{ORDER_STATUS_LABELS.get(cur, cur)}**")

    create_delivery_btn = False
    if cur in ("confirmed", "in_production", "partial"):
        create_delivery_btn = c2.button(
            "📦 Lieferung erstellen",
            key=f"action_delivery_{order_id}",
            use_container_width=True,
            help="Erzeugt eine outbound-Lieferung mit den Auftragspositionen",
        )

    create_invoice_btn = False
    if cur in ("partial", "shipped", "done"):
        create_invoice_btn = c3.button(
            "📄 Rechnung erstellen",
            key=f"action_invoice_{order_id}",
            use_container_width=True,
            help="Erzeugt einen Rechnungsentwurf mit den noch nicht fakturierten Mengen",
        )

    cancel_btn = False
    if cur not in ("done", "cancelled"):
        cancel_btn = c4.button(
            "✕ Stornieren",
            key=f"action_cancel_{order_id}",
            use_container_width=True,
        )

    if primary:
        # Validierung: Bestätigen erfordert mind. 1 Item
        if cur == "draft":
            items = repo.list_order_items(order_id)
            if not items:
                st.error("Mindestens 1 Position nötig, bevor der Auftrag bestätigt werden kann.")
                return
        try:
            service.update_status(order_id, next_status)
        except Exception as exc:
            st.error(f"Status-Update fehlgeschlagen: {exc}")
            return
        st.success(f"Status: {ORDER_STATUS_LABELS.get(next_status, next_status)}")
        st.rerun()

    if create_delivery_btn:
        try:
            delivery_id = service.create_delivery_from_order(order_id)
        except Exception as exc:
            st.error(f"Lieferung konnte nicht erzeugt werden: {exc}")
            return
        st.success(f"Lieferung angelegt — wechsle zur Lieferungen-Page (`{delivery_id[:8]}…`).")
        # Status auf 'partial' wenn vorher confirmed/in_production
        if cur in ("confirmed", "in_production"):
            try:
                service.update_status(order_id, "partial", comment="Lieferung erstellt")
            except Exception:
                pass
        st.rerun()

    if create_invoice_btn:
        try:
            from features.invoices import service as invoice_service
            invoice_id = invoice_service.create_invoice_from_order(order_id, mode="complete")
        except Exception as exc:
            st.error(f"Rechnung konnte nicht erzeugt werden: {exc}")
            return
        st.success(
            f"Rechnungsentwurf angelegt (`{invoice_id[:8]}…`). "
            "Wechsle zur **Rechnungen**-Page → Detail, um Leistungsdatum zu prüfen "
            "und festzuschreiben."
        )
        st.rerun()

    if cancel_btn:
        try:
            service.update_status(order_id, "cancelled")
        except Exception as exc:
            st.error(f"Storno fehlgeschlagen: {exc}")
            return
        st.warning("Auftrag storniert.")
        st.rerun()


def _render_smart_buttons(o: dict[str, Any]) -> None:
    """Counter-Buttons zu verknüpften Belegen (Lieferungen + Rechnungen)."""
    from features.invoices import repo as invoice_repo
    from features.invoices.constants import INVOICE_STATUS_LABELS

    deliveries = repo.list_deliveries_for_order(o["id"])
    invoices = invoice_repo.list_invoices_for_order(o["id"])

    if not deliveries and not invoices:
        st.caption("📦 Keine Lieferungen oder Rechnungen verknüpft.")
        return

    if deliveries:
        st.markdown("**Verknüpfte Lieferungen**")
        rows: list[dict[str, Any]] = []
        for d in deliveries:
            rows.append({
                "Nr.": d.get("delivery_number") or "—",
                "Status": DELIVERY_STATUS_LABELS.get(d.get("status"), d.get("status") or ""),
                "Termin": format_date(d.get("expected_at")),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if invoices:
        st.markdown("**Verknüpfte Rechnungen**")
        rows = []
        for i in invoices:
            brutto = int(i.get("total_net_cents") or 0) + int(i.get("tax_total_cents") or 0)
            rows.append({
                "Nr.": i.get("invoice_number") or "Entwurf",
                "Status": INVOICE_STATUS_LABELS.get(i.get("status"), i.get("status") or ""),
                "Datum": format_date(i.get("issued_at")),
                "Brutto": cents_to_eur(brutto),
                "Storno?": "✓" if i.get("reverses_id") else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_items_editor(o: dict[str, Any]) -> None:
    """Items-Editor mit Preis/USt/Rabatt. Read-only ab confirmed."""
    order_id = o["id"]
    is_locked = (o.get("status") or "draft") in ORDER_LOCKED_STATUSES

    items = repo.list_order_items(order_id)
    articles = delivery_repo.list_articles()
    article_by_label = {
        f"{a['sku']} — {a.get('title_de') or ''}".strip(" —"): a for a in articles
    }
    article_options = [FREE_ITEM_LABEL] + sorted(article_by_label.keys())

    customer = o.get("customer") or {}
    default_tax = TAX_RATE_REVERSE_CHARGE if customer.get("is_reverse_charge_eligible") else TAX_RATE_DEFAULT

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
            "Preis €": unit_price_eur,
            "Rabatt %": float(it.get("discount_pct") or 0),
            "USt %": float(it.get("tax_rate") if it.get("tax_rate") is not None else default_tax),
        })

    columns = ["Pos", "Artikel", "Beschreibung", "Menge", "Einheit", "Preis €", "Rabatt %", "USt %"]
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)

    if is_locked:
        st.caption(
            f"🔒 **GoBD-gesperrt** (Status: {ORDER_STATUS_LABELS.get(o.get('status'), o.get('status'))}). "
            "Positionen sind nur lesbar — Änderungen erfordern Storno + Neuanlage."
        )

    edited = st.data_editor(
        df,
        num_rows="fixed" if is_locked else "dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=is_locked,
        column_config={
            "Pos": st.column_config.NumberColumn(width="small", format="%d"),
            "Artikel": st.column_config.SelectboxColumn(
                options=article_options, required=False, width="medium"
            ),
            "Beschreibung": st.column_config.TextColumn(width="large"),
            "Menge": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Einheit": st.column_config.TextColumn(width="small"),
            "Preis €": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Rabatt %": st.column_config.NumberColumn(format="%.1f", width="small"),
            "USt %": st.column_config.NumberColumn(format="%.0f", width="small"),
        },
        key=f"order_items_{order_id}",
    )

    # Live-Summen-Berechnung aus Editor-State
    net = 0.0
    tax = 0.0
    for _, row in edited.iterrows():
        qty = float(row.get("Menge") or 0)
        price = float(row.get("Preis €") or 0)
        disc = float(row.get("Rabatt %") or 0)
        tax_rate = float(row.get("USt %") or 0)
        gross = qty * price * 100  # in Cents
        net_line = gross * (1 - disc / 100.0)
        net += net_line
        tax += net_line * tax_rate / 100.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Netto", cents_to_eur(int(round(net))) or "0,00 €")
    c2.metric("USt", cents_to_eur(int(round(tax))) or "0,00 €")
    c3.metric("Brutto", cents_to_eur(int(round(net + tax))) or "0,00 €")

    if is_locked:
        return

    if st.button("💾 Positionen speichern", key=f"save_order_items_{order_id}", type="primary"):
        new_items: list[dict[str, Any]] = []
        for i, row in edited.iterrows():
            label = row.get("Artikel") or FREE_ITEM_LABEL
            article_id = None
            article_obj = None
            if label != FREE_ITEM_LABEL:
                article_obj = article_by_label.get(label)
                article_id = article_obj["id"] if article_obj else None

            description = (row.get("Beschreibung") or "").strip()
            qty = float(row.get("Menge") or 0)
            unit = (row.get("Einheit") or "Stk").strip() or "Stk"

            if not article_id and not description and qty == 0:
                continue

            unit_price_cents = eur_to_cents(row.get("Preis €")) or 0
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
            }
            if article_id:
                item["article_id"] = article_id
            if description:
                item["description_override"] = description
            new_items.append(item)

        try:
            service.replace_items(order_id, new_items)
        except Exception as exc:
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        st.success(f"{len(new_items)} Position(en) gespeichert.")
        st.rerun()


def _render_history(o: dict[str, Any]) -> None:
    events = repo.list_order_events(o["id"], limit=50)
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
                old = ORDER_STATUS_LABELS.get(payload.get("old_status"), payload.get("old_status") or "?")
                new = ORDER_STATUS_LABELS.get(payload.get("new_status"), payload.get("new_status") or "?")
                desc = f"{old} → {new}"
            elif etype == "items_replaced":
                desc = f"{payload.get('count', 0)} Positionen"
            elif etype == "delivery_created":
                desc = f"Lieferung **{payload.get('delivery_number', '—')}** ({payload.get('items', 0)} Pos.)"
            else:
                desc = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "fields")
            st.caption(f"`{at}` · **{etype}** · {actor} · {desc}")


def _render_detail_tab() -> None:
    orders = repo.list_orders(limit=500)
    if not orders:
        render_empty_data(
            title="Noch keine Aufträge",
            description="Leg deinen ersten Verkaufsauftrag an — manuell oder als Konvertierung einer Anfrage.",
            icon="📑",
        )
        return

    options: dict[str, str] = {}
    for o in orders:
        c = o.get("customer") or {}
        cname = c.get("short_name") or c.get("legal_name") or "—"
        status_label = ORDER_STATUS_LABELS.get(o.get("status"), o.get("status") or "")
        options[o["id"]] = f"{o.get('order_number') or '?'} · {cname} · {status_label}"

    selected_id = st.selectbox(
        "Auftrag wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="orders_detail_select",
    )
    if not selected_id:
        return

    o = repo.get_order(selected_id)
    if not o:
        st.error("Auftrag nicht gefunden.")
        return

    st.divider()
    _render_header_card(o)
    st.divider()

    st.markdown("### Status")
    cur = o.get("status") or "draft"
    render_status_stepper(
        ORDER_FLOW, cur, ORDER_STATUS_LABELS, ORDER_STATUS_COLORS,
        terminal_states=ORDER_TERMINAL,
    )
    _render_action_buttons(o)
    st.divider()

    st.markdown("### Positionen")
    _render_items_editor(o)
    st.divider()

    st.markdown("### Bilanz: Liefer- und Rechnungsfortschritt")
    _render_fulfillment_balance(o)
    st.divider()

    st.markdown("### Lieferungen")
    _render_smart_buttons(o)
    st.divider()

    st.markdown("### Auftragsbestätigung-PDF")
    _render_pdf_section(o)
    st.divider()

    _render_history(o)


def _render_fulfillment_balance(o: dict[str, Any]) -> None:
    """Restmengen-Bilanz: bestellt vs. geliefert vs. fakturiert je Position."""
    rows = repo.get_fulfillment_balance(o["id"])
    if not rows:
        st.caption("Keine Positionen — keine Bilanz.")
        return

    total_qty = sum(r["qty"] for r in rows)
    total_delv = sum(r["delivered"] for r in rows)
    total_inv = sum(r["invoiced"] for r in rows)

    pct_delv = round((total_delv / total_qty) * 100) if total_qty > 0 else 0
    pct_inv = round((total_inv / total_qty) * 100) if total_qty > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Bestellt (Σ Stk)", f"{total_qty:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c2.metric(
        "Geliefert", f"{total_delv:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        delta=f"{pct_delv}% erfüllt", delta_color="off",
    )
    c3.metric(
        "Fakturiert", f"{total_inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        delta=f"{pct_inv}% berechnet", delta_color="off",
    )

    icon = {"open": "○", "partial": "◑", "done": "●", "—": "—"}
    df_rows: list[dict[str, Any]] = []
    for r in rows:
        df_rows.append({
            "Pos": r["pos_nr"],
            "Artikel": r["sku"] or "—",
            "Bezeichnung": r["title"][:60],
            "Bestellt": r["qty"],
            "Geliefert": r["delivered"],
            "Offen Lieferung": r["open_delivery"],
            "Lief.": f"{icon[r['status_delivery']]} {r['status_delivery']}",
            "Fakturiert": r["invoiced"],
            "Offen Rechn.": r["open_invoice"],
            "Rechn.": f"{icon[r['status_invoice']]} {r['status_invoice']}",
        })

    df = pd.DataFrame(df_rows)
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Pos": st.column_config.NumberColumn(width="small", format="%d"),
            "Bestellt":   st.column_config.NumberColumn(format="%.2f", width="small"),
            "Geliefert":  st.column_config.NumberColumn(format="%.2f", width="small"),
            "Offen Lieferung": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Fakturiert": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Offen Rechn.": st.column_config.NumberColumn(format="%.2f", width="small"),
        },
    )

    has_open_delv = any(r["open_delivery"] > 0 for r in rows)
    has_open_inv = any(r["open_invoice"] > 0 for r in rows)
    if not has_open_delv and not has_open_inv:
        st.success("✅ Alles ausgeliefert und fakturiert.")
    elif has_open_delv and has_open_inv:
        st.caption(
            "ℹ️ Restmengen offen. **Lieferungen** anlegen über _Lieferungen_-Sektion. "
            "**Rechnung** über _Rechnung erstellen_-Button — liefert nur die Rest-fakturierten Mengen."
        )


def _render_pdf_section(order: dict[str, Any]) -> None:
    items = repo.list_order_items(order["id"])
    has_items = bool(items)

    c1, c2 = st.columns([3, 2])
    if not has_items:
        c1.caption("ℹ️ Keine Positionen erfasst — die Auftragsbestätigung wäre leer.")

    if c1.button(
        "📄 Auftragsbestätigung-PDF generieren",
        key=f"gen_order_pdf_{order['id']}",
        type="primary",
        use_container_width=True,
        disabled=not has_items,
    ):
        try:
            from lib.beleg_generator import render_auftragsbestaetigung_pdf
            pdf_bytes = render_auftragsbestaetigung_pdf(order, items)
        except Exception as exc:
            st.error(f"PDF-Generierung fehlgeschlagen: {exc}")
            return
        st.session_state[f"order_pdf_{order['id']}"] = pdf_bytes
        st.success(f"PDF generiert ({len(pdf_bytes) // 1024} KB).")

    pdf_bytes = st.session_state.get(f"order_pdf_{order['id']}")
    if pdf_bytes:
        nr = order.get("order_number") or "Auftragsbestaetigung"
        c2.download_button(
            "⬇ Download",
            data=pdf_bytes,
            file_name=f"{nr}.pdf",
            mime="application/pdf",
            key=f"dl_order_pdf_{order['id']}",
            use_container_width=True,
        )
        from core.ui.mail import render_mail_link
        body = (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei senden wir Ihnen unsere Auftragsbestätigung {nr}"
            + (f" zu Ihrer Bestellung {order['customer_reference']}.\n\n"
               if order.get("customer_reference") else " zur Verfügung.\n\n")
            + "Mit freundlichen Grüßen\nWeber Trading & Service"
        )
        render_mail_link(to=None, subject=f"Auftragsbestätigung {nr}", body=body)


# =====================================================================
#  Entry
# =====================================================================

def render() -> None:
    render_header(
        "Aufträge",
        "Verkaufs-Aufträge — Bestätigung, Auslieferung, Abschluss",
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
