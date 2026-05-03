"""Side-by-Side Review-Modus: PDF links, editierbare Felder rechts.

Workflow:
  - User klickt im KI-Card auf '📝 Prüfen + bearbeiten'
  - Page wechselt in Review-Layout: PDF embedded + Form
  - User korrigiert Kunde, Bestellnr, Items
  - Save → ai_extracted_payload wird in DB überschrieben
  - Optional: direkt 'Auftrag anlegen' aus dem Review

Kein Auto-Save — explizite Aktion (kein Verlust durch versehentliches Wegklicken).
"""

from __future__ import annotations

import base64
from typing import Any

import pandas as pd
import streamlit as st

from core.db import supabase
from lib import imap_inbox, mail_to_beleg


def _load_primary_pdf(mail_row: dict[str, Any]) -> tuple[bytes | None, str | None]:
    atts = mail_row.get("attachments_meta") or []
    payload = mail_row.get("ai_extracted_payload") or {}
    primary_idx = (payload.get("classification") or {}).get("primary_attachment_index", -1)

    pdf_atts = [
        (i, a) for i, a in enumerate(atts)
        if (a.get("content_type") or "").lower() == "application/pdf" and a.get("storage_path")
    ]
    if not pdf_atts:
        return None, None

    chosen = pdf_atts[0]
    if 0 <= primary_idx < len(atts):
        for i, a in pdf_atts:
            if i == primary_idx:
                chosen = (i, a)
                break

    _, att = chosen
    try:
        data = supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(att["storage_path"])
        return data, att.get("filename") or "anhang.pdf"
    except Exception:
        return None, None


def _embed_pdf(pdf_bytes: bytes, height_px: int = 720) -> None:
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    st.markdown(
        f"""
        <object data="data:application/pdf;base64,{b64}#view=FitH"
                type="application/pdf"
                width="100%" height="{height_px}px"
                style="border: 1px solid #E4E4E7; border-radius: 8px;">
            <p>PDF kann nicht angezeigt werden — bitte herunterladen.</p>
        </object>
        """,
        unsafe_allow_html=True,
    )


def _save_extracted(mail_id: str, new_payload: dict[str, Any]) -> None:
    supabase().table("incoming_mails").update(
        {"ai_extracted_payload": new_payload}
    ).eq("id", mail_id).execute()


def render_review(mail_row: dict[str, Any]) -> None:
    """Side-by-Side Review-View. Aufruf statt der normalen Detail-Anzeige."""
    mail_id = mail_row["id"]
    payload = mail_row.get("ai_extracted_payload") or {}
    cat = mail_row.get("ai_category")

    if cat != "sales_order":
        st.info(
            "Side-by-Side-Review ist aktuell nur für Sales-Orders implementiert. "
            "Nutze die Standard-Ansicht für andere Mail-Typen."
        )
        if st.button("← Zurück zur Standard-Ansicht", key=f"review_back_{mail_id}"):
            st.session_state[f"review_mode_{mail_id}"] = False
            st.rerun()
        return

    so = payload.get("sales_order") or {}

    # Top-Bar: Zurück + Status
    top_a, top_b = st.columns([1, 4])
    if top_a.button("← Zurück", key=f"review_back_{mail_id}", use_container_width=True):
        st.session_state[f"review_mode_{mail_id}"] = False
        st.rerun()
    top_b.markdown(f"### 📝 Review · {mail_row.get('subject') or '(kein Betreff)'}")

    st.divider()

    pdf_bytes, pdf_name = _load_primary_pdf(mail_row)
    col_pdf, col_form = st.columns([3, 4])

    with col_pdf:
        st.caption(f"📎 {pdf_name or '(kein PDF)'}")
        if pdf_bytes:
            _embed_pdf(pdf_bytes, height_px=820)
            st.download_button(
                "💾 PDF herunterladen",
                data=pdf_bytes,
                file_name=pdf_name or f"mail_{mail_id[:8]}.pdf",
                mime="application/pdf",
                key=f"dl_review_{mail_id}",
                use_container_width=True,
            )
        else:
            st.warning("Keine PDF im Anhang gefunden.")

    with col_form:
        st.markdown("#### 🤖 KI-Extraktion (editierbar)")
        st.caption("Korrigiere Werte und speichere — danach kannst du den Auftrag anlegen.")

        with st.form(f"review_form_{mail_id}"):
            st.markdown("**Kunde**")
            c1, c2 = st.columns(2)
            customer_name = c1.text_input("Firma", value=so.get("customer_name") or "")
            customer_vat = c2.text_input("USt-IdNr.", value=so.get("customer_vat_id") or "")
            customer_email = c1.text_input("Email (Vertrieb)", value=so.get("customer_email") or "")
            customer_ref = c2.text_input("Bestell-Nr Kunde", value=so.get("customer_reference") or "")

            confirmation_email = c1.text_input(
                "AB an", value=so.get("confirmation_email") or "",
                help="Email für Auftragsbestätigung — wenn leer, geht AB an Vertriebs-Email.",
            )
            invoice_email = c2.text_input(
                "Rechnung an", value=so.get("invoice_email") or "",
                help="Email für die spätere Rechnung — wenn leer, geht sie an Vertriebs-Email.",
            )

            st.markdown("**Lieferadresse** (leer = Standard-Rechnungsadresse des Kunden)")
            da = so.get("delivery_address") or {}
            d1, d2 = st.columns(2)
            d_company = d1.text_input("Empfänger-Firma", value=da.get("company") or "")
            d_contact = d2.text_input("Ansprechpartner", value=da.get("contact_name") or "")
            d_street = d1.text_input("Straße + Nr", value=da.get("street") or "")
            d_street2 = d2.text_input("Adresszusatz", value=da.get("street_2") or "")
            d_zip = d1.text_input("PLZ", value=da.get("zip") or "")
            d_city = d2.text_input("Stadt", value=da.get("city") or "")
            d_country = st.text_input(
                "Ländercode", value=(da.get("country_code") or "DE")[:2].upper(),
                max_chars=2,
            )

            st.markdown("**Termine**")
            t1, t2 = st.columns(2)
            requested = t1.text_input(
                "Wunsch-Liefertermin (YYYY-MM-DD)",
                value=so.get("requested_delivery_date") or "",
            )
            t2.text_input("Konfidenz", value=so.get("confidence") or "medium", disabled=True)

            st.markdown("**Positionen**")
            items = so.get("items") or []
            items_df = pd.DataFrame([
                {
                    "Pos": int(it.get("pos_nr") or i + 1),
                    "SKU": it.get("sku") or "",
                    "Bezeichnung": it.get("description") or "",
                    "Menge": float(it.get("qty") or 0),
                    "Einheit": it.get("unit") or "Stk",
                    "Preis (€)": float(it.get("target_price_eur") or 0),
                }
                for i, it in enumerate(items)
            ])
            edited_items = st.data_editor(
                items_df,
                use_container_width=True,
                num_rows="dynamic",
                key=f"review_items_{mail_id}",
                column_config={
                    "Pos": st.column_config.NumberColumn(width="small", min_value=1, step=1),
                    "Menge": st.column_config.NumberColumn(format="%.2f", min_value=0.0),
                    "Preis (€)": st.column_config.NumberColumn(format="%.2f €", min_value=0.0),
                },
            )

            st.markdown("**Notizen**")
            notes = st.text_area("Notizen", value=so.get("notes") or "", height=100, label_visibility="collapsed")

            st.divider()
            sb1, sb2 = st.columns(2)
            do_save = sb1.form_submit_button(
                "💾 Korrekturen speichern", type="secondary", use_container_width=True
            )
            do_convert = sb2.form_submit_button(
                "✓ Speichern + Auftrag anlegen", type="primary", use_container_width=True
            )

        if do_save or do_convert:
            new_so = {
                "customer_name": customer_name.strip(),
                "customer_email": customer_email.strip(),
                "customer_vat_id": customer_vat.strip().upper().replace(" ", ""),
                "customer_reference": customer_ref.strip(),
                "confirmation_email": confirmation_email.strip(),
                "invoice_email": invoice_email.strip(),
                "delivery_address": {
                    "company": d_company.strip(),
                    "contact_name": d_contact.strip(),
                    "street": d_street.strip(),
                    "street_2": d_street2.strip(),
                    "zip": d_zip.strip(),
                    "city": d_city.strip(),
                    "country_code": (d_country or "DE").strip().upper()[:2],
                } if (d_street.strip() and d_city.strip()) else None,
                "requested_delivery_date": requested.strip(),
                "items": [
                    {
                        "pos_nr": int(row["Pos"]) if pd.notna(row.get("Pos")) else i + 1,
                        "sku": str(row.get("SKU") or "").strip(),
                        "description": str(row.get("Bezeichnung") or "").strip(),
                        "qty": float(row.get("Menge") or 0),
                        "unit": str(row.get("Einheit") or "Stk").strip() or "Stk",
                        "target_price_eur": float(row.get("Preis (€)") or 0),
                    }
                    for i, (_, row) in enumerate(edited_items.iterrows())
                    if str(row.get("Bezeichnung") or "").strip()
                ],
                "notes": notes.strip(),
                "confidence": so.get("confidence") or "medium",
            }
            new_payload = dict(payload)
            new_payload["sales_order"] = new_so
            _save_extracted(mail_id, new_payload)
            st.toast("Korrekturen gespeichert.", icon="💾")

            if do_convert:
                actor = (st.session_state.get("user") or {}).get("email")
                try:
                    with st.spinner("Auftrag wird angelegt …"):
                        order_id = mail_to_beleg.convert_mail_to_order(
                            mail_id=mail_id,
                            sales_order_payload=new_so,
                            mail_from_email=mail_row.get("from_email") or "",
                            actor_email=actor,
                        )
                    st.success(f"✓ Auftrag-Draft angelegt: `{order_id[:8]}…`")
                    st.session_state[f"review_mode_{mail_id}"] = False
                    st.rerun()
                except Exception as e:
                    st.error(f"Fehler bei Auftrags-Anlage: {e}")
            else:
                st.rerun()
