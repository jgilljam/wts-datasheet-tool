"""Wiederverwendbares Mail-Versand-Modal für alle Beleg-Typen.

Verwendung in einer Beleg-Page:

    from core.ui.mail_modal import render_mail_section

    render_mail_section(
        beleg_type="invoice",
        beleg_id=invoice["id"],
        beleg_number=invoice["invoice_number"],
        party_id=invoice["customer_id"],
        pdf_storage_path=invoice.get("pdf_storage_path"),
        template_ctx={
            "issued_at": _format_date(invoice.get("issued_at")),
            "due_date": _format_date(invoice.get("due_date")),
            "customer_reference": invoice.get("customer_reference"),
            ...
        },
    )
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from core.db import supabase
from lib import mail


def render_mail_section(
    *,
    beleg_type: str,
    beleg_id: str,
    beleg_number: str,
    party_id: str | None = None,
    pdf_storage_path: str | None = None,
    template_ctx: dict[str, Any] | None = None,
    button_label: str = "📧 Per Mail senden",
    only_when_locked: bool = True,
    is_locked: bool = True,
) -> None:
    """Rendert „Per Mail senden"-Button + Inline-Modal.

    Args:
        beleg_type: invoice / quotation / order / po / delivery / dunning
        beleg_id: UUID des Belegs
        beleg_number: Belegnummer (nur Anzeige)
        party_id: für Default-Empfänger-Lookup
        pdf_storage_path: Pfad im belege-Bucket — wird als Attachment angehängt
        template_ctx: zusätzliche Variablen für Body-Template-Rendering
        only_when_locked: nur erlauben wenn Beleg festgeschrieben ist
        is_locked: aktueller Lock-Status (wird übergeben, nicht selbst geprüft)
    """
    if only_when_locked and not is_locked:
        st.caption("Mail-Versand erst nach Festschreibung möglich.")
        return

    modal_key = f"mail_modal_{beleg_id}"

    # API-Key-Check fürs UX
    has_api_key = bool(st.secrets.get("RESEND_API_KEY"))

    col_btn, _ = st.columns([1, 3])
    with col_btn:
        if st.button(
            button_label,
            key=f"open_{modal_key}",
            use_container_width=True,
            disabled=not has_api_key,
            help=(None if has_api_key else "RESEND_API_KEY ist nicht gesetzt — Mail-Versand deaktiviert"),
        ):
            st.session_state[modal_key] = True
            st.rerun()

    if st.session_state.get(modal_key):
        _render_modal(
            modal_key=modal_key,
            beleg_type=beleg_type,
            beleg_id=beleg_id,
            beleg_number=beleg_number,
            party_id=party_id,
            pdf_storage_path=pdf_storage_path,
            template_ctx=template_ctx or {},
        )


def _render_modal(
    *,
    modal_key: str,
    beleg_type: str,
    beleg_id: str,
    beleg_number: str,
    party_id: str | None,
    pdf_storage_path: str | None,
    template_ctx: dict[str, Any],
) -> None:
    # Default-Empfänger laden (nur einmal pro Modal-Öffnung)
    state_recipient_key = f"{modal_key}_recipient"
    if state_recipient_key not in st.session_state:
        default = mail.default_recipient_for_party(party_id, "Buchhaltung") if party_id else ""
        st.session_state[state_recipient_key] = default or ""

    # Company-Settings für Sender-Name + Footer-Vars
    company = (
        supabase().table("company_settings")
        .select("legal_name, email").limit(1).execute().data
    )
    company = company[0] if company else {}

    me = st.session_state.get("user") or {}
    full_ctx = {
        "beleg_number": beleg_number,
        "company_name": company.get("legal_name") or "WTS Trading & Service",
        "sender_name": me.get("full_name") or me.get("email") or "WTS Trading",
        **template_ctx,
    }

    default_subject, default_body = mail.render_template(beleg_type, full_ctx)
    state_subject_key = f"{modal_key}_subject"
    state_body_key = f"{modal_key}_body"
    if state_subject_key not in st.session_state:
        st.session_state[state_subject_key] = default_subject
    if state_body_key not in st.session_state:
        st.session_state[state_body_key] = default_body

    with st.container(border=True):
        st.markdown(f"### 📧 Mail versenden — {beleg_number}")

        with st.form(f"form_{modal_key}"):
            to = st.text_input(
                "An",
                value=st.session_state[state_recipient_key],
                key=f"{modal_key}_to",
                help="Default aus Kontakten der Partei (Rolle Buchhaltung > primary > erste mit Email)",
            )
            cc_raw = st.text_input(
                "CC (optional, Komma-getrennt)",
                value="",
                key=f"{modal_key}_cc",
            )
            subject = st.text_input(
                "Betreff",
                value=st.session_state[state_subject_key],
                key=f"{modal_key}_subj",
            )
            body = st.text_area(
                "Nachricht",
                value=st.session_state[state_body_key],
                height=240,
                key=f"{modal_key}_body_in",
            )
            attach_pdf = st.checkbox(
                "PDF anhängen",
                value=bool(pdf_storage_path),
                disabled=not pdf_storage_path,
                help=(
                    "Beleg-PDF aus Storage" if pdf_storage_path
                    else "Kein PDF im Storage — bitte erst PDF generieren"
                ),
            )

            col_send, col_cancel = st.columns([1, 1])
            send = col_send.form_submit_button(
                "Senden", type="primary", use_container_width=True
            )
            cancel = col_cancel.form_submit_button(
                "Abbrechen", use_container_width=True
            )

        if cancel:
            for k in (modal_key, state_recipient_key, state_subject_key, state_body_key):
                st.session_state.pop(k, None)
            st.rerun()

        if send:
            if not to or "@" not in to:
                st.error("Bitte gültige Empfänger-Email eingeben.")
                return
            if not subject:
                st.error("Bitte Betreff eingeben.")
                return

            cc_list = [c.strip() for c in (cc_raw or "").split(",") if "@" in c]

            attachments = []
            if attach_pdf and pdf_storage_path:
                try:
                    attachments.append(
                        mail.attachment_from_storage(
                            filename=f"{beleg_number}.pdf",
                            storage_path=pdf_storage_path,
                        )
                    )
                except Exception as e:
                    st.error(f"PDF-Anhang konnte nicht geladen werden: {e}")
                    return

            try:
                result = mail.send_mail(
                    to_email=to,
                    subject=subject,
                    body_text=body,
                    cc_emails=cc_list or None,
                    reply_to=me.get("email"),
                    attachments=attachments or None,
                    beleg_type=beleg_type,
                    beleg_id=beleg_id,
                    beleg_number=beleg_number,
                )
            except mail.MailError as e:
                st.error(f"Versand fehlgeschlagen: {e}")
                return

            st.success(f"Mail gesendet an {to}. Resend-ID: {result.get('resend_id')}")
            for k in (modal_key, state_recipient_key, state_subject_key, state_body_key):
                st.session_state.pop(k, None)
            st.toast("📧 Mail versendet")
            st.rerun()
