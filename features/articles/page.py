"""Artikel-Stammdaten: Liste · Neu anlegen · Bearbeiten.

Versorgt sowohl den Items-Editor in `features/deliveries` als auch die
Bestand-/Buchungs-Seite in `features/stock`. Alle Schreiboperationen
gehen direkt gegen Supabase und invalidieren den Cache.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.ui.empty import render_empty_data, render_empty_filter


@st.cache_data(ttl=30)
def _list_articles(only_active: bool = True) -> list[dict[str, Any]]:
    q = supabase().table("articles").select("*")
    if only_active:
        q = q.eq("is_active", True)
    return q.order("sku").execute().data


def _eur(cents: int | None) -> float:
    return (cents or 0) / 100


def _cents(eur: float | None) -> int | None:
    if not eur:
        return None
    return int(round(eur * 100))


# ---------- Tab: Liste ----------


def _render_list_tab() -> None:
    c1, c2 = st.columns([3, 1])
    search = (
        c1.text_input("Suche SKU oder Bezeichnung", "", key="art_list_search")
        .strip()
        .lower()
    )
    show_inactive = c2.checkbox("inkl. inaktive", key="art_list_show_inactive")

    try:
        articles = _list_articles(only_active=not show_inactive)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Artikel nicht laden: {exc}")
        return

    rows: list[dict[str, Any]] = []
    inactive = 0
    total_value_cents = 0
    for a in articles:
        if search and not (
            search in (a.get("sku") or "").lower()
            or search in (a.get("title_de") or "").lower()
            or search in (a.get("manufacturer_sku") or "").lower()
        ):
            continue
        if not a.get("is_active", True):
            inactive += 1
        rows.append(
            {
                "SKU": a.get("sku") or "",
                "Hersteller-SKU": a.get("manufacturer_sku") or "",
                "Bezeichnung": a.get("title_de") or "",
                "Einheit": a.get("unit") or "Stk",
                "EK €": _eur(a.get("default_price_cents")),
                "Mindest": (
                    float(a["min_stock_qty"]) if a.get("min_stock_qty") is not None else None
                ),
                "Pfand": "✓" if a.get("is_pfand") else "",
                "ADR": a.get("adr_un_nr") or "",
                "Aktiv": bool(a.get("is_active", True)),
            }
        )
        total_value_cents += (a.get("default_price_cents") or 0)

    m1, m2, m3 = st.columns(3)
    m1.metric("Treffer", len(rows))
    m2.metric("davon inaktiv", inactive)
    m3.metric("Σ EK-Wert (Stamm)", f"{total_value_cents / 100:.2f} €")

    if not rows:
        render_empty_filter(
            label="Keine Artikel mit diesen Filtern.",
            reset_keys=["art_list_search", "art_list_show_inactive"],
        )
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "SKU": st.column_config.TextColumn(width="small"),
            "Hersteller-SKU": st.column_config.TextColumn(width="small"),
            "EK €": st.column_config.NumberColumn(format="%.2f €", width="small"),
            "Mindest": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Einheit": st.column_config.TextColumn(width="small"),
            "Pfand": st.column_config.TextColumn(width="small"),
            "ADR": st.column_config.TextColumn(width="small"),
            "Aktiv": st.column_config.CheckboxColumn(width="small"),
        },
    )


# ---------- Tab: Neu anlegen ----------


def _render_create_tab() -> None:
    with st.form("new_article", clear_on_submit=True):
        c1, c2 = st.columns(2)
        sku = c1.text_input("SKU *", help="Eindeutige interne Artikel-Nr (z. B. EV1413FR).")
        manufacturer_sku = c2.text_input("Hersteller-SKU")

        title_de = st.text_input("Bezeichnung (DE) *")

        c3, c4, c5 = st.columns(3)
        unit = c3.text_input("Einheit", value="Stk")
        price_eur = c4.number_input(
            "EK-Preis €", value=0.0, min_value=0.0, step=0.01, format="%.2f"
        )
        min_stock = c5.number_input(
            "Mindestbestand", value=0.0, min_value=0.0, step=1.0
        )

        is_pfand = st.checkbox("Pfand-Artikel")

        submitted = st.form_submit_button(
            "➕ Artikel anlegen", type="primary", use_container_width=True
        )

        if submitted:
            if not sku.strip() or not title_de.strip():
                st.warning("SKU und Bezeichnung sind Pflichtfelder.")
                return
            payload: dict[str, Any] = {
                "sku": sku.strip(),
                "title_de": title_de.strip(),
                "unit": unit.strip() or "Stk",
                "is_active": True,
                "is_pfand": is_pfand,
            }
            if manufacturer_sku.strip():
                payload["manufacturer_sku"] = manufacturer_sku.strip()
            cents = _cents(price_eur)
            if cents:
                payload["default_price_cents"] = cents
            if min_stock > 0:
                payload["min_stock_qty"] = min_stock

            try:
                supabase().table("articles").insert(payload).execute()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Anlegen fehlgeschlagen: {exc}")
                return
            _list_articles.clear()
            st.success(f"Artikel '{sku}' angelegt.")
            st.rerun()


# ---------- Tab: Bearbeiten ----------


def _render_edit_tab() -> None:
    try:
        articles = _list_articles(only_active=False)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Artikel nicht laden: {exc}")
        return

    if not articles:
        render_empty_data(
            title="Noch keine Artikel",
            description="Leg deinen ersten Artikel im Tab „Neu anlegen“ an. Artikel sind die Grundlage für Lager, Lieferungen und Belege.",
            icon="🔧",
        )
        return

    options = {
        a["id"]: f"{a['sku']} - {a.get('title_de') or ''}".strip(" -") for a in articles
    }
    selected_id = st.selectbox(
        "Artikel wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="edit_select",
    )
    if not selected_id:
        return

    article = next((a for a in articles if a["id"] == selected_id), None)
    if not article:
        return

    with st.form(f"edit_article_{selected_id}", clear_on_submit=False):
        c1, c2 = st.columns(2)
        sku = c1.text_input("SKU", value=article.get("sku") or "")
        manufacturer_sku = c2.text_input(
            "Hersteller-SKU", value=article.get("manufacturer_sku") or ""
        )

        title_de = st.text_input("Bezeichnung (DE)", value=article.get("title_de") or "")
        title_en = st.text_input("Bezeichnung (EN)", value=article.get("title_en") or "")

        c3, c4, c5 = st.columns(3)
        category = c3.text_input("Kategorie", value=article.get("category") or "")
        unit = c4.text_input("Einheit", value=article.get("unit") or "Stk")
        price_eur = c5.number_input(
            "EK-Preis €",
            value=_eur(article.get("default_price_cents")),
            min_value=0.0,
            step=0.01,
            format="%.2f",
        )

        c6, c7 = st.columns(2)
        min_stock_default = (
            float(article["min_stock_qty"]) if article.get("min_stock_qty") is not None else 0.0
        )
        min_stock = c6.number_input(
            "Mindestbestand",
            value=min_stock_default,
            min_value=0.0,
            step=1.0,
        )
        is_active = c7.toggle("Aktiv", value=bool(article.get("is_active", True)))

        with st.expander("Pfand"):
            is_pfand = st.checkbox(
                "Pfand-Artikel", value=bool(article.get("is_pfand", False))
            )
            pfand_eur = st.number_input(
                "Pfand pro Einheit €",
                value=_eur(article.get("pfand_per_unit_cents")),
                min_value=0.0,
                step=0.01,
                format="%.2f",
            )

        with st.expander("Gefahrgut (ADR)"):
            adr_un = st.text_input(
                "UN-Nummer (z. B. UN 3252)", value=article.get("adr_un_nr") or ""
            )
            adr_class = st.text_input(
                "ADR-Klasse (z. B. 2.1)", value=article.get("adr_class") or ""
            )
            adr_proper_name = st.text_input(
                "ADR Eigenname", value=article.get("adr_proper_name") or ""
            )
            adr_kg_default = (
                float(article["adr_net_kg_per_unit"])
                if article.get("adr_net_kg_per_unit") is not None
                else 0.0
            )
            adr_kg = st.number_input(
                "ADR Netto-Gewicht pro Einheit (kg)",
                value=adr_kg_default,
                min_value=0.0,
                step=0.001,
                format="%.3f",
            )

        c_save, c_del = st.columns([3, 1])
        save_clicked = c_save.form_submit_button(
            "💾 Änderungen speichern", type="primary", use_container_width=True
        )
        delete_clicked = c_del.form_submit_button(
            "🗑 Deaktivieren", use_container_width=True,
            help="Setzt is_active=false. Echtes Löschen nicht möglich, weil Lieferungen darauf verweisen.",
        )

    if save_clicked:
        if not sku.strip() or not title_de.strip():
            st.warning("SKU und Bezeichnung sind Pflicht.")
            return
        payload: dict[str, Any] = {
            "sku": sku.strip(),
            "title_de": title_de.strip(),
            "unit": unit.strip() or "Stk",
            "is_active": is_active,
            "is_pfand": is_pfand,
            "manufacturer_sku": manufacturer_sku.strip() or None,
            "title_en": title_en.strip() or None,
            "category": category.strip() or None,
            "default_price_cents": _cents(price_eur),
            "min_stock_qty": min_stock if min_stock > 0 else None,
            "pfand_per_unit_cents": _cents(pfand_eur),
            "adr_un_nr": adr_un.strip() or None,
            "adr_class": adr_class.strip() or None,
            "adr_proper_name": adr_proper_name.strip() or None,
            "adr_net_kg_per_unit": adr_kg if adr_kg > 0 else None,
        }
        try:
            supabase().table("articles").update(payload).eq("id", selected_id).execute()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        _list_articles.clear()
        st.success(f"Gespeichert: {sku}")
        st.rerun()

    if delete_clicked:
        try:
            supabase().table("articles").update({"is_active": False}).eq(
                "id", selected_id
            ).execute()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Deaktivieren fehlgeschlagen: {exc}")
            return
        _list_articles.clear()
        st.success(f"Artikel '{article.get('sku')}' deaktiviert.")
        st.rerun()


# ---------- Entry ----------


def render() -> None:
    render_header("Artikel", "Stammdaten · Liste · Anlegen · Bearbeiten")

    tab_list, tab_new, tab_edit = st.tabs(
        ["📋 Liste", "➕ Neu anlegen", "✏️ Bearbeiten"]
    )
    with tab_list:
        _render_list_tab()
    with tab_new:
        _render_create_tab()
    with tab_edit:
        _render_edit_tab()

    render_footer()
