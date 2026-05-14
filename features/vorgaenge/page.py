"""Vorgangs-Übersicht — Zeitraum-Filter + Status-Ampeln + PDFs pro Vorgang."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from lib import pdf_bundle

from . import service


# =====================================================================
#  Helpers
# =====================================================================

def _ampel(ok: bool, partial: bool = False) -> str:
    if ok:
        return "✅"
    if partial:
        return "🟡"
    return "⚪"


def _signed_url(bucket: str, path: str, expires_in: int = 3600) -> str | None:
    try:
        res = supabase().storage.from_(bucket).create_signed_url(path, expires_in)
        return res.get("signedURL") or res.get("signed_url")
    except Exception:
        return None


def _format_eur(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# =====================================================================
#  KPI-Bar
# =====================================================================

def _render_kpis(vorgaenge: list[dict[str, Any]]) -> None:
    if not vorgaenge:
        return
    total = len(vorgaenge)
    sum_net = sum(v.get("order_net_eur") or 0 for v in vorgaenge)
    n_with_outinv = sum(1 for v in vorgaenge if v["flags"]["has_outgoing_invoice"])
    n_offen_rechnung = total - n_with_outinv
    margin_sum = sum(v["margin_eur"] for v in vorgaenge if v.get("margin_eur") is not None)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vorgänge", total)
    c2.metric("Auftragsvolumen (Netto)", _format_eur(sum_net))
    c3.metric("Rechnung an Kunde offen", n_offen_rechnung)
    c4.metric("Marge bekannt (Σ)", _format_eur(margin_sum) if margin_sum else "—")


# =====================================================================
#  Tabelle
# =====================================================================

def _render_table(vorgaenge: list[dict[str, Any]]) -> None:
    if not vorgaenge:
        st.info("Keine Vorgänge im gewählten Zeitraum.")
        return

    rows = []
    for v in vorgaenge:
        f = v["flags"]
        rows.append({
            "Auftrag": v["order_number"],
            "Kunde": v["customer_name"],
            "Datum": v["ordered_at"] or "",
            "Status": v["status"] or "—",
            "Betrag": _format_eur(v["order_net_eur"]),
            "🛒 Bestellung": _ampel(f["has_pos"]),
            "📑 AB Lieferant": _ampel(f["all_pos_confirmed"], partial=f["has_pos"] and not f["all_pos_confirmed"]),
            "📥 ER Lieferant": _ampel(f["all_pos_invoiced"], partial=f["has_pos"] and not f["all_pos_invoiced"]),
            "📤 AR an Kunde": _ampel(f["has_outgoing_invoice"]),
            "PDFs": len(service.collect_pdf_paths(v)),
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Auftrag": st.column_config.TextColumn(width="small"),
            "PDFs": st.column_config.NumberColumn(width="small"),
        },
    )


# =====================================================================
#  Detail-Expander pro Vorgang
# =====================================================================

def _render_details(vorgaenge: list[dict[str, Any]]) -> None:
    if not vorgaenge:
        return
    st.markdown("### Detail-Ansicht")
    for v in vorgaenge:
        f = v["flags"]
        ampel_line = (
            f"🛒{_ampel(f['has_pos'])} "
            f"📑{_ampel(f['all_pos_confirmed'])} "
            f"📥{_ampel(f['all_pos_invoiced'])} "
            f"📤{_ampel(f['has_outgoing_invoice'])}"
        )
        title = (
            f"{v['order_number']}  ·  {v['customer_name']}  ·  "
            f"{_format_eur(v['order_net_eur'])}  ·  {ampel_line}"
        )
        with st.expander(title, expanded=False):
            c_left, c_right = st.columns([2, 1])
            with c_left:
                st.markdown(f"**Status:** `{v['status']}`  ·  **Datum:** {v['ordered_at']}")
                if v.get("customer_reference"):
                    st.caption(f"Kunden-Referenz: {v['customer_reference']}")

                # Bestellungen bei Lieferanten
                pos = v["purchase_orders"]
                if pos:
                    st.markdown("**Bestellungen bei Lieferanten:**")
                    for p in pos:
                        ab_marker = "✅" if p.get("confirmed_at") else "⏳"
                        sup = (p.get("supplier") or {}).get("legal_name") or "—"
                        st.markdown(
                            f"- `{p.get('po_number') or '—'}` · {sup} · "
                            f"{ab_marker} AB · Status: `{p.get('status')}`"
                        )

                # Eingangsrechnungen
                inc = v["incoming_invoices"]
                if inc:
                    st.markdown("**Eingangsrechnungen (Lieferanten):**")
                    for inv in inc:
                        sup = (inv.get("supplier") or {}).get("legal_name") or "—"
                        st.markdown(
                            f"- `{inv.get('invoice_number') or '—'}` · {sup} · "
                            f"{_format_eur((inv.get('total_net_cents') or 0) / 100.0)} netto · "
                            f"Status: `{inv.get('status')}`"
                        )

                # Ausgangsrechnungen
                out_tool = v["outgoing_invoices_tool"]
                out_mail = v["outgoing_mails_sent"]
                if out_tool or out_mail:
                    st.markdown("**Ausgangsrechnungen / versendete Mails:**")
                    for inv in out_tool:
                        st.markdown(
                            f"- 🧾 Tool-Rechnung `{inv.get('invoice_number') or '—'}` · "
                            f"{_format_eur((inv.get('total_net_cents') or 0) / 100.0)} netto · "
                            f"Status: `{inv.get('status')}`"
                        )
                    for m in out_mail:
                        cat = m.get("ai_category") or "—"
                        st.markdown(
                            f"- 📤 Mail: _{m.get('subject') or '(ohne Betreff)'}_ · "
                            f"an {m.get('to_email') or '—'} · Kategorie: `{cat}`"
                        )
                elif not (out_tool or out_mail):
                    st.warning("Noch keine Rechnung an Kunde versendet.", icon="📤")

            with c_right:
                pdfs = service.collect_pdf_paths(v)
                st.markdown(f"**PDFs ({len(pdfs)})**")
                if not pdfs:
                    st.caption("Keine PDFs.")
                else:
                    for p in pdfs:
                        url = _signed_url(p["bucket"], p["path"])
                        if url:
                            st.markdown(f"[📄 {p['label']}]({url})")
                        else:
                            st.caption(f"📄 {p['label']} _(nicht erreichbar)_")

                    # Per-Vorgang-ZIP-Button
                    zip_key = f"__vz_{v['order_id']}"
                    if st.button(
                        "📦 ZIP für Vorgang",
                        key=f"btn_{zip_key}",
                        use_container_width=True,
                    ):
                        with st.spinner("Baue ZIP …"):
                            zb, _ = pdf_bundle.build_zip_for_vorgang(v, pdfs)
                            st.session_state[zip_key] = zb
                    if st.session_state.get(zip_key):
                        st.download_button(
                            f"⬇️ {v['order_number']}.zip",
                            data=st.session_state[zip_key],
                            file_name=f"{v['order_number']}.zip",
                            mime="application/zip",
                            key=f"dl_{zip_key}",
                            use_container_width=True,
                        )


# =====================================================================
#  Main
# =====================================================================

def render() -> None:
    render_header(
        title="Vorgangs-Übersicht",
        subtitle="Offene Aufträge im Zeitraum, mit Lieferanten-ABs, Eingangs- und Ausgangsrechnungen.",
    )

    today = date.today()
    first_of_month = today.replace(day=1)

    # Default-Werte (überschreibbar via Quick-Buttons unten — die löschen die
    # Widget-States vor dem Render, damit value= greift).
    default_from = st.session_state.get("_vg_default_from", first_of_month)
    default_to = st.session_state.get("_vg_default_to", today)

    # ----- Schnell-Buttons (MÜSSEN vor den date_inputs stehen, sonst greift
    # der reset nicht im selben Run) -----
    qb1, qb2, qb3, qb4, _, qb_match = st.columns([1, 1, 1, 1, 4, 2])

    if qb_match.button(
        "🔄 Sent-Mails matchen",
        use_container_width=True,
        help="Geht alle versendeten Mails durch und sucht in Subject+Body "
             "nach Auftragsnummern (AB/RE/BE), um sie mit Belegen zu verlinken.",
    ):
        with st.spinner("Matching läuft …"):
            stats = service.rematch_all_unlinked_sent_mails()
        st.toast(
            f"✅ {stats['linked']} von {stats['processed']} Sent-Mails verlinkt"
            + (f" · {stats['failed']} Fehler" if stats.get("failed") else ""),
            icon="🔗",
        )
        st.rerun()

    def _apply_quick(d_from: date, d_to: date) -> None:
        st.session_state["_vg_default_from"] = d_from
        st.session_state["_vg_default_to"] = d_to
        # Widget-State löschen, damit value= beim Re-Render greift
        st.session_state.pop("vorgaenge_date_from", None)
        st.session_state.pop("vorgaenge_date_to", None)

    if qb1.button("Akt. Monat", use_container_width=True):
        _apply_quick(first_of_month, today)
        st.rerun()
    if qb2.button("Letzt. Monat", use_container_width=True):
        last_month_end = first_of_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        _apply_quick(last_month_start, last_month_end)
        st.rerun()
    if qb3.button("Akt. Quartal", use_container_width=True):
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        _apply_quick(today.replace(month=q_start_month, day=1), today)
        st.rerun()
    if qb4.button("Akt. Jahr", use_container_width=True):
        _apply_quick(today.replace(month=1, day=1), today)
        st.rerun()

    # ----- Filter-Bar -----
    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    with f1:
        date_from = st.date_input(
            "Von",
            value=default_from,
            format="DD.MM.YYYY",
            key="vorgaenge_date_from",
        )
    with f2:
        date_to = st.date_input(
            "Bis",
            value=default_to,
            format="DD.MM.YYYY",
            key="vorgaenge_date_to",
        )
    with f3:
        only_open = st.toggle(
            "Nur offene Vorgänge",
            value=True,
            key="vorgaenge_only_open",
            help="Blendet abgeschlossene/stornierte Aufträge aus.",
        )
    with f4:
        search = st.text_input(
            "🔍",
            placeholder="Auftragsnummer, Kunden-Referenz …",
            key="vorgaenge_search",
            label_visibility="collapsed",
        )

    # ----- Daten laden -----
    try:
        vorgaenge = service.list_vorgaenge(
            date_from=date_from,
            date_to=date_to,
            only_open=only_open,
            search=search or None,
        )
    except Exception as e:
        st.error(f"Fehler beim Laden: {e}")
        vorgaenge = []

    # ----- KPIs + Tabelle + Details -----
    _render_kpis(vorgaenge)
    st.divider()
    _render_table(vorgaenge)
    st.divider()

    # ZIP-Download für Zeitraum
    z1, z2, z3 = st.columns([3, 1, 1])
    with z1:
        total_pdfs = sum(len(service.collect_pdf_paths(v)) for v in vorgaenge)
        st.caption(
            f"📦 Bulk-Export: {len(vorgaenge)} Vorgänge · {total_pdfs} PDFs · "
            f"Zeitraum {date_from} – {date_to}"
        )
    with z2:
        if st.button(
            "📦 ZIP bauen",
            disabled=not vorgaenge,
            use_container_width=True,
            help="Baut ZIP mit allen PDFs + INDEX.csv. Kann bei vielen Belegen einen Moment dauern.",
        ):
            with st.spinner(f"Lade {total_pdfs} PDFs aus Storage …"):
                zip_bytes, stats = pdf_bundle.build_zip_for_zeitraum(
                    vorgaenge,
                    date_from,
                    date_to,
                    service.collect_pdf_paths,
                )
            st.session_state["__vorgaenge_zip_bytes"] = zip_bytes
            st.session_state["__vorgaenge_zip_stats"] = stats
            st.session_state["__vorgaenge_zip_filename"] = (
                f"WTS_Vorgaenge_{date_from}_bis_{date_to}.zip"
            )
    with z3:
        zip_bytes = st.session_state.get("__vorgaenge_zip_bytes")
        if zip_bytes:
            stats = st.session_state.get("__vorgaenge_zip_stats") or {}
            st.download_button(
                f"⬇️ {stats.get('loaded', 0)} PDFs",
                data=zip_bytes,
                file_name=st.session_state.get("__vorgaenge_zip_filename") or "vorgaenge.zip",
                mime="application/zip",
                use_container_width=True,
            )
        else:
            st.caption("")  # Platzhalter

    if st.session_state.get("__vorgaenge_zip_stats"):
        stats = st.session_state["__vorgaenge_zip_stats"]
        if stats.get("failed", 0) > 0:
            st.warning(
                f"⚠️ {stats['failed']} PDFs konnten nicht geladen werden "
                "(evtl. Storage-Pfad veraltet)."
            )

    _render_details(vorgaenge)

    render_footer()
