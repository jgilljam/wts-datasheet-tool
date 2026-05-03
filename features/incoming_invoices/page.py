"""Eingangsrechnungen — Upload (OCR), Liste, Detail."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.config import gemini_settings
from core.db import supabase
from core.ui.empty import render_empty_data
from core.ui.kpi import render_kpis
from core.ui.status import render_status_pill, render_status_stepper
from core.utils import cents_to_eur, eur_to_cents, format_date, parse_date

from . import repo, service
from .constants import (
    CONFIDENCE_LABELS,
    INCOMING_DONE_STATUSES,
    INCOMING_STATUS_COLORS,
    INCOMING_STATUS_LABELS,
    INCOMING_STATUSES,
)


# =====================================================================
#  Tab 1 — Upload (OCR)
# =====================================================================

def _render_upload_tab() -> None:
    st.subheader("📥 PDF hochladen — KI extrahiert automatisch")
    st.caption(
        "Lieferanten-Rechnung als PDF hochladen. Gemini liest Lieferant, "
        "Rechnungsnummer, Items und Beträge aus. Du kannst danach noch alles korrigieren."
    )

    uploaded = st.file_uploader(
        "Lieferanten-Rechnung (PDF)",
        type=["pdf"],
        key="incoming_uploader",
        help="Eine Datei pro Upload. Nach dem Parsen kommst du direkt zur Detail-Seite.",
    )
    if not uploaded:
        return

    pdf_bytes = uploaded.read()
    st.info(f"📄 **{uploaded.name}** ({len(pdf_bytes) // 1024} KB)")

    if st.button("🤖 KI parst Rechnung jetzt", type="primary", use_container_width=True):
        with st.spinner("Gemini extrahiert Lieferant, Items und Beträge…"):
            try:
                api_key, model = gemini_settings()
                from lib.incoming_invoice_ocr import parse_invoice_pdf
                parsed = parse_invoice_pdf(pdf_bytes, api_key=api_key, model=model)
            except Exception as exc:
                st.error(f"OCR fehlgeschlagen: {exc}")
                return

        # Konfidenz
        conf = (parsed.confidence or "medium").lower()
        st.success(f"✓ {len(parsed.items)} Position(en) extrahiert. Konfidenz: {CONFIDENCE_LABELS.get(conf, conf)}")

        # Vorschau
        with st.expander("Vorschau extrahierter Daten", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Lieferant**")
                st.write(parsed.supplier_name or "—")
                if parsed.supplier_vat_id:
                    st.caption(f"USt-IdNr.: {parsed.supplier_vat_id}")
                if parsed.supplier_address:
                    st.caption(parsed.supplier_address)
            with c2:
                st.markdown("**Rechnungs-Daten**")
                st.write(f"Nr.: **{parsed.invoice_number}**")
                st.caption(f"Datum: {parsed.invoice_date}")
                if parsed.due_date:
                    st.caption(f"Fällig: {parsed.due_date}")
                if parsed.customer_reference:
                    st.caption(f"Unsere Bestell-Nr.: {parsed.customer_reference}")

            st.markdown("**Beträge**")
            t1, t2, t3 = st.columns(3)
            t1.metric("Netto", f"{parsed.total_net_eur:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
            t2.metric("USt", f"{parsed.tax_total_eur:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
            t3.metric("Brutto", f"{parsed.gross_total_eur:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))

            st.markdown("**Positionen**")
            df = pd.DataFrame([
                {
                    "Pos": it.pos_nr,
                    "SKU": it.sku,
                    "Bezeichnung": it.description,
                    "Menge": it.qty,
                    "Einh.": it.unit,
                    "EK €": it.unit_price_eur,
                    "Rabatt %": it.discount_pct,
                    "Summe €": it.line_total_eur,
                    "USt %": it.tax_rate_pct,
                }
                for it in parsed.items
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

        if st.button("✅ Übernehmen & in DB anlegen", type="primary", use_container_width=True):
            try:
                inv_id = service.create_from_ocr(
                    parsed=parsed,
                    pdf_bytes=pdf_bytes,
                    pdf_filename=uploaded.name,
                    auto_match=True,
                )
                st.toast("✓ Eingangsrechnung angelegt — Lieferant + Items automatisch zugeordnet.", icon="✅")
                st.query_params["id"] = inv_id
                st.rerun()
            except Exception as exc:
                st.error(f"Speichern fehlgeschlagen: {exc}")


# =====================================================================
#  Tab 2 — Liste
# =====================================================================

def _kpis(rows: list[dict[str, Any]]) -> None:
    open_rows = [r for r in rows if r.get("status") not in INCOMING_DONE_STATUSES]
    today = date.today()

    overdue = sum(
        1 for r in open_rows
        if (d := parse_date(r.get("due_date"))) and d < today
    )
    in_review = sum(1 for r in open_rows if r.get("status") in ("received", "in_review"))
    open_value = sum(int(r.get("gross_total_cents") or 0) for r in open_rows)

    render_kpis([
        ("Offen gesamt", len(open_rows)),
        ("In Prüfung", in_review),
        ("Überfällig", overdue),
        ("Volumen offen", cents_to_eur(open_value) or "0,00 €"),
    ])


def _render_list_tab() -> None:
    if "incoming_list_statuses" not in st.session_state:
        st.session_state["incoming_list_statuses"] = [s for s in INCOMING_STATUSES if s not in INCOMING_DONE_STATUSES]
    statuses = st.pills(
        "Status",
        INCOMING_STATUSES,
        selection_mode="multi",
        format_func=lambda v: INCOMING_STATUS_LABELS.get(v, v),
        key="incoming_list_statuses",
    )
    search = st.text_input("Suche (Re-Nr., Bestell-Nr., Notiz)", "", key="incoming_list_search")

    try:
        rows = repo.list_incoming_invoices(
            statuses=statuses or None,
            search=search.strip() or None,
            limit=500,
        )
    except Exception as exc:
        st.error(f"Konnte Eingangsrechnungen nicht laden: {exc}")
        return

    _kpis(rows)

    if not rows:
        render_empty_data(
            label="Noch keine Eingangsrechnungen — lade die erste PDF im Tab nebenan hoch.",
            cta_label="",
        )
        return

    today = date.today()
    data = []
    ids = []
    for r in rows:
        s = r.get("supplier") or {}
        po = r.get("related_po") or {}
        due = parse_date(r.get("due_date"))
        urgency = ""
        if due and r.get("status") not in INCOMING_DONE_STATUSES:
            delta = (due - today).days
            if delta < 0:
                urgency = f"⚠️ {-delta} d überfällig"
            elif delta <= 7:
                urgency = f"in {delta} Tagen"
        ids.append(r["id"])
        conf = r.get("ocr_confidence") or ""
        data.append({
            "Re-Nr.": r.get("supplier_invoice_number") or "?",
            "Lieferant": s.get("short_name") or s.get("legal_name") or "—",
            "Datum": format_date(r.get("invoice_date")),
            "Fällig": format_date(r.get("due_date")),
            "Mahnung": urgency,
            "Brutto": cents_to_eur(r.get("gross_total_cents")),
            "PO": po.get("po_number") or "",
            "Status": INCOMING_STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
            "OCR": CONFIDENCE_LABELS.get(conf, conf),
        })
    df = pd.DataFrame(data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="incoming_list_table",
        column_config={
            "Re-Nr.": st.column_config.TextColumn(width="medium"),
            "Datum": st.column_config.TextColumn(width="small"),
            "Fällig": st.column_config.TextColumn(width="small"),
            "Brutto": st.column_config.TextColumn(width="small"),
            "PO": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="small"),
            "OCR": st.column_config.TextColumn(width="small"),
        },
    )
    sel_indices = sel.get("selection", {}).get("rows", [])
    if sel_indices:
        st.query_params["id"] = ids[sel_indices[0]]
        st.rerun()
    st.caption(f"{len(rows)} Eingangsrechnungen geladen.")


# =====================================================================
#  Detail
# =====================================================================

def _render_detail(inv_id: str) -> None:
    inv = repo.get_incoming_invoice(inv_id)
    if not inv:
        st.error("Eingangsrechnung nicht gefunden.")
        if st.button("← Zurück"):
            st.query_params.clear()
            st.rerun()
        return
    items = repo.list_items(inv_id)
    supplier = inv.get("supplier") or {}
    po = inv.get("related_po") or {}

    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.subheader(f"{inv.get('supplier_invoice_number')} — {supplier.get('legal_name') or '—'}")
        render_status_pill(
            inv.get("status") or "received",
            INCOMING_STATUS_LABELS,
            INCOMING_STATUS_COLORS,
        )
        if conf := inv.get("ocr_confidence"):
            st.caption(f"OCR-Konfidenz: {CONFIDENCE_LABELS.get(conf, conf)}")
    with top_r:
        if st.button("← Liste", use_container_width=True):
            st.query_params.clear()
            st.rerun()

    # Status-Aktionen
    cols = st.columns(5)
    cur_status = inv.get("status") or "received"
    if cur_status in {"received"} and cols[0].button("📝 In Prüfung", use_container_width=True):
        service.update_status(inv_id, "in_review")
        st.rerun()
    if cur_status in {"received", "in_review", "disputed"} and cols[1].button(
        "✅ Freigeben", type="primary", use_container_width=True,
    ):
        service.update_status(inv_id, "approved")
        st.rerun()
    if cur_status == "approved" and cols[2].button(
        "💶 Als bezahlt verbuchen", type="primary", use_container_width=True,
    ):
        service.update_status(inv_id, "paid")
        st.rerun()
    if cur_status not in INCOMING_DONE_STATUSES and cols[3].button(
        "⚠️ Reklamation", use_container_width=True,
    ):
        service.update_status(inv_id, "disputed")
        st.rerun()

    if inv.get("pdf_storage_path"):
        if cols[4].button("📄 Original-PDF", use_container_width=True):
            try:
                url = service.get_pdf_signed_url(inv["pdf_storage_path"])
                st.link_button("⬇ Download", url, use_container_width=True)
            except Exception as exc:
                st.error(f"Storage-Fehler: {exc}")

    st.divider()

    # Zwei Spalten: Header + Verknüpfungen
    c_l, c_r = st.columns([2, 1])
    with c_l:
        st.markdown("### 📋 Header")
        with st.form(f"edit_header_{inv_id}"):
            cc1, cc2 = st.columns(2)
            new_inv_nr = cc1.text_input(
                "Lieferanten-Rechnungs-Nr.",
                value=inv.get("supplier_invoice_number") or "",
            )
            new_inv_date = cc2.date_input(
                "Rechnungsdatum",
                value=parse_date(inv.get("invoice_date")) or date.today(),
            )
            new_due = cc1.date_input(
                "Fällig am",
                value=parse_date(inv.get("due_date")) or date.today(),
            )
            new_serv = cc2.date_input(
                "Leistungsdatum",
                value=parse_date(inv.get("service_date")) or date.today(),
            )
            new_ref = cc1.text_input(
                "Unsere Bestell-Nr. (auf Re erwähnt)",
                value=inv.get("customer_reference") or "",
            )
            new_supref = cc2.text_input(
                "Lieferanten-Auftrags-Nr.",
                value=inv.get("supplier_reference") or "",
            )

            cc3, cc4, cc5 = st.columns(3)
            new_net = cc3.number_input(
                "Netto €",
                value=float(inv.get("total_net_cents") or 0) / 100.0,
                step=0.01, format="%.2f",
            )
            new_tax = cc4.number_input(
                "USt €",
                value=float(inv.get("tax_total_cents") or 0) / 100.0,
                step=0.01, format="%.2f",
            )
            new_gross = cc5.number_input(
                "Brutto €",
                value=float(inv.get("gross_total_cents") or 0) / 100.0,
                step=0.01, format="%.2f",
            )

            new_notes = st.text_area(
                "Notizen", value=inv.get("notes") or "", height=70,
            )

            if st.form_submit_button("💾 Speichern", type="primary"):
                service.update_invoice(inv_id, {
                    "supplier_invoice_number": new_inv_nr,
                    "invoice_date": new_inv_date,
                    "due_date": new_due,
                    "service_date": new_serv,
                    "customer_reference": new_ref,
                    "supplier_reference": new_supref,
                    "total_net_cents": eur_to_cents(new_net),
                    "tax_total_cents": eur_to_cents(new_tax),
                    "gross_total_cents": eur_to_cents(new_gross),
                    "notes": new_notes,
                })
                st.toast("Header gespeichert.", icon="✅")
                st.rerun()

    with c_r:
        st.markdown("### 🔗 Verknüpfungen")
        st.markdown(f"**Lieferant:** {supplier.get('legal_name') or '—'}")
        if supplier.get("vat_id"):
            st.caption(f"USt-IdNr.: {supplier['vat_id']}")
        st.markdown(f"**Eigene Bestellung (PO):**")
        if po:
            st.success(f"{po.get('po_number')} ({po.get('status')})")
        else:
            st.caption("Nicht verknüpft.")
            # Manueller Match
            po_nr = st.text_input("PO-Nr. zuordnen", placeholder="BE-2026-0007", key=f"link_po_{inv_id}")
            if po_nr and st.button("Verknüpfen", key=f"link_btn_{inv_id}"):
                found = repo.find_po_by_number(po_nr.strip())
                if found:
                    service.update_invoice(inv_id, {"related_po_id": found["id"]})
                    st.toast(f"Verknüpft mit {po_nr}", icon="✅")
                    st.rerun()
                else:
                    st.error(f"Keine PO {po_nr} gefunden.")

        # Konsistenz-Check
        st.markdown("### ⚠️ Konsistenz")
        net = int(inv.get("total_net_cents") or 0)
        tax = int(inv.get("tax_total_cents") or 0)
        gross = int(inv.get("gross_total_cents") or 0)
        diff = gross - (net + tax)
        if abs(diff) > 1:
            st.warning(f"Brutto ≠ Netto + USt (Diff. {cents_to_eur(diff)})")
        else:
            st.success("Brutto = Netto + USt ✓")

    # Items
    st.markdown("### Positionen")
    if not items:
        st.info("Keine Positionen extrahiert.")
    else:
        items_data = []
        for it in items:
            ma = it.get("matched_article") or {}
            items_data.append({
                "Pos": it.get("pos_nr"),
                "SKU (gelesen)": it.get("sku") or "",
                "Bezeichnung": it.get("description") or "",
                "Menge": float(it.get("qty") or 0),
                "Einh.": it.get("unit") or "Stk",
                "EK €": (int(it.get("unit_price_cents") or 0) / 100.0),
                "Summe €": (int(it.get("line_total_cents") or 0) / 100.0),
                "USt %": float(it.get("tax_rate") or 0),
                "Match": ma.get("sku") + " (" + (it.get("match_confidence") or "")  + ")" if ma else (it.get("match_confidence") or ""),
            })
        df = pd.DataFrame(items_data)
        st.dataframe(df, use_container_width=True, hide_index=True)


# =====================================================================
#  Entry
# =====================================================================

def render() -> None:
    render_header(
        "Eingangsrechnungen",
        "Lieferanten-Rechnungen — Upload · OCR · BE-Zuordnung",
    )

    inv_id = st.query_params.get("id")
    if inv_id:
        _render_detail(inv_id)
        render_footer()
        return

    tab_upload, tab_list = st.tabs(["📥 Upload (KI)", "📋 Liste"])
    with tab_upload:
        _render_upload_tab()
    with tab_list:
        _render_list_tab()

    render_footer()
