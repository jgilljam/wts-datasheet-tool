"""OP-Liste & Mahnwesen — Übersicht offener Rechnungen + Mahnstufen-Aktionen."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.ui.empty import render_empty_data
from core.ui.kpi import render_kpis
from core.utils import cents_to_eur, format_date

from features.invoices import repo as inv_repo

from . import repo, service
from .constants import AGING_BUCKETS, DUNNING_LEVELS


def _aging_bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "Aktuell"
    for label, lo, hi in AGING_BUCKETS[1:]:
        if lo <= days_overdue <= hi:
            return label
    return ">90 Tage"


def _kpis(rows: list[dict[str, Any]]) -> None:
    total_open = sum(r.get("_open_cents", 0) for r in rows)
    overdue = [r for r in rows if r.get("_days_overdue", 0) > 0]
    total_overdue = sum(r.get("_open_cents", 0) for r in overdue)
    severe = [r for r in rows if r.get("_days_overdue", 0) > 60]
    total_severe = sum(r.get("_open_cents", 0) for r in severe)
    in_dunning = [r for r in rows if int(r.get("current_dunning_level") or 0) > 0]

    render_kpis([
        ("Offen gesamt", cents_to_eur(total_open) or "0,00 €"),
        ("Davon überfällig", cents_to_eur(total_overdue) or "0,00 €"),
        (">60 Tage", cents_to_eur(total_severe) or "0,00 €"),
        ("In Mahnung", len(in_dunning)),
    ])


def _aging_breakdown(rows: list[dict[str, Any]]) -> None:
    """Aging-Buckets als kompakte Pills/Chips."""
    buckets: dict[str, dict[str, int]] = {label: {"count": 0, "sum": 0} for label, _, _ in AGING_BUCKETS}
    for r in rows:
        bucket = _aging_bucket(r.get("_days_overdue", 0))
        buckets[bucket]["count"] += 1
        buckets[bucket]["sum"] += r.get("_open_cents", 0)

    cols = st.columns(len(buckets))
    for col, (label, data) in zip(cols, buckets.items()):
        with col:
            st.metric(
                label,
                cents_to_eur(data["sum"]) or "0,00 €",
                delta=f"{data['count']} Rg.",
                delta_color="off",
            )


def _table(rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    if not rows:
        render_empty_data(
            label="Keine offenen Rechnungen — alles bezahlt 🎉",
            cta_label="",
        )
        return pd.DataFrame(), []

    today = date.today()
    data: list[dict[str, Any]] = []
    ids: list[str] = []
    for r in rows:
        c = r.get("customer") or {}
        days = r.get("_days_overdue", 0)
        bucket = _aging_bucket(days)
        marker = ""
        if days > 90:
            marker = "🔴"
        elif days > 60:
            marker = "🟠"
        elif days > 30:
            marker = "🟡"
        elif days > 0:
            marker = "⚠️"
        else:
            marker = "🟢"
        level = int(r.get("current_dunning_level") or 0)
        ids.append(r["id"])
        data.append({
            "Nr.": r.get("invoice_number") or "",
            "Kunde": c.get("short_name") or c.get("legal_name") or "—",
            "Datum": format_date(r.get("issued_at")),
            "Fällig": format_date(r.get("due_date")),
            "": marker,
            "Tage": days if days > 0 else 0,
            "Bucket": bucket,
            "Offen": cents_to_eur(r.get("_open_cents")) or "—",
            "Mahnstufe": DUNNING_LEVELS.get(level, str(level)),
        })
    df = pd.DataFrame(data)
    return df, ids


def _render_op_list() -> None:
    rows = repo.list_open_invoices(limit=2000)

    f1, f2, f3 = st.columns([2, 2, 1])
    with f1:
        only_overdue = st.toggle(
            "Nur überfällige", value=True,
            help="Blendet Rechnungen aus, die noch nicht fällig sind.",
        )
    with f2:
        level_filter = st.selectbox(
            "Mahnstufe",
            options=["Alle", 0, 1, 2, 3],
            format_func=lambda v: v if v == "Alle" else DUNNING_LEVELS.get(v, str(v)),
        )
    with f3:
        if st.button("🔄 Neu laden", use_container_width=True):
            st.rerun()

    filtered = rows
    if only_overdue:
        filtered = [r for r in filtered if r.get("_days_overdue", 0) > 0]
    if level_filter != "Alle":
        filtered = [r for r in filtered if int(r.get("current_dunning_level") or 0) == level_filter]

    _kpis(filtered)
    _aging_breakdown(filtered)
    st.divider()

    df, ids = _table(filtered)
    if df.empty:
        return

    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="op_list_table",
        column_config={
            "Nr.": st.column_config.TextColumn(width="small"),
            "Datum": st.column_config.TextColumn(width="small"),
            "Fällig": st.column_config.TextColumn(width="small"),
            "": st.column_config.TextColumn(width="small"),
            "Tage": st.column_config.NumberColumn(width="small", format="%d"),
            "Bucket": st.column_config.TextColumn(width="small"),
            "Offen": st.column_config.TextColumn(width="small"),
            "Mahnstufe": st.column_config.TextColumn(width="medium"),
        },
    )

    sel_indices = sel.get("selection", {}).get("rows", [])
    if not sel_indices:
        st.caption(f"{len(df)} offene Rechnungen geladen. Wähle Zeilen für Bulk-Aktionen.")
        return

    selected = [(ids[i], filtered[i]) for i in sel_indices]
    st.markdown(f"**{len(selected)} Rechnung(en) markiert.**")

    c1, c2, c3, c4 = st.columns(4)

    if c1.button("📨 Erinnerung (Stufe 1)", use_container_width=True, type="primary"):
        _bulk_dunning(selected, target_level=1)
    if c2.button("📩 1. Mahnung (Stufe 2)", use_container_width=True):
        _bulk_dunning(selected, target_level=2)
    if c3.button("📌 2. Mahnung (Stufe 3)", use_container_width=True):
        _bulk_dunning(selected, target_level=3)
    if c4.button("⚡ Stufe + 1 (auto)", use_container_width=True):
        _bulk_escalate(selected)


def _bulk_dunning(selected: list[tuple[str, dict]], target_level: int) -> None:
    success = 0
    skipped: list[str] = []
    errors: list[str] = []
    for inv_id, inv in selected:
        try:
            cur = int(inv.get("current_dunning_level") or 0)
            if cur >= target_level:
                skipped.append(f"{inv.get('invoice_number')} (bereits Stufe {cur})")
                continue
            service.create_dunning(inv_id, target_level)
            success += 1
        except Exception as exc:
            errors.append(f"{inv.get('invoice_number')}: {exc}")
    if success:
        st.toast(f"{success} Mahnung(en) erstellt.", icon="✅")
    if skipped:
        st.info("Übersprungen: " + ", ".join(skipped))
    if errors:
        st.error("Fehler: " + " · ".join(errors))
    st.rerun()


def _bulk_escalate(selected: list[tuple[str, dict]]) -> None:
    success = 0
    errors: list[str] = []
    for inv_id, inv in selected:
        try:
            service.escalate_dunning(inv_id)
            success += 1
        except Exception as exc:
            errors.append(f"{inv.get('invoice_number')}: {exc}")
    if success:
        st.toast(f"{success} Rechnung(en) eskaliert.", icon="✅")
    if errors:
        st.error("Fehler: " + " · ".join(errors))
    st.rerun()


def _render_dunning_history() -> None:
    """Zeigt die letzten 50 erstellten Mahnungen mit PDF-Download."""
    from core.db import supabase

    rows = (
        supabase()
        .table("invoice_dunnings")
        .select(
            "id, level, sent_at, due_date, amount_due_cents, fees_cents, "
            "interest_cents, total_cents, "
            "invoice:invoices!invoice_id(id, invoice_number, "
            "customer:parties!customer_id(legal_name, short_name))"
        )
        .order("sent_at", desc=True)
        .limit(50)
        .execute()
        .data
    )
    if not rows:
        st.info("Noch keine Mahnungen erstellt.")
        return

    data = []
    ids = []
    inv_ids = []
    for r in rows:
        inv = r.get("invoice") or {}
        c = inv.get("customer") or {}
        ids.append(r["id"])
        inv_ids.append(inv.get("id"))
        data.append({
            "Datum": format_date(r.get("sent_at")),
            "Stufe": DUNNING_LEVELS.get(r.get("level"), r.get("level")),
            "Rechnung": inv.get("invoice_number") or "—",
            "Kunde": c.get("short_name") or c.get("legal_name") or "—",
            "Hauptforderung": cents_to_eur(r.get("amount_due_cents")),
            "Gebühren": cents_to_eur(r.get("fees_cents")),
            "Zinsen": cents_to_eur(r.get("interest_cents")),
            "Gesamt": cents_to_eur(r.get("total_cents")),
            "Frist": format_date(r.get("due_date")),
        })
    df = pd.DataFrame(data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="dunning_history_table",
    )
    sel_indices = sel.get("selection", {}).get("rows", [])
    if not sel_indices:
        st.caption(f"{len(df)} Mahnungen — wähle eine Zeile für PDF-Download.")
        return

    idx = sel_indices[0]
    dunning = repo.get_dunning(ids[idx])
    if not dunning:
        return

    st.markdown(f"**Mahnung Stufe {dunning['level']}** vom {format_date(dunning.get('sent_at'))}")

    if st.button("📄 Mahnung-PDF erzeugen", type="primary", key=f"gen_pdf_{ids[idx]}"):
        try:
            inv = inv_repo.get_invoice(inv_ids[idx])
            items = inv_repo.list_invoice_items(inv_ids[idx])
            from lib.beleg_generator import render_mahnung_pdf
            pdf_bytes = render_mahnung_pdf(inv, items, dunning)
            st.session_state[f"mahnung_pdf_{ids[idx]}"] = pdf_bytes
            st.success(f"PDF erzeugt ({len(pdf_bytes) // 1024} KB).")
        except Exception as exc:
            st.error(f"PDF-Fehler: {exc}")

    pdf_bytes = st.session_state.get(f"mahnung_pdf_{ids[idx]}")
    if pdf_bytes:
        inv = data[idx]
        st.download_button(
            "⬇ Download Mahnung.pdf",
            data=pdf_bytes,
            file_name=f"Mahnung_{inv['Rechnung']}_S{dunning['level']}.pdf",
            mime="application/pdf",
            key=f"dl_mahnung_{ids[idx]}",
        )


def render() -> None:
    render_header()
    st.title("💼 OP-Liste & Mahnwesen")

    tab_op, tab_history = st.tabs(["📋 Offene Posten", "📚 Mahnungs-Historie"])
    with tab_op:
        _render_op_list()
    with tab_history:
        _render_dunning_history()

    render_footer()
