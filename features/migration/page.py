"""sevDesk-Migration — Bulk-Import offener Aufträge.

3 Eingabe-Modi:
  - Freitext: rohe Notizen / Liste paste → KI parst alles
  - PDF-Upload: alte Auftragsbestätigungen, eine pro Datei
  - CSV-Upload: sevDesk-Export → KI interpretiert Spalten

Workflow:
  1. Quelle wählen + KI-Extraktion
  2. Preview-Tabelle (jeder Auftrag eine Zeile, Checkbox + Editierfelder)
  3. „Importieren" — Drafts werden angelegt
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.config import gemini_settings
from lib import migration_ai, migration_to_beleg


CONF_BADGE = {"high": "🟢", "medium": "🟡", "low": "🔴"}


def _parse_form(parsed_orders: list[Any]) -> list[dict[str, Any]]:
    """Pydantic → dict mit allen Feldern."""
    out: list[dict[str, Any]] = []
    for p in parsed_orders or []:
        if hasattr(p, "model_dump"):
            out.append(p.model_dump())
        elif isinstance(p, dict):
            out.append(p)
    return out


def _summary_df(orders: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for i, o in enumerate(orders):
        items = o.get("items") or []
        total_eur = sum(float(it.get("qty") or 0) * float(it.get("target_price_eur") or 0) for it in items)
        rows.append({
            "✓": True,
            "#": i + 1,
            "Konfidenz": f"{CONF_BADGE.get(o.get('confidence', 'medium'), '⚪')} {o.get('confidence', 'medium')}",
            "Kunde": o.get("customer_name") or "(unbekannt)",
            "Email": o.get("customer_email") or "",
            "Bestell-Nr": o.get("customer_reference") or "",
            "Positionen": len(items),
            "Wunschtermin": o.get("requested_delivery_date") or "",
            "Summe (€)": round(total_eur, 2),
            "Notiz": (o.get("notes") or "")[:80],
        })
    return pd.DataFrame(rows)


def _render_results(results: list[dict[str, Any]]) -> None:
    ok = [r for r in results if not r.get("error")]
    fail = [r for r in results if r.get("error")]
    cols = st.columns(3)
    cols[0].metric("Importiert", len(ok))
    cols[1].metric("Fehler", len(fail))
    cols[2].metric("Gesamt", len(results))

    if ok:
        st.success(f"✓ {len(ok)} Auftrag/Aufträge als Draft angelegt.")
        st.dataframe(
            pd.DataFrame([
                {
                    "Auftrag-Nr": r.get("order_number") or "",
                    "Kunde": r.get("customer_name") or "",
                    "Positionen": r.get("items_count") or 0,
                }
                for r in ok
            ]),
            use_container_width=True,
            hide_index=True,
        )
    if fail:
        st.error(f"✗ {len(fail)} Eintrag/Einträge konnten nicht importiert werden:")
        for r in fail:
            st.write(f"- **{r.get('customer_name')}**: `{r.get('error')}`")


def _step_extract(api_key: str, model: str) -> None:
    """Schritt 1: Quelle wählen + KI-Extraktion."""
    mode = st.radio(
        "Eingabe-Modus",
        ["✍️ Freitext / Liste", "📄 PDF-Upload", "📊 CSV-Upload"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if mode.startswith("✍️"):
        st.caption(
            "Paste hier alles rein, was offene Aufträge beschreibt — Email-Auszüge, "
            "Notizen, Excel-Zeilen. Trenne Aufträge mit Leerzeile oder `---`."
        )
        text = st.text_area("Text", height=300, label_visibility="collapsed", key="mig_text")
        if st.button("🤖 KI-Extraktion starten", type="primary", disabled=not text.strip()):
            with st.spinner("Gemini extrahiert Aufträge…"):
                batch = migration_ai.extract_batch_from_text(api_key=api_key, model=model, text=text)
            st.session_state["mig_parsed"] = _parse_form(batch.orders)
            st.session_state["mig_ai_notes"] = batch.notes
            st.rerun()

    elif mode.startswith("📄"):
        st.caption("Lade alte Auftragsbestätigungen oder Bestellungen als PDF hoch.")
        files = st.file_uploader(
            "PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key="mig_pdfs",
        )
        if st.button("🤖 KI-Extraktion starten", type="primary", disabled=not files):
            with st.spinner(f"Gemini analysiert {len(files)} PDF(s)…"):
                pdfs = [(f.name, f.getvalue()) for f in files]
                batch = migration_ai.extract_batch_from_pdfs(api_key=api_key, model=model, pdfs=pdfs)
            st.session_state["mig_parsed"] = _parse_form(batch.orders)
            st.session_state["mig_ai_notes"] = batch.notes
            st.rerun()

    else:  # CSV
        st.caption(
            "Upload eines sevDesk-Exports (oder beliebiger Tabelle mit Auftrags-Zeilen). "
            "Spalten dürfen frei benannt sein — Gemini interpretiert die Header."
        )
        csv_file = st.file_uploader(
            "CSV / TSV",
            type=["csv", "tsv", "txt"],
            label_visibility="collapsed",
            key="mig_csv",
        )
        if csv_file:
            try:
                preview_df = pd.read_csv(csv_file, sep=None, engine="python")
                st.dataframe(preview_df.head(10), use_container_width=True)
                st.caption(f"{len(preview_df)} Zeilen, {len(preview_df.columns)} Spalten")
                csv_file.seek(0)
            except Exception as e:
                st.warning(f"CSV-Vorschau fehlgeschlagen: {e}")
        if st.button("🤖 KI-Extraktion starten", type="primary", disabled=not csv_file):
            with st.spinner("Gemini interpretiert CSV…"):
                csv_file.seek(0)
                csv_text = csv_file.read().decode("utf-8", errors="replace")
                batch = migration_ai.extract_batch_from_csv_rows(
                    api_key=api_key, model=model, csv_text=csv_text
                )
            st.session_state["mig_parsed"] = _parse_form(batch.orders)
            st.session_state["mig_ai_notes"] = batch.notes
            st.rerun()


def _step_review(parsed: list[dict[str, Any]]) -> None:
    """Schritt 2: Preview mit Auswahl pro Zeile."""
    ai_notes = st.session_state.get("mig_ai_notes") or ""
    if ai_notes:
        st.info(f"🤖 KI-Hinweis: {ai_notes}")

    st.markdown(f"**{len(parsed)} Auftrag/Aufträge erkannt** — wähle aus, was importiert werden soll.")

    df = _summary_df(parsed)
    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        disabled=["#", "Konfidenz", "Positionen", "Summe (€)"],
        column_config={
            "✓": st.column_config.CheckboxColumn(width="small", help="Importieren?"),
            "#": st.column_config.NumberColumn(width="small"),
        },
        key="mig_editor",
    )

    with st.expander("🔍 Detail-Ansicht (alle Felder + Items)", expanded=False):
        for i, o in enumerate(parsed):
            with st.container(border=True):
                st.markdown(
                    f"**#{i + 1} — {o.get('customer_name') or '(?)'}** "
                    f"· {CONF_BADGE.get(o.get('confidence', 'medium'), '⚪')} {o.get('confidence', 'medium')}"
                )
                cols = st.columns(3)
                cols[0].caption(f"Email: `{o.get('customer_email') or '—'}`")
                cols[1].caption(f"USt-ID: `{o.get('customer_vat_id') or '—'}`")
                cols[2].caption(f"Bestell-Nr: `{o.get('customer_reference') or '—'}`")
                items = o.get("items") or []
                if items:
                    items_df = pd.DataFrame([
                        {
                            "Pos": it.get("pos_nr"),
                            "SKU": it.get("sku") or "",
                            "Bezeichnung": it.get("description") or "",
                            "Menge": it.get("qty"),
                            "Einheit": it.get("unit") or "Stk",
                            "Preis (€)": it.get("target_price_eur") or 0,
                        }
                        for it in items
                    ])
                    st.dataframe(items_df, use_container_width=True, hide_index=True)
                else:
                    st.caption("_(keine Positionen erkannt)_")

    selected = [parsed[i] for i, ok in enumerate(edited["✓"].tolist()) if ok and i < len(parsed)]
    cols = st.columns([1, 1, 2])
    if cols[0].button("⬅ Zurück", key="mig_back"):
        st.session_state.pop("mig_parsed", None)
        st.session_state.pop("mig_ai_notes", None)
        st.rerun()
    if cols[1].button(
        f"✓ {len(selected)} Auftrag/Aufträge importieren",
        type="primary",
        disabled=not selected,
        key="mig_import",
    ):
        with st.spinner(f"Lege {len(selected)} Draft(s) an…"):
            results = migration_to_beleg.import_orders_batch(selected)
        st.session_state["mig_results"] = results
        st.session_state.pop("mig_parsed", None)
        st.session_state.pop("mig_ai_notes", None)
        st.rerun()


def render() -> None:
    render_header(
        "Migration",
        subtitle="Offene Aufträge aus sevDesk übernehmen — KI parst und legt Drafts an.",
    )

    api_key, model = gemini_settings()

    results = st.session_state.get("mig_results")
    if results:
        _render_results(results)
        if st.button("🔄 Weitere Aufträge importieren", type="primary"):
            st.session_state.pop("mig_results", None)
            st.rerun()
        render_footer()
        return

    parsed = st.session_state.get("mig_parsed")
    if parsed:
        _step_review(parsed)
    else:
        _step_extract(api_key, model)

    render_footer()
