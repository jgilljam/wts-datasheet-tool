"""Dashboard — Start-Page mit KPI-Strip, Zu-tun-Liste und Activity-Feed."""

from __future__ import annotations

import html as html_lib
from datetime import date

import streamlit as st

from core.branding import render_footer, render_header
from core.config import ACCENT, BORDER, PRIMARY, SUBTLE, TEXT_SECONDARY
from core.utils import cents_to_eur

from . import repo


# ---------- Helfer ----------

_KIND_ICON = {
    "order": "📑",
    "po": "🛒",
    "invoice": "📄",
    "delivery": "📦",
}
_KIND_LABEL = {
    "order": "Auftrag",
    "po": "Bestellung",
    "invoice": "Rechnung",
    "delivery": "Lieferung",
}
_EVENT_LABEL = {
    "created": "angelegt",
    "created_from_order": "aus Auftrag erstellt",
    "updated": "aktualisiert",
    "status_change": "Status geändert",
    "issued": "festgeschrieben",
    "reversed": "storniert",
    "is_storno_for": "ist Storno-Beleg",
    "payment_recorded": "Zahlung erfasst",
    "locked": "GoBD-gesperrt",
    "items_replaced": "Positionen geändert",
    "item_added": "Position hinzugefügt",
    "delivery_created": "Lieferung erstellt",
    "stock_booked": "Bestand gebucht",
    "document_uploaded": "Dokument hochgeladen",
    "document_deleted": "Dokument gelöscht",
}

_GREETING_HOURS = [
    (5, "Guten Morgen"),
    (11, "Guten Tag"),
    (18, "Guten Abend"),
    (23, "Gute Nacht"),
]


def _greeting() -> str:
    from datetime import datetime
    h = datetime.now().hour
    for lim, txt in _GREETING_HOURS:
        if h <= lim:
            return txt
    return "Guten Abend"


def _format_event_text(ev: dict) -> str:
    et = ev.get("event_type") or ""
    base = _EVENT_LABEL.get(et, et.replace("_", " "))
    payload = ev.get("payload") or {}
    if et == "status_change":
        old = payload.get("from") or payload.get("old") or "?"
        new = payload.get("to") or payload.get("new") or "?"
        return f"Status: {old} → {new}"
    if et == "payment_recorded":
        amt = payload.get("amount_cents")
        if amt:
            return f"Zahlung erfasst: {cents_to_eur(amt)}"
        return "Zahlung erfasst"
    if et == "reversed":
        return "storniert"
    if et == "is_storno_for":
        ref = payload.get("original_invoice_number") or payload.get("ref_number") or ""
        return f"Stornobeleg zu {ref}".strip()
    if et == "issued":
        nr = payload.get("invoice_number") or payload.get("number") or ""
        return f"festgeschrieben{f' als {nr}' if nr else ''}"
    if et == "delivery_created":
        nr = payload.get("delivery_number") or ""
        return f"Lieferung erstellt{f': {nr}' if nr else ''}"
    return base


def _delta_arrow(curr: int, prev: int) -> tuple[str, str]:
    """Liefert (Pfeil, Prozent-Text). Bei prev=0 → '—'."""
    if prev == 0:
        if curr > 0:
            return ("↑", "neu")
        return ("·", "—")
    pct = round((curr - prev) / prev * 100)
    if pct > 0:
        return ("↑", f"+{pct}%")
    if pct < 0:
        return ("↓", f"{pct}%")
    return ("·", "0%")


# ---------- Sektionen ----------

def _render_header_with_actions() -> None:
    pages = st.session_state.get("__wts_pages", {})

    today = date.today()
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    date_str = f"{weekdays[today.weekday()]}, {today.day}. {months[today.month-1]} {today.year}"

    head_col, btns_col = st.columns([3, 4])
    with head_col:
        st.markdown(
            f"""
            <div style="margin: 0.25rem 0 1.5rem 0;">
              <div class="wts-eyebrow" style="margin-bottom: 4px;">{html_lib.escape(date_str)}</div>
              <h1 style="margin: 0; font-size: 1.85rem;">{_greeting()}, Julian.</h1>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with btns_col:
        st.write("")
        b1, b2, b3 = st.columns(3)
        if b1.button("📑 Auftrag", help="Neuer Verkaufsauftrag", use_container_width=True):
            if pages.get("orders"):
                st.session_state["orders_open_tab"] = "neu"
                st.switch_page(pages["orders"])
        if b2.button("📄 Datenblatt", help="Datenblatt erstellen", use_container_width=True):
            if pages.get("datasheet"):
                st.switch_page(pages["datasheet"])
        if b3.button("🛒 Bestellung", help="Neue Lieferanten-Bestellung", use_container_width=True):
            if pages.get("purchase_orders"):
                st.session_state["po_open_tab"] = "neu"
                st.switch_page(pages["purchase_orders"])


def _render_kpi_strip(k: dict) -> None:
    rev_curr = k["month_revenue_cents"]
    rev_prev = k["prev_month_revenue_cents"]
    arrow, delta_txt = _delta_arrow(rev_curr, rev_prev)
    delta_color = "#10B981" if arrow == "↑" else ("#EF4444" if arrow == "↓" else TEXT_SECONDARY)

    cols = st.columns(4)
    cols[0].metric(
        "Offene Aufträge",
        k["open_orders"],
        delta=f"{k['todo_drafts_orders']} Entwürfe" if k["todo_drafts_orders"] else None,
        delta_color="off",
    )
    cols[1].metric(
        "Offene Rechnungen",
        k["open_invoices"],
        delta=f"{k['todo_overdue_invoices']} überfällig" if k["todo_overdue_invoices"] else None,
        delta_color="inverse" if k["todo_overdue_invoices"] else "off",
    )
    cols[2].metric(
        "Lieferungen heute",
        k["deliveries_today"],
    )
    cols[3].metric(
        "Umsatz Monat",
        cents_to_eur(rev_curr) or "0,00 €",
        delta=f"{arrow} {delta_txt} vs. Vormonat" if rev_prev or rev_curr else None,
        delta_color="normal" if arrow == "↑" else ("inverse" if arrow == "↓" else "off"),
    )


def _render_todo_card(
    *,
    icon: str,
    label: str,
    count: int,
    accent: str,
    cta: str,
    target_page: str | None,
    state_setter: dict | None = None,
) -> None:
    if count <= 0:
        return
    pages = st.session_state.get("__wts_pages", {})
    box = st.container(border=True)
    with box:
        c1, c2 = st.columns([5, 2])
        c1.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:.75rem;">
              <div style="font-size:1.6rem;line-height:1;">{icon}</div>
              <div>
                <div style="font-size:1.4rem;font-weight:700;color:{accent};line-height:1.1;">{count}</div>
                <div style="color:{PRIMARY};font-weight:500;">{html_lib.escape(label)}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if target_page and pages.get(target_page):
            with c2:
                st.write("")
                if st.button(cta, key=f"todo_{target_page}_{label}", use_container_width=True):
                    if state_setter:
                        for k_, v_ in state_setter.items():
                            st.session_state[k_] = v_
                    st.switch_page(pages[target_page])


def _render_todo_list(k: dict) -> None:
    st.markdown(
        f'<div class="wts-eyebrow" style="margin-bottom:.6rem;">Was zu tun ist</div>',
        unsafe_allow_html=True,
    )

    any_todo = (
        k["todo_overdue_invoices"]
        or k["todo_orders_to_ship_this_week"]
        or k["todo_inbound_pending"]
        or k["todo_drafts_orders"]
        or k["todo_drafts_invoices"]
    )
    if not any_todo:
        st.success("Alles erledigt — keine offenen Punkte.")
        return

    _render_todo_card(
        icon="⚠️",
        label="Überfällige Rechnungen",
        count=k["todo_overdue_invoices"],
        accent="#B91C1C",
        cta="Öffnen →",
        target_page="invoices",
        state_setter={"invoices_list_statuses": ["overdue"]},
    )
    _render_todo_card(
        icon="📦",
        label="Aufträge mit Liefertermin diese Woche",
        count=k["todo_orders_to_ship_this_week"],
        accent="#B45309",
        cta="Öffnen →",
        target_page="orders",
        state_setter={"orders_list_statuses": ["confirmed", "in_production", "partial"]},
    )
    _render_todo_card(
        icon="🛒",
        label="Bestellungen mit ausstehendem Wareneingang",
        count=k["todo_inbound_pending"],
        accent="#1E40AF",
        cta="Öffnen →",
        target_page="purchase_orders",
        state_setter={"po_list_statuses": ["sent", "confirmed", "in_production"]},
    )
    _render_todo_card(
        icon="📑",
        label="Auftrags-Entwürfe (noch nicht bestätigt)",
        count=k["todo_drafts_orders"],
        accent=TEXT_SECONDARY,
        cta="Öffnen →",
        target_page="orders",
        state_setter={"orders_list_statuses": ["draft"]},
    )
    _render_todo_card(
        icon="📄",
        label="Rechnungs-Entwürfe (nicht festgeschrieben)",
        count=k["todo_drafts_invoices"],
        accent=TEXT_SECONDARY,
        cta="Öffnen →",
        target_page="invoices",
        state_setter={"invoices_list_statuses": ["draft"]},
    )


def _render_activity_feed() -> None:
    events = repo.list_recent_activity(limit=12)
    st.markdown(
        f'<div class="wts-eyebrow" style="margin-bottom:.6rem;">Aktivität</div>',
        unsafe_allow_html=True,
    )
    if not events:
        st.caption("Noch keine Aktivität.")
        return

    rows: list[str] = []
    for ev in events:
        kind = ev.get("kind") or ""
        icon = _KIND_ICON.get(kind, "•")
        kind_label = _KIND_LABEL.get(kind, kind)
        ref_nr = ev.get("ref_number") or "—"
        text = _format_event_text(ev)
        ago = repo._ago(ev.get("at"))
        actor = ev.get("actor_label") or ""
        actor_html = (
            f'<span style="color:{TEXT_SECONDARY};font-size:.7rem;"> · {html_lib.escape(actor)}</span>'
            if actor else ""
        )
        rows.append(
            f'<div style="display:flex;gap:.75rem;padding:.55rem .25rem;border-bottom:1px solid {BORDER};">'
            f'<div style="font-size:1.05rem;line-height:1.4;">{icon}</div>'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:.82rem;color:{PRIMARY};">'
            f'<strong>{html_lib.escape(kind_label)}</strong> '
            f'<span style="font-family:ui-monospace,monospace;color:{ACCENT};">{html_lib.escape(ref_nr)}</span>'
            f' — {html_lib.escape(text)}'
            f'</div>'
            f'<div style="font-size:.7rem;color:{TEXT_SECONDARY};">{html_lib.escape(ago)}{actor_html}</div>'
            f'</div>'
            f'</div>'
        )
    st.markdown(
        '<div class="wts-card flat" style="padding:.5rem 1rem;">'
        + "".join(rows)
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------- Page-Entry ----------

def render() -> None:
    render_header("Dashboard", "Überblick — Aufträge · Rechnungen · Lieferungen · Bestellungen")

    try:
        kpis = repo.get_kpis()
    except Exception as exc:
        st.error(f"Konnte KPIs nicht laden: {exc}")
        render_footer()
        return

    _render_header_with_actions()
    _render_kpi_strip(kpis)
    st.write("")

    col_main, col_side = st.columns([2, 1], gap="large")
    with col_main:
        _render_todo_list(kpis)
    with col_side:
        _render_activity_feed()

    render_footer()
