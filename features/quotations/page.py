"""Angebote — Liste / Neu / Detail."""

from __future__ import annotations

from datetime import date, timedelta
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
from features.orders.constants import INCOTERMS_2020, TAX_RATE_DEFAULT, TAX_RATE_REVERSE_CHARGE

from . import repo, service
from .constants import (
    DEFAULT_VALIDITY_DAYS,
    QUOTATION_DONE_STATUSES,
    QUOTATION_FLOW,
    QUOTATION_NEXT_ACTION,
    QUOTATION_STATUS_COLORS,
    QUOTATION_STATUS_LABELS,
    QUOTATION_STATUSES,
    QUOTATION_TERMINAL,
)


FREE_ITEM_LABEL = "— freie Position —"
NEW_PARTY_SENTINEL = "__none__"


# =====================================================================
#  KPIs + Tabelle
# =====================================================================

def _kpis(rows: list[dict[str, Any]]) -> None:
    today = date.today()
    open_rows = [r for r in rows if r.get("status") not in QUOTATION_DONE_STATUSES]
    expired = sum(
        1 for r in open_rows
        if (d := parse_date(r.get("valid_until"))) and d < today
    )
    converted_value = sum(
        int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        for r in rows
        if r.get("status") == "converted"
    )
    open_value = sum(
        int(r.get("total_net_cents") or 0) + int(r.get("tax_total_cents") or 0)
        for r in open_rows
    )

    render_kpis([
        ("Offene Angebote", len(open_rows)),
        ("Ablauf droht", expired),
        ("Konvertiert (Σ)", cents_to_eur(converted_value) or "0,00 €"),
        ("Offen (Σ)", cents_to_eur(open_value) or "0,00 €"),
    ])


def _table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_filter(
            label="Keine Angebote mit diesen Filtern.",
            reset_keys=["quotations_list_statuses", "quotations_list_search"],
        )
        return
    today = date.today()
    data: list[dict[str, Any]] = []
    ids: list[str] = []
    for r in rows:
        c = r.get("customer") or {}
        valid = parse_date(r.get("valid_until"))
        urgency = ""
        if valid and r.get("status") not in QUOTATION_DONE_STATUSES:
            delta = (valid - today).days
            if delta < 0:
                urgency = f"⚠️ {-delta} d abgelaufen"
            elif delta == 0:
                urgency = "🔥 läuft heute aus"
            elif delta <= 7:
                urgency = f"in {delta} Tagen"
        ids.append(r["id"])
        data.append({
            "Nr.": r.get("quotation_number") or "",
            "Kunde": c.get("short_name") or c.get("legal_name") or "—",
            "Datum": format_date(r.get("quoted_at")),
            "Gültig bis": format_date(r.get("valid_until")),
            "Status": QUOTATION_STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
            "Ihre Anfrage-Nr.": r.get("customer_reference") or "",
            "Netto": cents_to_eur(r.get("total_net_cents")),
            "Hinweis": urgency,
        })
    df = pd.DataFrame(data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="quotations_list_table",
        column_config={
            "Nr.": st.column_config.TextColumn(width="small"),
            "Datum": st.column_config.TextColumn(width="small"),
            "Gültig bis": st.column_config.TextColumn(width="small"),
            "Netto": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="small"),
        },
    )
    sel_indices = sel.get("selection", {}).get("rows", [])
    if sel_indices:
        st.query_params["id"] = ids[sel_indices[0]]
        st.rerun()


# =====================================================================
#  Tab 1 — Liste
# =====================================================================

def _render_list_tab() -> None:
    default_open = [s for s in QUOTATION_STATUSES if s not in QUOTATION_DONE_STATUSES]
    if "quotations_list_statuses" not in st.session_state:
        st.session_state["quotations_list_statuses"] = default_open
    statuses = st.pills(
        "Status",
        QUOTATION_STATUSES,
        selection_mode="multi",
        format_func=lambda v: QUOTATION_STATUS_LABELS.get(v, v),
        key="quotations_list_statuses",
    )
    search = st.text_input(
        "Suche (Nr., Anfrage-Nr., Notiz)", "", key="quotations_list_search"
    )

    try:
        rows = repo.list_quotations(
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:
        st.error(f"Konnte Angebote nicht laden: {exc}")
        return

    _kpis(rows)
    _table(rows)
    st.caption(f"{len(rows)} Angebote geladen.")


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
    st.subheader("Neues Angebot")
    st.caption("Pflicht: Kunde + Datum. Items + Konditionen werden im Detail erfasst.")

    parties = delivery_repo.list_parties(party_type="customer")
    party_choices = {NEW_PARTY_SENTINEL: "— wählen —"}
    for p in parties:
        party_choices[p["id"]] = p.get("short_name") or p["legal_name"]

    party_id = st.selectbox(
        "Kunde",
        list(party_choices.keys()),
        format_func=lambda v: party_choices[v],
        key="new_quotation_party",
    )

    with st.expander("➕ Neuen Kunden schnell anlegen"):
        new_name = st.text_input("Firmenname", key="new_quotation_party_name")
        if st.button("Anlegen", key="new_quotation_party_submit"):
            if not new_name.strip():
                st.warning("Firmenname darf nicht leer sein.")
            else:
                _create_party_quick(new_name)
                st.success(f"'{new_name}' angelegt — bitte oben auswählen.")
                st.rerun()

    real_party_id = party_id if party_id != NEW_PARTY_SENTINEL else None
    shipping_addr_id = render_address_picker(
        real_party_id, "new_quotation_ship", "Lieferadresse (optional)",
        kinds=["shipping", "registered"],
    )
    billing_addr_id = render_address_picker(
        real_party_id, "new_quotation_bill", "Rechnungsadresse (optional)",
        kinds=["billing", "registered"],
    )

    with st.form("create_quotation", clear_on_submit=True):
        c1, c2 = st.columns(2)
        quoted_at = c1.date_input("Angebotsdatum", value=date.today(), key="new_quotation_quoted_at")
        valid_until = c2.date_input(
            "Gültig bis",
            value=date.today() + timedelta(days=DEFAULT_VALIDITY_DAYS),
            key="new_quotation_valid_until",
        )

        c3, c4 = st.columns(2)
        customer_reference = c3.text_input("Anfrage-Nr.", key="new_quotation_ref")
        payment_terms = c4.number_input(
            "Zahlungsziel (Tage)", min_value=0, max_value=180, value=14, step=1,
            key="new_quotation_payment_terms",
        )

        c5, c6 = st.columns(2)
        incoterms = c5.selectbox(
            "Incoterms",
            ["—"] + INCOTERMS_2020,
            format_func=lambda v: "— wählen —" if v == "—" else v,
            key="new_quotation_incoterms",
        )
        incoterms_place = c6.text_input("Incoterms-Ort", key="new_quotation_incoterms_place")

        notes = st.text_area("Notizen / Anschreiben", key="new_quotation_notes", height=80)

        submit = st.form_submit_button("Angebot anlegen", type="primary")

        if submit:
            if party_id == NEW_PARTY_SENTINEL:
                st.error("Bitte zuerst einen Kunden wählen.")
                st.stop()
            data = {
                "customer_id": party_id,
                "quoted_at": quoted_at,
                "valid_until": valid_until,
                "customer_reference": customer_reference,
                "payment_terms_days": payment_terms,
                "incoterms": incoterms if incoterms != "—" else None,
                "incoterms_place": incoterms_place,
                "shipping_address_id": shipping_addr_id,
                "billing_address_id": billing_addr_id,
                "notes": notes,
            }
            try:
                new_id = service.create_quotation(data)
                st.success("✓ Angebot angelegt.")
                st.query_params["id"] = new_id
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler: {exc}")


# =====================================================================
#  Detail-Ansicht
# =====================================================================

def _render_detail(qid: str) -> None:
    q = repo.get_quotation(qid)
    if not q:
        st.error("Angebot nicht gefunden.")
        if st.button("← Zurück zur Liste"):
            st.query_params.clear()
            st.rerun()
        return

    customer = q.get("customer") or {}
    items = repo.list_quotation_items(qid)

    # Header-Zeile
    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.subheader(f"{q['quotation_number']} — {customer.get('legal_name') or '—'}")
        render_status_pill(
            q.get("status") or "draft",
            QUOTATION_STATUS_LABELS,
            QUOTATION_STATUS_COLORS,
        )
    with top_r:
        if st.button("← Liste", use_container_width=True, key="back_to_list"):
            st.query_params.clear()
            st.rerun()

    # Stepper
    render_status_stepper(
        q.get("status") or "draft",
        QUOTATION_FLOW,
        QUOTATION_STATUS_LABELS,
        QUOTATION_TERMINAL,
    )

    # Convert-Banner
    converted = q.get("converted_to_order")
    if converted:
        st.info(
            f"✅ Konvertiert in Auftrag **{converted.get('order_number')}** "
            f"(Status: {converted.get('status')})."
        )

    # Status-Aktionen
    next_action = QUOTATION_NEXT_ACTION.get(q.get("status") or "")
    cols = st.columns(4)
    if next_action:
        target_status, label = next_action
        if cols[0].button(label, type="primary", use_container_width=True, key=f"next_{qid}"):
            try:
                if target_status == "converted":
                    new_order_id = service.convert_to_order(qid)
                    st.toast(f"✓ Auftrag erstellt: {new_order_id[:8]}…", icon="✅")
                else:
                    service.update_status(qid, target_status)
                    st.toast(f"✓ Status: {QUOTATION_STATUS_LABELS.get(target_status)}", icon="✅")
                    # Auto-Persist on Lock: insb. beim Senden GoBD-Archiv anlegen
                    if target_status in {"sent", "accepted", "rejected", "expired"}:
                        try:
                            from lib.beleg_generator import render_angebot_pdf
                            from lib.pdf_storage import persist_after_lock
                            fresh = repo.get_quotation(qid)
                            fresh_items = repo.list_quotation_items(qid)
                            if fresh and fresh_items:
                                pdf_bytes = render_angebot_pdf(
                                    fresh,
                                    fresh_items,
                                    hide_totals=bool(fresh.get("hide_totals_in_pdf")),
                                )
                                persist_after_lock(
                                    table="quotations",
                                    doc_id=qid,
                                    beleg_type="quotation",
                                    beleg_number=fresh["quotation_number"],
                                    pdf_bytes=pdf_bytes,
                                )
                        except Exception as exc:
                            st.warning(f"Status gesetzt, aber PDF-Archivierung fehlgeschlagen: {exc}")
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler: {exc}")

    if q.get("status") in {"sent", "accepted", "expired"}:
        if cols[1].button("❌ Ablehnen", use_container_width=True, key=f"reject_{qid}"):
            service.reject_quotation(qid)
            st.rerun()

    # PDF-Download — Toggle wird unterhalb gerendert, Button bleibt in cols[2]
    has_persisted = bool(q.get("pdf_storage_path"))
    pdf_button_label = (
        "⬇ Angebot-PDF laden (Archiv)" if has_persisted else "📄 PDF erzeugen"
    )
    pdf_clicked = cols[2].button(pdf_button_label, use_container_width=True, key=f"pdf_{qid}")

    if cols[3].button("🗑 Löschen", use_container_width=True, key=f"del_{qid}"):
        if q.get("status") not in {"draft", "cancelled"}:
            st.error("Nur Entwürfe oder stornierte Angebote dürfen gelöscht werden.")
        else:
            supabase().table("quotations").delete().eq("id", qid).execute()
            st.toast("Angebot gelöscht.", icon="🗑")
            st.query_params.clear()
            st.rerun()

    # PDF-Optionen + Erzeugung
    pdf_c1, pdf_c2 = st.columns([1, 2])
    default_hide = bool(q.get("hide_totals_in_pdf"))
    hide_totals = pdf_c1.checkbox(
        "Quote-Variante (ohne Gesamtsumme)",
        value=default_hide,
        key=f"hide_totals_{qid}",
        help="Zeigt nur Pos · Artikel · Menge · Preis. Keine Rabatt-/USt-Spalten, keine Summen.",
    )
    toggle_changed = hide_totals != default_hide
    if toggle_changed:
        try:
            supabase().table("quotations").update(
                {"hide_totals_in_pdf": hide_totals}
            ).eq("id", qid).execute()
        except Exception as exc:
            pdf_c1.warning(f"Konnte Einstellung nicht persistieren: {exc}")

    # Storage-First-Caption
    if has_persisted and not toggle_changed:
        pdf_c1.caption("📑 Festgeschriebenes PDF aus Archiv (byte-stable, GoBD).")

    if pdf_clicked:
        if not items:
            st.error("Keine Positionen erfasst.")
        else:
            try:
                from lib.beleg_generator import render_angebot_pdf
                from lib.pdf_storage import render_or_fetch
                # Toggle wechselt → altes PDF ignorieren und überschreiben
                doc_for_fetch = (
                    {**q, "pdf_storage_path": None} if toggle_changed else q
                )
                persist_pdf = q.get("status") in {
                    "sent", "accepted", "rejected", "converted", "expired",
                }
                pdf_bytes, _ = render_or_fetch(
                    table="quotations",
                    doc=doc_for_fetch,
                    beleg_type="quotation",
                    beleg_number=q.get("quotation_number") or qid,
                    render_fn=lambda: render_angebot_pdf(q, items, hide_totals=hide_totals),
                    persist=persist_pdf,
                )
                st.session_state[f"pdf_bytes_{qid}"] = pdf_bytes
            except Exception as exc:
                st.error(f"PDF-Fehler: {exc}")

    if st.session_state.get(f"pdf_bytes_{qid}"):
        suffix = "-Quote" if hide_totals else ""
        pdf_c2.download_button(
            label="⬇ Download Angebot.pdf",
            data=st.session_state[f"pdf_bytes_{qid}"],
            file_name=f"{q['quotation_number']}{suffix}.pdf",
            mime="application/pdf",
            key=f"dl_{qid}",
            type="primary",
            use_container_width=True,
        )

    st.divider()

    # Header-Felder editieren
    is_locked = q.get("status") in QUOTATION_TERMINAL
    with st.expander("📝 Kopfdaten bearbeiten", expanded=False):
        c1, c2 = st.columns(2)
        new_quoted_at = c1.date_input(
            "Angebotsdatum",
            value=parse_date(q.get("quoted_at")) or date.today(),
            key=f"edit_quoted_{qid}",
            disabled=is_locked,
        )
        new_valid_until = c2.date_input(
            "Gültig bis",
            value=parse_date(q.get("valid_until")) or date.today() + timedelta(days=30),
            key=f"edit_valid_{qid}",
            disabled=is_locked,
        )
        new_ref = c1.text_input(
            "Anfrage-Nr.", value=q.get("customer_reference") or "",
            key=f"edit_ref_{qid}", disabled=is_locked,
        )
        new_terms = c2.number_input(
            "Zahlungsziel (Tage)",
            min_value=0, max_value=180,
            value=int(q.get("payment_terms_days") or 14),
            key=f"edit_terms_{qid}", disabled=is_locked,
        )
        new_notes = st.text_area(
            "Notizen", value=q.get("notes") or "",
            key=f"edit_notes_{qid}", disabled=is_locked, height=80,
        )
        if st.button("Speichern", key=f"save_{qid}", disabled=is_locked):
            try:
                service.update_quotation(qid, {
                    "quoted_at": new_quoted_at,
                    "valid_until": new_valid_until,
                    "customer_reference": new_ref,
                    "payment_terms_days": new_terms,
                    "notes": new_notes,
                })
                st.toast("Gespeichert.", icon="✅")
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler: {exc}")

    # Items-Editor
    st.markdown("### Positionen")
    _render_items_editor(qid, items, is_locked)

    # Summen
    st.markdown("### Summen")
    s1, s2, s3 = st.columns(3)
    s1.metric("Netto", cents_to_eur(q.get("total_net_cents")) or "0,00 €")
    s2.metric("USt", cents_to_eur(q.get("tax_total_cents")) or "0,00 €")
    s3.metric(
        "Brutto",
        cents_to_eur((q.get("total_net_cents") or 0) + (q.get("tax_total_cents") or 0)) or "0,00 €",
    )

    st.divider()
    from core.ui.mail_modal import render_mail_section
    render_mail_section(
        beleg_type="quotation",
        beleg_id=qid,
        beleg_number=q.get("quotation_number") or qid,
        party_id=q.get("customer_id"),
        pdf_storage_path=q.get("pdf_storage_path"),
        template_ctx={
            "valid_until": format_date(q.get("valid_until")) or "",
            "customer_reference": q.get("customer_reference") or "",
        },
        is_locked=q.get("status") in {"sent", "accepted", "rejected", "expired", "converted"},
    )


def _render_items_editor(qid: str, items: list[dict[str, Any]], is_locked: bool) -> None:
    """Items über st.data_editor — vollständige Replace beim Speichern."""
    art_rows = (
        supabase()
        .table("articles")
        .select("id, sku, title_de, unit, default_price_cents")
        .order("sku")
        .execute()
        .data
    )
    art_lookup = {a["id"]: a for a in art_rows}
    sku_to_id = {a["sku"]: a["id"] for a in art_rows if a.get("sku")}

    rows: list[dict[str, Any]] = []
    for it in items:
        a = it.get("articles") or {}
        if it.get("expected_delivery_date"):
            delivery_str = format_date(it["expected_delivery_date"])
        else:
            delivery_str = it.get("delivery_lead_time_text") or ""
        rows.append({
            "Pos": it.get("pos_nr") or "",
            "SKU": a.get("sku") or "",
            "Bezeichnung": it.get("description_override") or a.get("title_de") or "",
            "Menge": float(it.get("qty") or 0),
            "Einheit": it.get("unit") or a.get("unit") or "Stk",
            "Preis €": (int(it.get("unit_price_cents") or 0) / 100.0),
            "Rabatt %": float(it.get("discount_pct") or 0),
            "USt %": float(it.get("tax_rate") or TAX_RATE_DEFAULT),
            "Liefertermin": delivery_str,
        })
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        "Pos", "SKU", "Bezeichnung", "Menge", "Einheit", "Preis €", "Rabatt %", "USt %", "Liefertermin",
    ])

    edited = st.data_editor(
        df,
        num_rows="dynamic" if not is_locked else "fixed",
        disabled=is_locked,
        use_container_width=True,
        hide_index=True,
        key=f"items_editor_{qid}",
        column_config={
            "Pos": st.column_config.NumberColumn(width="small", step=1),
            "SKU": st.column_config.TextColumn(width="medium"),
            "Bezeichnung": st.column_config.TextColumn(width="large"),
            "Menge": st.column_config.NumberColumn(width="small", step=1.0, format="%.2f"),
            "Einheit": st.column_config.TextColumn(width="small"),
            "Preis €": st.column_config.NumberColumn(width="small", step=0.01, format="%.2f"),
            "Rabatt %": st.column_config.NumberColumn(width="small", step=0.1, format="%.1f"),
            "USt %": st.column_config.NumberColumn(width="small", step=1.0, format="%.0f"),
            "Liefertermin": st.column_config.TextColumn(
                width="small",
                help='Datum (TT.MM.JJJJ) oder Freitext wie "6-8 Wochen"',
            ),
        },
    )

    if not is_locked:
        if st.button("💾 Positionen speichern", type="primary", key=f"save_items_{qid}"):
            new_items: list[dict[str, Any]] = []
            for i, r in edited.iterrows():
                if not r.get("Bezeichnung") and not r.get("SKU"):
                    continue
                qty = float(r.get("Menge") or 0)
                if qty <= 0:
                    continue
                price_cents = eur_to_cents(r.get("Preis €")) or 0
                disc_pct = float(r.get("Rabatt %") or 0)
                tax_rate = float(r.get("USt %") or 0)
                gross_line = qty * price_cents
                disc_cents = gross_line * disc_pct / 100.0
                net_line = gross_line - disc_cents
                tax_line = net_line * tax_rate / 100.0
                article_id = sku_to_id.get(str(r.get("SKU") or "").strip())
                # Liefertermin: Datum oder Freitext
                delivery_raw = (str(r.get("Liefertermin") or "")).strip()
                expected_date = None
                lead_text = None
                if delivery_raw:
                    pd_date = parse_date(delivery_raw)
                    if pd_date:
                        expected_date = pd_date.isoformat()
                    else:
                        lead_text = delivery_raw
                new_items.append({
                    "pos_nr": int(r.get("Pos") or i + 1),
                    "article_id": article_id,
                    "description_override": r.get("Bezeichnung") if not article_id else None,
                    "qty": qty,
                    "unit": r.get("Einheit") or "Stk",
                    "unit_price_cents": price_cents,
                    "line_total_cents": int(round(net_line)),
                    "tax_rate": tax_rate,
                    "tax_amount_cents": int(round(tax_line)),
                    "discount_pct": disc_pct,
                    "expected_delivery_date": expected_date,
                    "delivery_lead_time_text": lead_text,
                })
            try:
                service.replace_items(qid, new_items)
                st.toast(f"{len(new_items)} Positionen gespeichert.", icon="✅")
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler: {exc}")


# =====================================================================
#  Entry
# =====================================================================

def render() -> None:
    render_header(
        "Angebote",
        "Quotations — Anlegen · Versenden · In Auftrag konvertieren",
    )

    qid = st.query_params.get("id")
    if qid:
        _render_detail(qid)
        render_footer()
        return

    tab_list, tab_new = st.tabs(["📋 Liste", "➕ Neu anlegen"])
    with tab_list:
        _render_list_tab()
    with tab_new:
        _render_create_tab()

    render_footer()
