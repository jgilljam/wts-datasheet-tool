"""Rechnungen — Liste / Anlegen / Detail mit Storno-Workflow."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.ui.address_picker import render_address_picker
from core.ui.empty import render_empty_data, render_empty_filter
from core.ui.kpi import render_kpis
from core.ui.status import render_status_pill, render_status_stepper
from core.utils import cents_to_eur, eur_to_cents, format_date, parse_date

from features.deliveries import repo as delivery_repo
from features.orders import repo as order_repo

from . import repo, service
from .constants import (
    CANCELLATION_REASONS,
    INCOTERMS_2020,
    INVOICE_DONE_STATUSES,
    INVOICE_FLOW,
    INVOICE_LOCKED_STATUSES,
    INVOICE_NEXT_ACTION,
    INVOICE_STATUS_COLORS,
    INVOICE_STATUS_LABELS,
    INVOICE_STATUSES,
    INVOICE_TERMINAL,
    TAX_RATE_DEFAULT,
    TAX_RATE_REVERSE_CHARGE,
)


FREE_ITEM_LABEL = "— freie Position —"
NEW_PARTY_SENTINEL = "__none__"


# =====================================================================
#  Tab 1 — Liste
# =====================================================================

def _kpis(rows: list[dict[str, Any]]) -> None:
    today = date.today()

    open_unpaid = [
        r for r in rows
        if r.get("status") in ("issued", "partially_paid", "overdue")
    ]
    overdue = sum(
        1 for r in open_unpaid
        if (d := parse_date(r.get("due_date"))) and d < today
    )

    # Ausstehender Betrag (Brutto - bereits gezahlt) summieren
    open_amount_cents = 0
    for r in open_unpaid:
        brutto = int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        paid = int(r.get("paid_amount_cents") or 0)
        open_amount_cents += max(0, brutto - paid)

    # Umsatz Monat (alle issued/paid Rechnungen ohne Stornobelege)
    month_start = today.replace(day=1)
    month_revenue_cents = sum(
        int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        for r in rows
        if (d := parse_date(r.get("issued_at"))) and d >= month_start
        and r.get("status") in ("issued", "partially_paid", "paid")
        and not r.get("reverses_id")  # Stornobelege ausschließen
    )

    render_kpis([
        ("Offene Rechnungen", len(open_unpaid)),
        ("Überfällig", overdue),
        ("Offener Betrag", cents_to_eur(open_amount_cents) or "0,00 €"),
        ("Umsatz Monat", cents_to_eur(month_revenue_cents) or "0,00 €"),
    ])


def _table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_filter(
            label="Keine Rechnungen mit diesen Filtern.",
            reset_keys=["invoices_list_statuses", "invoices_list_search"],
            extra_caption="Tipp: Rechnungen entstehen am einfachsten aus einem Auftrag (Detail → Rechnung erstellen).",
        )
        return
    today = date.today()
    data: list[dict[str, Any]] = []
    ids: list[str] = []
    for r in rows:
        c = r.get("customer") or {}
        order = r.get("related_order") or {}
        reverses = r.get("reverses") or {}
        due = parse_date(r.get("due_date"))
        urgency = ""
        if due and r.get("status") in ("issued", "partially_paid", "overdue"):
            delta = (due - today).days
            if delta < 0:
                urgency = f"⚠️ {-delta} d überfällig"
            elif delta == 0:
                urgency = "🔥 heute fällig"
            elif delta <= 7:
                urgency = f"in {delta} Tagen"

        nr_display = r.get("invoice_number") or "—"
        if reverses.get("invoice_number"):
            nr_display = f"{nr_display} (Storno zu {reverses['invoice_number']})"

        brutto = int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        paid = int(r.get("paid_amount_cents") or 0)
        offen = max(0, brutto - paid)

        ids.append(r["id"])
        data.append({
            "Nr.": nr_display,
            "Kunde": c.get("short_name") or c.get("legal_name") or "—",
            "Datum": format_date(r.get("issued_at")),
            "Fällig": format_date(r.get("due_date")),
            "Status": INVOICE_STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
            "Brutto": cents_to_eur(brutto),
            "Offen": cents_to_eur(offen) if offen else "—",
            "Auftrag": order.get("order_number") or "",
            "Dringlichkeit": urgency,
        })
    df = pd.DataFrame(data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="invoices_list_table",
        column_config={
            "Nr.": st.column_config.TextColumn(width="medium"),
            "Datum": st.column_config.TextColumn(width="small"),
            "Fällig": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="medium"),
            "Brutto": st.column_config.TextColumn(width="small"),
            "Offen": st.column_config.TextColumn(width="small"),
        },
    )
    selected_indices = (sel.selection.rows if sel and sel.selection else []) or []
    if selected_indices:
        _render_bulk_actions([(ids[i], rows[i]) for i in selected_indices])


def _render_bulk_actions(selected: list[tuple[str, dict[str, Any]]]) -> None:
    """Bulk-Aktionen für markierte Rechnungen — Zahlung erfassen für alle offenen."""
    n = len(selected)
    payable = [
        (iid, inv) for iid, inv in selected
        if inv.get("status") in ("issued", "partially_paid", "overdue")
    ]
    box = st.container(border=True)
    with box:
        st.markdown(f"**{n} Rechnung(en) markiert**")
        c1, c2 = st.columns([3, 2])

        # Bulk: alle offenen als "voll bezahlt" markieren (record_payment mit Restbetrag)
        total_open = 0
        for _, inv in payable:
            brutto = int(inv.get("total_net_cents") or 0) + int(inv.get("tax_total_cents") or 0)
            paid = int(inv.get("paid_amount_cents") or 0)
            total_open += max(0, brutto - paid)

        if c1.button(
            f"✓ {len(payable)} Rechnung(en) als bezahlt markieren ({cents_to_eur(total_open)})",
            disabled=not payable,
            key="bulk_inv_paid",
            use_container_width=True,
            type="primary",
        ):
            errors: list[str] = []
            for iid, inv in payable:
                brutto = int(inv.get("total_net_cents") or 0) + int(inv.get("tax_total_cents") or 0)
                paid = int(inv.get("paid_amount_cents") or 0)
                rest = max(0, brutto - paid)
                if rest <= 0:
                    continue
                try:
                    service.record_payment(iid, rest, paid_at=date.today())
                except Exception as exc:
                    errors.append(f"{inv.get('invoice_number') or iid[:8]}: {exc}")
            if errors:
                st.error("Fehler: " + " · ".join(errors))
            else:
                st.toast(
                    f"{len(payable)} Rechnung(en) als bezahlt verbucht ({cents_to_eur(total_open)})",
                    icon="💶",
                )
            st.rerun()

        # Bulk: Mailto-Liste mit Zahlungserinnerung für überfällige
        overdue = [
            (iid, inv) for iid, inv in selected
            if inv.get("status") == "overdue"
        ]
        if overdue:
            c2.caption(f"📧 {len(overdue)} überfällig — Mahnungen einzeln im Detail.")
        else:
            c2.caption(" ")


def _render_list_tab() -> None:
    # Automatik: einmal pro Page-Load fällige Rechnungen auf 'overdue' setzen
    if not st.session_state.get("__overdue_check_done"):
        try:
            n = service.auto_mark_overdue()
            if n:
                st.toast(f"{n} Rechnung(en) wegen Fälligkeit auf „Überfällig“ gesetzt", icon="⚠️")
        except Exception:
            pass
        st.session_state["__overdue_check_done"] = True

    default_open = [s for s in INVOICE_STATUSES if s not in INVOICE_DONE_STATUSES]
    if "invoices_list_statuses" not in st.session_state:
        st.session_state["invoices_list_statuses"] = default_open
    statuses = st.pills(
        "Status",
        INVOICE_STATUSES,
        selection_mode="multi",
        format_func=lambda v: INVOICE_STATUS_LABELS.get(v, v),
        key="invoices_list_statuses",
    )
    search = st.text_input("Suche (Nr., Best.-Nr., Notiz)", "", key="invoices_list_search")

    try:
        rows = repo.list_invoices(
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:
        st.error(f"Konnte Rechnungen nicht laden: {exc}")
        return

    _kpis(rows)
    _table(rows)
    st.caption(f"{len(rows)} Rechnungen geladen.")


# =====================================================================
#  Tab 2 — Neu anlegen (manueller Entwurf — Hauptweg ist „aus Auftrag")
# =====================================================================

def _render_create_tab() -> None:
    st.subheader("Neue Rechnung")
    st.caption(
        "💡 Empfohlener Weg: **Aufträge → Detail → 📑 Rechnung erstellen**. "
        "Dieser Tab ist nur für freie Rechnungen ohne Auftragsbezug."
    )

    parties = delivery_repo.list_parties(party_type="customer")
    party_choices = {NEW_PARTY_SENTINEL: "— wählen —"}
    for p in parties:
        party_choices[p["id"]] = p.get("short_name") or p["legal_name"]

    party_id = st.selectbox(
        "Kunde",
        list(party_choices.keys()),
        format_func=lambda v: party_choices[v],
        key="new_invoice_party",
    )

    real_party_id = party_id if party_id != NEW_PARTY_SENTINEL else None
    shipping_addr_id = render_address_picker(
        real_party_id, "new_invoice_ship", "Lieferadresse",
        kinds=["shipping", "registered"],
    )
    billing_addr_id = render_address_picker(
        real_party_id, "new_invoice_bill", "Rechnungsadresse",
        kinds=["billing", "registered"],
    )

    with st.form("create_invoice", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        issued_at = c1.date_input("Rechnungsdatum", value=date.today(), key="new_inv_issued")
        service_date = c2.date_input(
            "Leistungsdatum (Pflicht)", value=date.today(), key="new_inv_service",
            help="Datum der Lieferung/Leistung — UStG §14 Pflichtangabe.",
        )
        due_date = c3.date_input(
            "Zahlbar bis",
            value=date.today() + timedelta(days=14),
            key="new_inv_due",
        )

        c4, c5 = st.columns(2)
        customer_reference = c4.text_input("Kunden-Bestell-Nr.", key="new_inv_ref")
        payment_terms = c5.number_input(
            "Zahlungsziel (Tage)", min_value=0, max_value=180, value=14, step=1,
            key="new_inv_payment_terms",
        )

        c6, c7 = st.columns(2)
        incoterms = c6.selectbox(
            "Incoterms",
            ["—"] + INCOTERMS_2020,
            format_func=lambda v: "— wählen —" if v == "—" else v,
            key="new_inv_incoterms",
        )
        incoterms_place = c7.text_input("Incoterms-Ort", key="new_inv_incoterms_place")

        notes = st.text_area("Notizen (sichtbar auf Rechnung)", key="new_inv_notes", height=80)
        internal_notes = st.text_area("Interne Notizen", key="new_inv_internal_notes", height=80)

        submitted = st.form_submit_button(
            "📄 Rechnungsentwurf anlegen", type="primary", use_container_width=True
        )

        if submitted:
            if party_id == NEW_PARTY_SENTINEL:
                st.error("Bitte einen Kunden wählen.")
                return

            customer = next((p for p in parties if p["id"] == party_id), {})
            rev_charge = bool(customer.get("is_reverse_charge_eligible"))

            payload: dict[str, Any] = {
                "customer_id": party_id,
                "status": "draft",
                "issued_at": issued_at,
                "service_date": service_date,
                "due_date": due_date,
                "payment_terms_days": int(payment_terms) if payment_terms else None,
                "is_reverse_charge": rev_charge,
            }
            if shipping_addr_id:
                payload["shipping_address_id"] = shipping_addr_id
            if billing_addr_id:
                payload["billing_address_id"] = billing_addr_id
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

            try:
                new_id = service.create_invoice(payload)
            except Exception as exc:
                st.error(f"Konnte Rechnung nicht anlegen: {exc}")
                return

            st.success(
                f"Rechnungsentwurf angelegt (`{new_id[:8]}…`). "
                "Wechsle zum Tab **Detail**, um Positionen zu erfassen."
            )


# =====================================================================
#  Tab 3 — Detail
# =====================================================================

def _render_header_card(inv: dict[str, Any]) -> None:
    customer = inv.get("customer") or {}
    related_order = inv.get("related_order") or {}
    reverses = inv.get("reverses") or {}
    reversed_by = inv.get("reversed_by") or {}

    if reverses.get("invoice_number"):
        st.error(
            f"🔄 **Stornorechnung** zu Rechnung **{reverses['invoice_number']}** "
            f"(vom {format_date(reverses.get('issued_at'))})."
        )
    if reversed_by.get("invoice_number"):
        st.warning(
            f"❌ **Storniert** durch Stornorechnung **{reversed_by['invoice_number']}** "
            f"vom {format_date(reversed_by.get('issued_at'))}. "
            f"Grund: {inv.get('cancellation_reason') or '—'}"
        )

    if inv.get("is_reverse_charge"):
        st.info(
            "💶 **Reverse-Charge** — Steuerschuldnerschaft des Leistungsempfängers "
            "nach §13b UStG. Nettorechnung (0% USt)."
        )

    pill = render_status_pill(
        inv.get("status") or "draft",
        INVOICE_STATUS_LABELS,
        INVOICE_STATUS_COLORS,
    )
    title = inv.get("invoice_number") or "📝 Entwurf (noch keine Nr.)"
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
        f"<h3 style='margin:0;'>📄 {title}</h3>"
        f"<div>{pill}</div></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Kunde**\n\n{customer.get('legal_name') or '—'}")
    c2.markdown(f"**Rechnungsdatum**\n\n{format_date(inv.get('issued_at')) or '—'}")
    c3.markdown(f"**Leistungsdatum**\n\n{format_date(inv.get('service_date')) or '—'}")

    c4, c5, c6 = st.columns(3)
    c4.markdown(f"**Zahlbar bis**\n\n{format_date(inv.get('due_date')) or '—'}")
    c5.markdown(f"**Zahlungsziel**\n\n{inv.get('payment_terms_days') or '—'} Tage")
    c6.markdown(f"**Auftrag**\n\n{related_order.get('order_number') or '—'}")

    # Zahlungsstatus
    brutto = int(inv.get("total_net_cents") or 0) + int(inv.get("tax_total_cents") or 0)
    paid = int(inv.get("paid_amount_cents") or 0)
    offen = brutto - paid
    if brutto > 0:
        c7, c8, c9 = st.columns(3)
        c7.metric("Brutto", cents_to_eur(brutto))
        c8.metric("Bezahlt", cents_to_eur(paid))
        c9.metric("Offen", cents_to_eur(max(0, offen)))

    if inv.get("notes"):
        st.info(f"📝 {inv['notes']}")


def _render_action_buttons(inv: dict[str, Any]) -> None:
    invoice_id = inv["id"]
    cur = inv.get("status") or "draft"

    if cur in INVOICE_TERMINAL or cur == "paid":
        return

    next_action = INVOICE_NEXT_ACTION.get(cur)

    cols = st.columns([3, 2, 2])

    if next_action and cur == "draft":
        # Festschreiben — eigener Button mit Validierung
        primary = cols[0].button(
            "✓ Festschreiben & ausstellen",
            key=f"inv_issue_{invoice_id}",
            type="primary",
            use_container_width=True,
            help="Vergibt eine Rechnungsnummer und sperrt den Beleg (GoBD).",
        )
        if primary:
            try:
                new_nr = service.issue_invoice(invoice_id)
            except Exception as exc:
                st.error(f"Festschreiben fehlgeschlagen: {exc}")
                return
            # PDF byte-stable im Storage festschreiben (GoBD)
            try:
                fresh = repo.get_invoice(invoice_id)
                fresh_items = repo.list_invoice_items(invoice_id)
                from lib.beleg_generator import render_rechnung_pdf
                from lib.pdf_storage import persist_after_lock
                pdf_bytes = render_rechnung_pdf(fresh, fresh_items)
                persist_after_lock(
                    table="invoices",
                    doc_id=invoice_id,
                    beleg_type="invoice",
                    beleg_number=new_nr,
                    pdf_bytes=pdf_bytes,
                )
            except Exception as exc:
                st.warning(f"Rechnung gesperrt, aber PDF-Archivierung fehlgeschlagen: {exc}")
            st.success(f"Rechnung **{new_nr}** ausgestellt + GoBD-gesperrt.")
            st.rerun()
    elif next_action:
        next_status, label = next_action
        primary = cols[0].button(
            label,
            key=f"inv_action_{invoice_id}",
            type="primary",
            use_container_width=True,
        )
        if primary:
            # Bei "Zahlung erfassen" → record_payment
            try:
                brutto = int(inv.get("total_net_cents") or 0) + int(inv.get("tax_total_cents") or 0)
                paid = int(inv.get("paid_amount_cents") or 0)
                offen = max(0, brutto - paid)
                service.record_payment(invoice_id, offen, paid_at=date.today())
            except Exception as exc:
                st.error(f"Zahlung fehlgeschlagen: {exc}")
                return
            st.success("Vollzahlung erfasst.")
            st.rerun()

    # Storno-Button (nur ab issued)
    if cur in ("issued", "partially_paid", "paid", "overdue"):
        if cols[1].button(
            "🔄 Stornorechnung",
            key=f"inv_reverse_btn_{invoice_id}",
            use_container_width=True,
            help="Erzeugt eine Stornorechnung mit eigener Belegnummer (GoBD-konform).",
        ):
            st.session_state[f"inv_reverse_modal_{invoice_id}"] = True

    # Verwerfen (Draft)
    if cur == "draft":
        if cols[2].button(
            "✕ Entwurf verwerfen",
            key=f"inv_cancel_draft_{invoice_id}",
            use_container_width=True,
        ):
            try:
                service.update_status(invoice_id, "cancelled")
            except Exception as exc:
                st.error(f"Verwerfen fehlgeschlagen: {exc}")
                return
            st.warning("Entwurf verworfen.")
            st.rerun()

    # Storno-Modal
    if st.session_state.get(f"inv_reverse_modal_{invoice_id}"):
        _render_reverse_modal(inv)


def _render_reverse_modal(inv: dict[str, Any]) -> None:
    """Inline-Modal für Storno (Streamlit hat keine echten Modals — wir nutzen einen Container)."""
    invoice_id = inv["id"]
    with st.container(border=True):
        st.markdown("### 🔄 Stornorechnung erstellen")
        st.caption(
            f"Rechnung **{inv.get('invoice_number')}** vom "
            f"{format_date(inv.get('issued_at'))} stornieren. "
            "Es wird ein eigener Storno-Beleg mit negativen Beträgen erzeugt. "
            "**Original bleibt unverändert** (GoBD)."
        )

        reason = st.selectbox(
            "Grund (Pflicht)",
            CANCELLATION_REASONS,
            key=f"inv_rev_reason_{invoice_id}",
        )
        if reason == "Sonstiger Grund":
            reason_text = st.text_input(
                "Bitte beschreiben",
                key=f"inv_rev_reason_text_{invoice_id}",
            )
        else:
            reason_text = ""

        rev_date = st.date_input(
            "Storno-Datum",
            value=date.today(),
            key=f"inv_rev_date_{invoice_id}",
        )

        c1, c2 = st.columns(2)
        if c1.button(
            "🔄 Storno-Beleg erzeugen",
            key=f"inv_rev_confirm_{invoice_id}",
            type="primary",
            use_container_width=True,
        ):
            full_reason = (
                f"{reason} — {reason_text.strip()}" if reason == "Sonstiger Grund" and reason_text.strip()
                else reason
            )
            try:
                storno_id = service.reverse_invoice(invoice_id, full_reason, rev_date)
            except Exception as exc:
                st.error(f"Storno fehlgeschlagen: {exc}")
                return
            # Storno-PDF byte-stable archivieren
            try:
                storno = repo.get_invoice(storno_id)
                storno_items = repo.list_invoice_items(storno_id)
                from lib.beleg_generator import render_rechnung_pdf
                from lib.pdf_storage import persist_after_lock
                pdf_bytes = render_rechnung_pdf(storno, storno_items)
                persist_after_lock(
                    table="invoices",
                    doc_id=storno_id,
                    beleg_type="invoice",
                    beleg_number=storno.get("invoice_number") or storno_id,
                    pdf_bytes=pdf_bytes,
                )
            except Exception as exc:
                st.warning(f"Storno erstellt, PDF-Archivierung fehlgeschlagen: {exc}")
            st.session_state.pop(f"inv_reverse_modal_{invoice_id}", None)
            st.success(f"Stornorechnung erzeugt (`{storno_id[:8]}…`). Wechsle zur Liste.")
            st.rerun()
        if c2.button(
            "Abbrechen",
            key=f"inv_rev_cancel_{invoice_id}",
            use_container_width=True,
        ):
            st.session_state.pop(f"inv_reverse_modal_{invoice_id}", None)
            st.rerun()


def _render_items_editor(inv: dict[str, Any]) -> None:
    invoice_id = inv["id"]
    is_locked = (inv.get("status") or "draft") in INVOICE_LOCKED_STATUSES

    items = repo.list_invoice_items(invoice_id)
    articles = delivery_repo.list_articles()
    article_by_label = {
        f"{a['sku']} — {a.get('title_de') or ''}".strip(" —"): a for a in articles
    }
    article_options = [FREE_ITEM_LABEL] + sorted(article_by_label.keys())

    default_tax = TAX_RATE_REVERSE_CHARGE if inv.get("is_reverse_charge") else TAX_RATE_DEFAULT

    rows: list[dict[str, Any]] = []
    for it in items:
        a = it.get("articles") or {}
        if a.get("sku"):
            label = f"{a['sku']} — {a.get('title_de') or ''}".strip(" —")
            if label not in article_by_label:
                article_options.append(label)
        else:
            label = FREE_ITEM_LABEL
        unit_price_eur = float(it.get("unit_price_cents") or 0) / 100.0
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
            f"🔒 **GoBD-gesperrt** (Status: {INVOICE_STATUS_LABELS.get(inv.get('status'), inv.get('status'))}). "
            "Korrekturen erfordern Storno-Beleg."
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
        key=f"invoice_items_{invoice_id}",
    )

    # Live-Summen
    net = 0.0
    tax = 0.0
    for _, row in edited.iterrows():
        qty = float(row.get("Menge") or 0)
        price = float(row.get("Preis €") or 0)
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

    if st.button("💾 Positionen speichern", key=f"save_inv_items_{invoice_id}", type="primary"):
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
            service.replace_items(invoice_id, new_items)
        except Exception as exc:
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        st.success(f"{len(new_items)} Position(en) gespeichert.")
        st.rerun()


def _render_pdf_section(inv: dict[str, Any]) -> None:
    items = repo.list_invoice_items(inv["id"])
    has_items = bool(items)
    is_locked = bool(inv.get("locked_at"))
    has_persisted = bool(inv.get("pdf_storage_path"))

    c1, c2 = st.columns([3, 2])
    if not has_items:
        c1.caption("ℹ️ Keine Positionen erfasst.")
    elif has_persisted:
        c1.caption("📑 Festgeschriebenes PDF aus Archiv (byte-stable, GoBD).")

    primary_label = (
        "⬇ Rechnung-PDF laden (Archiv)"
        if has_persisted
        else "📄 Rechnung-PDF generieren"
    )
    if c1.button(
        primary_label,
        key=f"gen_inv_pdf_{inv['id']}",
        type="primary",
        use_container_width=True,
        disabled=not has_items,
    ):
        try:
            from lib.beleg_generator import render_rechnung_pdf
            from lib.pdf_storage import render_or_fetch
            pdf_bytes, _ = render_or_fetch(
                table="invoices",
                doc=inv,
                beleg_type="invoice",
                beleg_number=inv.get("invoice_number") or inv["id"],
                render_fn=lambda: render_rechnung_pdf(inv, items),
                persist=is_locked,
            )
        except Exception as exc:
            st.error(f"PDF-Generierung fehlgeschlagen: {exc}")
            return
        st.session_state[f"inv_pdf_{inv['id']}"] = pdf_bytes
        if has_persisted:
            st.success(f"PDF geladen ({len(pdf_bytes) // 1024} KB).")
        else:
            st.success(f"PDF generiert ({len(pdf_bytes) // 1024} KB).")

    if c1.button(
        "📋 Proforma-PDF",
        key=f"gen_proforma_pdf_{inv['id']}",
        use_container_width=True,
        disabled=not has_items,
        help="Vorab-Information für Zoll/Anfrage — keine Zahlungsaufforderung. Wird nicht archiviert.",
    ):
        try:
            from lib.beleg_generator import render_proforma_pdf
            pdf_bytes = render_proforma_pdf(inv, items)
        except Exception as exc:
            st.error(f"Proforma-PDF fehlgeschlagen: {exc}")
            return
        st.session_state[f"inv_pdf_{inv['id']}"] = pdf_bytes
        st.success(f"Proforma-PDF generiert ({len(pdf_bytes) // 1024} KB).")

    pdf_bytes = st.session_state.get(f"inv_pdf_{inv['id']}")
    if pdf_bytes:
        nr = inv.get("invoice_number") or "Rechnung_Entwurf"
        c2.download_button(
            "⬇ Download",
            data=pdf_bytes,
            file_name=f"{nr}.pdf",
            mime="application/pdf",
            key=f"dl_inv_pdf_{inv['id']}",
            use_container_width=True,
        )

        # mailto-Link mit vorbefülltem Body
        from core.ui.mail import render_mail_link
        customer = inv.get("customer") or {}
        customer_name = customer.get("legal_name") or "Kunden"
        is_storno = bool(inv.get("reverses_id"))
        subject = (
            f"Stornorechnung {nr}" if is_storno else f"Rechnung {nr}"
        )
        body_lines = [
            f"Sehr geehrte Damen und Herren,",
            "",
            f"anbei senden wir Ihnen unsere {'Stornorechnung' if is_storno else 'Rechnung'} {nr}",
        ]
        if inv.get("customer_reference"):
            body_lines.append(f"zu Ihrer Bestellung {inv['customer_reference']}.")
        else:
            body_lines.append("zur Verfügung.")
        if not is_storno and inv.get("due_date"):
            body_lines.append("")
            body_lines.append(f"Zahlbar bis {format_date(inv['due_date'])}.")
        body_lines.extend([
            "",
            "Mit freundlichen Grüßen",
            "Weber Trading & Service",
        ])
        render_mail_link(
            to=None,  # Empfänger-E-Mail aus parties.contacts wäre Phase K
            subject=subject,
            body="\n".join(body_lines),
        )


def _render_history(inv: dict[str, Any]) -> None:
    events = repo.list_invoice_events(inv["id"], limit=50)
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
                old = INVOICE_STATUS_LABELS.get(payload.get("old_status"), payload.get("old_status") or "?")
                new = INVOICE_STATUS_LABELS.get(payload.get("new_status"), payload.get("new_status") or "?")
                desc = f"{old} → {new}"
            elif etype == "issued":
                desc = f"Rechnungsnr. **{payload.get('invoice_number', '—')}** vergeben + GoBD-gesperrt"
            elif etype == "items_replaced":
                desc = f"{payload.get('count', 0)} Positionen"
            elif etype == "created_from_order":
                desc = f"Erzeugt aus Auftrag **{payload.get('order_number', '—')}** ({payload.get('items', 0)} Pos.)"
            elif etype == "reversed":
                desc = f"Storniert — Grund: {payload.get('reason', '—')}"
            elif etype == "is_storno_for":
                desc = f"Storno-Beleg zu **{payload.get('original_number', '—')}**"
            elif etype == "payment_recorded":
                desc = f"Zahlung **{cents_to_eur(payload.get('amount_cents'))}** → {INVOICE_STATUS_LABELS.get(payload.get('new_status'), '?')}"
            else:
                desc = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "fields")
            st.caption(f"`{at}` · **{etype}** · {actor} · {desc}")


def _render_detail_tab() -> None:
    invoices = repo.list_invoices(limit=500)
    if not invoices:
        render_empty_data(
            title="Noch keine Rechnungen",
            description="Erstelle deine erste Rechnung am einfachsten aus einem Auftrag (Detail → Rechnung erstellen) oder freihändig im Tab „Neu anlegen“.",
            icon="📄",
        )
        return

    options: dict[str, str] = {}
    for inv in invoices:
        c = inv.get("customer") or {}
        cname = c.get("short_name") or c.get("legal_name") or "—"
        status_label = INVOICE_STATUS_LABELS.get(inv.get("status"), inv.get("status") or "")
        nr = inv.get("invoice_number") or f"Entwurf {inv['id'][:8]}"
        options[inv["id"]] = f"{nr} · {cname} · {status_label}"

    selected_id = st.selectbox(
        "Rechnung wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="invoices_detail_select",
    )
    if not selected_id:
        return

    inv = repo.get_invoice(selected_id)
    if not inv:
        st.error("Rechnung nicht gefunden.")
        return

    st.divider()
    _render_header_card(inv)
    st.divider()

    st.markdown("### Status")
    cur = inv.get("status") or "draft"
    render_status_stepper(
        INVOICE_FLOW, cur, INVOICE_STATUS_LABELS, INVOICE_STATUS_COLORS,
        terminal_states=INVOICE_TERMINAL,
    )
    _render_action_buttons(inv)
    st.divider()

    st.markdown("### Positionen")
    _render_items_editor(inv)
    st.divider()

    st.markdown("### Rechnung-PDF")
    _render_pdf_section(inv)
    st.divider()

    from core.ui.mail_modal import render_mail_section
    is_storno = bool(inv.get("reverses_id"))
    render_mail_section(
        beleg_type="invoice",
        beleg_id=inv["id"],
        beleg_number=inv.get("invoice_number") or inv["id"],
        party_id=inv.get("customer_id"),
        pdf_storage_path=inv.get("pdf_storage_path"),
        template_ctx={
            "issued_at": format_date(inv.get("issued_at")) or "",
            "due_date": format_date(inv.get("due_date")) or "",
            "customer_reference": inv.get("customer_reference") or "",
            "is_storno": is_storno,
        },
        is_locked=(inv.get("status") or "draft") in INVOICE_LOCKED_STATUSES,
    )
    st.divider()

    _render_history(inv)


# =====================================================================
#  Entry
# =====================================================================

def render() -> None:
    render_header(
        "Rechnungen",
        "Verkaufsrechnungen — Festschreiben, Zahlung erfassen, Stornieren",
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
