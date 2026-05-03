"""Lager-Page: Bestandsübersicht, manuelle Buchungen, Bewegungs-Historie.

Modell: Single-Lager (kein location-Feld). Bestand pro Artikel = Summe
aller Bewegungen aus `stock_movements` (View `stock_balances`).

Migration: db/0002_stock.sql (im Supabase-SQL-Editor ausführen, bevor
diese Page genutzt wird — sonst kommt Tabellen-fehlt-Fehler).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase


MOVEMENT_TYPES = ["inbound", "outbound", "adjustment"]
MOVEMENT_LABELS = {
    "inbound": "📥 Wareneingang",
    "outbound": "📤 Versand",
    "adjustment": "✏️ Korrektur / Inventur",
    "transfer": "🔄 Umlagerung",
}


# ---------- Datenzugriff (lokal, kein eigener repo.py — Modul klein) ----------


@st.cache_data(ttl=30)
def _list_balances() -> list[dict[str, Any]]:
    return (
        supabase()
        .table("stock_balances")
        .select("*")
        .order("sku")
        .execute()
        .data
    )


@st.cache_data(ttl=60)
def _list_articles_for_dropdown() -> list[dict[str, Any]]:
    return (
        supabase()
        .table("articles")
        .select("id, sku, title_de, unit")
        .eq("is_active", True)
        .order("sku")
        .execute()
        .data
    )


def _list_movements(article_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("stock_movements")
        .select("*, articles(sku, title_de)")
    )
    if article_id:
        q = q.eq("article_id", article_id)
    return q.order("at", desc=True).limit(limit).execute().data


def _create_movement(
    article_id: str,
    qty_delta: float,
    movement_type: str,
    note: str | None = None,
    batch_lot: str | None = None,
) -> None:
    actor_label = st.session_state.get("user_email") or "Mitarbeiter"
    supabase().table("stock_movements").insert(
        {
            "article_id": article_id,
            "qty_delta": qty_delta,
            "movement_type": movement_type,
            "actor_label": actor_label,
            "note": note,
            "batch_lot": batch_lot,
        }
    ).execute()
    _list_balances.clear()


# ---------- Format-Helfer ----------


def _format_dt(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return v
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


# ---------- Tabs ----------


def _render_balances_tab() -> None:
    try:
        balances = _list_balances()
    except Exception as exc:  # noqa: BLE001
        st.error(
            "Konnte Bestände nicht laden. "
            "Wurde die Migration `db/0002_stock.sql` im Supabase-SQL-Editor ausgeführt?\n\n"
            f"Fehler: {exc}"
        )
        return

    c1, c2 = st.columns([3, 1])
    search = c1.text_input(
        "Suche SKU oder Bezeichnung", "", key="stock_search"
    ).strip().lower()
    only_below = c2.checkbox("nur unter Mindest", key="stock_only_below")

    rows = []
    below_count = 0
    posten_count = 0
    total_value_cents = 0
    for b in balances:
        if search and not (
            search in (b.get("sku") or "").lower()
            or search in (b.get("title_de") or "").lower()
        ):
            continue
        if only_below and not b.get("below_min"):
            continue
        if b.get("below_min"):
            below_count += 1
        qty = float(b.get("qty_on_hand") or 0)
        if qty > 0:
            posten_count += 1
        ek_cents = b.get("default_price_cents") or 0
        value_cents = b.get("value_cents") or 0
        total_value_cents += value_cents
        rows.append(
            {
                "SKU": b.get("sku") or "",
                "Bezeichnung": b.get("title_de") or "",
                "Bestand": qty,
                "Einheit": b.get("unit") or "Stk",
                "EK €": ek_cents / 100,
                "Wert €": value_cents / 100,
                "Mindest": (
                    float(b["min_stock_qty"]) if b.get("min_stock_qty") is not None else None
                ),
                "⚠": "⚠️" if b.get("below_min") else "",
                "ADR": b.get("adr_un_nr") or "",
                "Pfand": "✓" if b.get("is_pfand") else "",
                "Letzte Bewegung": _format_dt(b.get("last_movement_at")),
            }
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Artikel im Lager", posten_count)
    m2.metric("Unter Mindestbestand", below_count)
    m3.metric("Stammdaten gesamt", len(balances))
    m4.metric("Σ Lagerwert", f"{total_value_cents / 100:,.2f} €".replace(",", "."))

    if not rows:
        st.info("Keine Artikel mit diesen Filtern.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "SKU": st.column_config.TextColumn(width="small"),
            "Bestand": st.column_config.NumberColumn(format="%.2f", width="small"),
            "EK €": st.column_config.NumberColumn(format="%.2f €", width="small"),
            "Wert €": st.column_config.NumberColumn(format="%.2f €", width="small"),
            "Mindest": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Einheit": st.column_config.TextColumn(width="small"),
            "⚠": st.column_config.TextColumn(width="small"),
            "ADR": st.column_config.TextColumn(width="small"),
            "Pfand": st.column_config.TextColumn(width="small"),
        },
    )


def _render_booking_tab() -> None:
    try:
        articles = _list_articles_for_dropdown()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Artikel nicht laden: {exc}")
        return

    if not articles:
        st.info(
            "Keine aktiven Artikel angelegt. "
            "Stammdaten-Pflege folgt als eigene Page; bis dahin Artikel direkt in Supabase einfügen."
        )
        return

    article_choices = {
        a["id"]: f"{a['sku']} — {a.get('title_de') or ''}".strip(" —")
        for a in articles
    }

    with st.form("stock_booking", clear_on_submit=True):
        article_id = st.selectbox(
            "Artikel",
            list(article_choices.keys()),
            format_func=lambda v: article_choices[v],
        )
        movement_type = st.selectbox(
            "Buchungs-Typ",
            MOVEMENT_TYPES,
            format_func=lambda v: MOVEMENT_LABELS[v],
            help="Eingang erhöht Bestand, Versand reduziert ihn. Korrektur/Inventur kann beides.",
        )
        c1, c2 = st.columns(2)
        qty_delta = c1.number_input(
            "Menge (+ Eingang / – Ausgang)",
            value=0.0,
            step=1.0,
            format="%.2f",
            help="Positiv = Bestand wird erhöht, negativ = reduziert.",
        )
        batch = c2.text_input("Charge / Lot (optional)")
        note = st.text_area("Notiz (optional)", height=60)

        submitted = st.form_submit_button(
            "📦 Buchung speichern", type="primary", use_container_width=True
        )

        if submitted:
            if qty_delta == 0:
                st.warning("Menge darf nicht 0 sein.")
                return
            try:
                _create_movement(
                    article_id=article_id,
                    qty_delta=qty_delta,
                    movement_type=movement_type,
                    note=note.strip() or None,
                    batch_lot=batch.strip() or None,
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Buchung fehlgeschlagen: {exc}")
                return
            sign = "+" if qty_delta > 0 else ""
            st.success(
                f"Buchung gespeichert: {sign}{qty_delta:.2f} {article_choices[article_id]}"
            )
            st.rerun()


def _render_history_tab() -> None:
    try:
        articles = _list_articles_for_dropdown()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Artikel nicht laden: {exc}")
        return

    article_choices: dict[str | None, str] = {None: "— alle Artikel —"}
    for a in articles:
        article_choices[a["id"]] = f"{a['sku']} — {a.get('title_de') or ''}".strip(" —")

    article_id = st.selectbox(
        "Artikel filtern",
        list(article_choices.keys()),
        format_func=lambda v: article_choices[v],
        key="hist_article",
    )

    try:
        movements = _list_movements(article_id=article_id, limit=200)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Bewegungen nicht laden: {exc}")
        return

    if not movements:
        st.info("Keine Bewegungen vorhanden.")
        return

    rows = []
    for m in movements:
        a = m.get("articles") or {}
        rows.append(
            {
                "Datum": _format_dt(m.get("at")),
                "SKU": a.get("sku") or "",
                "Typ": MOVEMENT_LABELS.get(m.get("movement_type"), m.get("movement_type") or ""),
                "Menge": float(m.get("qty_delta") or 0),
                "Charge": m.get("batch_lot") or "",
                "Wer": m.get("actor_label") or "",
                "Notiz": m.get("note") or "",
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Menge": st.column_config.NumberColumn(format="%.2f"),
            "SKU": st.column_config.TextColumn(width="small"),
            "Typ": st.column_config.TextColumn(width="medium"),
            "Charge": st.column_config.TextColumn(width="small"),
        },
    )


# ---------- Entry ----------


def render() -> None:
    render_header("Lager", "Bestand · Buchungen · Bewegungs-Historie")

    tab_bal, tab_book, tab_hist = st.tabs(
        ["📊 Bestand", "✏️ Buchung", "📜 Historie"]
    )
    with tab_bal:
        _render_balances_tab()
    with tab_book:
        _render_booking_tab()
    with tab_hist:
        _render_history_tab()

    render_footer()
