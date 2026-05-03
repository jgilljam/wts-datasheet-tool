"""Posteingang — vollwertiger Mail-Client + KI-Pipeline.

Layout:
  Linke Sidebar: Mailbox-Auswahl + Filter
  Hauptbereich:  Mail-Liste (oben) + Detail (unten, beim Auswählen)

Features:
  - 3 Mailboxen: sales@ / invoice@ / info@
  - Lesestatus, Sterne, Suche
  - HTML-Body sicher gerendert
  - Antworten-Button öffnet Mail-Modal pre-filled (Versand bleibt User-Klick)
  - KI-Pipeline auf sales@ + invoice@ (info@ ohne KI)
  - Auto-Refresh-Toggle
  - Convert-To-Beleg
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.utils import format_date

from lib import imap_inbox, mail, mail_pipeline, mail_to_beleg


# ============================================================
# Konstanten
# ============================================================

STATUS_LABELS = {
    "received": "📨 Eingegangen",
    "ai_processing": "🤖 KI läuft",
    "ai_classified": "🤖 KI fertig",
    "linked": "✅ Verknüpft",
    "ignored": "🗑 Ignoriert",
    "failed": "❌ Fehler",
    "archived": "📁 Archiviert",
}

CATEGORY_LABELS = {
    "sales_order": "🛒 Kunden-Bestellung",
    "po_acknowledgment": "📑 Auftragsbestätigung",
    "incoming_invoice": "📥 Eingangsrechnung",
    "reply": "↩️ Antwort",
    "other": "❓ Sonstiges",
    None: "—",
}

MAILBOX_OPTIONS = [
    ("sales", "🛒 sales@"),
    ("invoice", "📥 invoice@"),
    ("info", "ℹ️ info@"),
]


# ============================================================
# Daten-Layer
# ============================================================

def _list_mails(
    *,
    mailboxes: list[str] | None = None,
    only_unread: bool = False,
    only_starred: bool = False,
    statuses: list[str] | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = supabase().table("incoming_mails").select("*")
    if mailboxes:
        q = q.in_("mailbox", mailboxes)
    if only_unread:
        q = q.eq("read_status", "unread")
    if only_starred:
        q = q.eq("starred", True)
    if statuses:
        q = q.in_("status", statuses)
    if search:
        s = search.replace("%", r"\%")
        q = q.or_(
            f"subject.ilike.%{s}%,from_email.ilike.%{s}%,body_text.ilike.%{s}%"
        )
    return q.order("received_at", desc=True).limit(limit).execute().data or []


def _list_thread(thread_id: str) -> list[dict[str, Any]]:
    if not thread_id:
        return []
    return (
        supabase().table("incoming_mails")
        .select("id, subject, from_email, received_at, read_status, mailbox")
        .eq("thread_id", thread_id)
        .order("received_at", desc=False)
        .execute()
        .data
    ) or []


def _mark_read(mail_id: str, read: bool = True) -> None:
    supabase().table("incoming_mails").update(
        {"read_status": "read" if read else "unread"}
    ).eq("id", mail_id).execute()


def _toggle_starred(mail_id: str, current: bool) -> None:
    supabase().table("incoming_mails").update(
        {"starred": not current}
    ).eq("id", mail_id).execute()


# ============================================================
# Pull-Section
# ============================================================

def _render_pull_section() -> None:
    sales_ok = imap_inbox.has_credentials("sales")
    invoice_ok = imap_inbox.has_credentials("invoice")
    info_ok = imap_inbox.has_credentials("info")
    cfg = mail_pipeline.settings()

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        modes = []
        if cfg["auto_classify"]:
            modes.append("🤖 Auto-KI")
        if cfg["auto_convert"]:
            modes.append(f"⚡ Auto-Convert (≥{cfg['auto_convert_min_confidence']})")
        modes_str = " · ".join(modes) or "Nur manuell"
        st.caption(
            f"sales@ {'✅' if sales_ok else '⚠️'} · "
            f"invoice@ {'✅' if invoice_ok else '⚠️'} · "
            f"info@ {'✅' if info_ok else '⚠️'} · "
            f"{modes_str}"
        )
    with c4:
        if st.button(
            "↻ Versand-Status",
            use_container_width=True,
            help="Pollt Resend für ausgehende Mails (delivered/bounced/complained).",
        ):
            try:
                with st.spinner("Resend-Status …"):
                    res = mail.sync_outgoing_status(limit=50)
                st.toast(f"Versand: {res['updated']}/{res['checked']} aktualisiert", icon="📊")
            except mail.MailError as e:
                st.error(f"Sync fehlgeschlagen: {e}")
    with c2:
        if st.button(
            "📥 Mails abrufen",
            type="primary",
            use_container_width=True,
            disabled=not (sales_ok or invoice_ok or info_ok),
        ):
            spinner_label = "IMAP-Pull + KI läuft …" if cfg["auto_classify"] else "IMAP-Pull läuft …"
            with st.spinner(spinner_label):
                results = imap_inbox.pull_all_mailboxes()
            converted_count = 0
            new_total = 0
            for mb, res in results.items():
                if "error" in res:
                    st.error(f"{mb}@: {res['error']}")
                elif "skipped" in res:
                    pass
                else:
                    new_total += res.get("new", 0)
                    for pr in res.get("pipeline_results") or []:
                        if pr.get("auto_convert", {}).get("converted"):
                            converted_count += 1
            if new_total:
                st.toast(f"📥 {new_total} neue Mail{'s' if new_total != 1 else ''}", icon="📬")
            else:
                st.toast("Keine neuen Mails", icon="ℹ️")
            if converted_count:
                st.toast(f"⚡ {converted_count} Belege auto-converted", icon="✅")
            st.rerun()
    with c3:
        st.toggle(
            "⏱ Auto-Refresh (60s)",
            key="inbox_auto_refresh",
            help="Alle 60 Sekunden automatisch neu pullen.",
        )

    if not (sales_ok and invoice_ok):
        with st.popover("ℹ️ Setup-Hinweis"):
            st.markdown(
                "Trage in `.streamlit/secrets.toml` ein:\n"
                "```toml\n"
                'IMAP_SALES_USER = "sales@wts-trading.de"\n'
                'IMAP_SALES_PASSWORD = "..."\n'
                'IMAP_INVOICE_USER = "invoice@wts-trading.de"\n'
                'IMAP_INVOICE_PASSWORD = "..."\n'
                '# Optional:\n'
                'IMAP_INFO_USER = "info@wts-trading.de"\n'
                'IMAP_INFO_PASSWORD = "..."\n'
                "```"
            )

    if st.session_state.get("inbox_auto_refresh"):
        _auto_pull_fragment()


@st.fragment(run_every=60)
def _auto_pull_fragment() -> None:
    if not st.session_state.get("inbox_auto_refresh"):
        return
    try:
        results = imap_inbox.pull_all_mailboxes()
    except Exception:
        return
    new_total = sum(r.get("new", 0) for r in results.values() if isinstance(r, dict))
    if new_total > 0:
        st.toast(f"📥 {new_total} neue Mail{'s' if new_total != 1 else ''}", icon="📬")
        st.rerun()


# ============================================================
# HTML-Body sicher rendern
# ============================================================

_DANGEROUS_TAGS = re.compile(
    r"<\s*(script|style|iframe|object|embed|form|meta|link)[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_LONE_DANGEROUS_TAGS = re.compile(
    r"<\s*(script|style|iframe|object|embed|form|meta|link)[^>]*/?\s*>",
    re.IGNORECASE,
)
_EVENT_HANDLERS = re.compile(r"\son\w+\s*=\s*('[^']*'|\"[^\"]*\"|[^\s>]+)", re.IGNORECASE)
_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)


def _sanitize_html(raw: str) -> str:
    """Entfernt Script/Style/iFrame/Event-Handler/javascript:-URLs."""
    if not raw:
        return ""
    s = _DANGEROUS_TAGS.sub("", raw)
    s = _LONE_DANGEROUS_TAGS.sub("", s)
    s = _EVENT_HANDLERS.sub("", s)
    s = _JS_URL.sub("blocked:", s)
    return s


# ============================================================
# Mail-Liste
# ============================================================

def _render_filter_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.markdown("### 📬 Posteingang")

        # Mailbox-Auswahl als Pills
        mailbox_keys = [k for k, _ in MAILBOX_OPTIONS]
        mailbox_labels = {k: label for k, label in MAILBOX_OPTIONS}
        selected_mailboxes = st.multiselect(
            "Postfach",
            options=mailbox_keys,
            default=st.session_state.get("inbox_filter_mailboxes", ["sales", "invoice"]),
            format_func=lambda k: mailbox_labels[k],
            key="inbox_filter_mailboxes",
        )

        st.divider()
        st.markdown("**Filter**")
        only_unread = st.checkbox("Nur ungelesen", key="inbox_filter_unread")
        only_starred = st.checkbox("Nur ⭐ markiert", key="inbox_filter_starred")

        st.markdown("**Pipeline-Status**")
        statuses = st.multiselect(
            "Status",
            options=list(STATUS_LABELS.keys()),
            default=st.session_state.get("inbox_filter_statuses", []),
            format_func=lambda s: STATUS_LABELS.get(s, s),
            key="inbox_filter_statuses",
            label_visibility="collapsed",
        )

        st.divider()
        search = st.text_input(
            "🔍 Suche", key="inbox_search",
            placeholder="Betreff, Absender, Body …",
        )

    return {
        "mailboxes": selected_mailboxes,
        "only_unread": only_unread,
        "only_starred": only_starred,
        "statuses": statuses,
        "search": search,
    }


def _render_mail_list(filters: dict[str, Any]) -> str | None:
    rows = _list_mails(
        mailboxes=filters["mailboxes"] or None,
        only_unread=filters["only_unread"],
        only_starred=filters["only_starred"],
        statuses=filters["statuses"] or None,
        search=filters["search"] or None,
    )
    if not rows:
        st.info("Keine Mails passend zum Filter.")
        return None

    df_data = []
    for r in rows:
        atts = r.get("attachments_meta") or []
        unread = r.get("read_status") == "unread"
        starred = r.get("starred")
        cat = r.get("ai_category")
        df_data.append({
            "_id": r["id"],
            "": ("⭐" if starred else "") + ("🆕" if unread else ""),
            "Eingang": format_date(r.get("received_at")) or "—",
            "Postfach": next((label for k, label in MAILBOX_OPTIONS if k == r.get("mailbox")), r.get("mailbox") or ""),
            "Von": (r.get("from_name") or r.get("from_email") or "?")[:35],
            "Betreff": (r.get("subject") or "(kein Betreff)")[:70],
            "📎": len(atts) if atts else "",
            "Kategorie": CATEGORY_LABELS.get(cat, cat or "—"),
            "Status": STATUS_LABELS.get(r.get("status"), r.get("status") or ""),
        })
    df = pd.DataFrame(df_data).drop(columns=["_id"])
    sel = st.dataframe(
        df, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        key="inbox_table",
    )
    sel_idx = sel.get("selection", {}).get("rows", [])
    if not sel_idx:
        st.caption(f"{len(rows)} Mails — wähle eine Zeile für Details.")
        return None
    return rows[sel_idx[0]]["id"]


# ============================================================
# Detail-View
# ============================================================

def _render_detail(mail_id: str) -> None:
    mail_row = (
        supabase().table("incoming_mails").select("*").eq("id", mail_id).single().execute().data
    )
    if not mail_row:
        st.error("Mail nicht gefunden.")
        return

    # Beim Öffnen automatisch als gelesen markieren
    if mail_row.get("read_status") == "unread":
        _mark_read(mail_id, True)
        mail_row["read_status"] = "read"

    # Toolbar: Stern, Antworten, Archiv, Ignorieren, KI
    tb1, tb2, tb3, tb4, tb5, tb6 = st.columns([1, 1, 1, 1, 1, 2])
    starred = bool(mail_row.get("starred"))
    if tb1.button("⭐" if starred else "☆", help="Stern", key=f"star_{mail_id}", use_container_width=True):
        _toggle_starred(mail_id, starred)
        st.rerun()
    if tb2.button("↩️ Antworten", help="Antwort vorbereiten", key=f"reply_{mail_id}", use_container_width=True):
        st.session_state[f"reply_open_{mail_id}"] = True
    if tb3.button("🤖 KI", help="Klassifikation/Extraktion", key=f"ai_{mail_id}", use_container_width=True):
        with st.spinner("Gemini …"):
            _run_ai_classification(mail_row)
        st.rerun()
    if tb4.button("📁 Archiv", help="Archivieren", key=f"arch_{mail_id}", use_container_width=True):
        supabase().table("incoming_mails").update({"status": "archived"}).eq("id", mail_id).execute()
        st.rerun()
    if tb5.button("🗑 Ignorieren", help="Als ignoriert markieren", key=f"ign_{mail_id}", use_container_width=True):
        supabase().table("incoming_mails").update({"status": "ignored"}).eq("id", mail_id).execute()
        st.rerun()

    # Header
    st.markdown(f"### {mail_row.get('subject') or '(kein Betreff)'}")
    meta1, meta2, meta3 = st.columns(3)
    meta1.markdown(f"**Von:** `{mail_row.get('from_name') or ''} <{mail_row.get('from_email')}>`")
    meta2.markdown(f"**An:** `{mail_row.get('to_email')}`")
    meta3.markdown(f"**Eingang:** {format_date(mail_row.get('received_at')) or '—'}")

    cat = mail_row.get("ai_category")
    pill_text = (
        f"**Status:** {STATUS_LABELS.get(mail_row.get('status'), mail_row.get('status'))} · "
        f"**Kategorie:** {CATEGORY_LABELS.get(cat, cat or '—')}"
    )
    if mail_row.get("ai_confidence"):
        pill_text += f" · KI: `{mail_row['ai_confidence']}`"
    st.caption(pill_text)

    # Thread-Anzeige (wenn mehr als eine Mail im Thread)
    thread = _list_thread(mail_row.get("thread_id") or "")
    if len(thread) > 1:
        with st.expander(f"🧵 Thread ({len(thread)} Nachrichten)", expanded=False):
            for t in thread:
                marker = "▶ " if t["id"] == mail_id else "   "
                date = format_date(t.get("received_at")) or ""
                st.caption(f"{marker}{date} · {t.get('from_email', '?')} · {(t.get('subject') or '')[:60]}")

    # Antworten-Form (inline)
    if st.session_state.get(f"reply_open_{mail_id}"):
        _render_reply_form(mail_row)

    # Body + Anhänge
    body_col, side_col = st.columns([3, 2])

    with body_col:
        st.markdown("#### Inhalt")
        view_mode = st.radio(
            "Ansicht",
            ["📝 Text", "🌐 HTML"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"view_{mail_id}",
            index=1 if mail_row.get("body_html") else 0,
        )
        if view_mode == "🌐 HTML" and mail_row.get("body_html"):
            sanitized = _sanitize_html(mail_row["body_html"])
            st.components.v1.html(sanitized, height=500, scrolling=True)
        else:
            text = mail_row.get("body_text") or ""
            if not text and mail_row.get("body_html"):
                # HTML zu Text fallback
                text = re.sub(r"<[^>]+>", "", _sanitize_html(mail_row["body_html"]))
            st.text_area(
                "body_view",
                value=text or "(kein Inhalt)",
                height=500,
                label_visibility="collapsed",
                disabled=True,
                key=f"body_{mail_id}",
            )

    with side_col:
        atts = mail_row.get("attachments_meta") or []
        st.markdown(f"#### 📎 Anhänge ({len(atts)})")
        if not atts:
            st.caption("Keine Anhänge.")
        for att in atts:
            with st.container(border=True):
                st.markdown(f"**{att.get('filename') or '?'}**")
                size_kb = (att.get("size_bytes") or 0) // 1024
                st.caption(f"{att.get('content_type', '?')} · {size_kb} KB")
                if att.get("storage_path"):
                    if st.button(
                        "⬇ Laden",
                        key=f"loadatt_{mail_id}_{att['storage_path']}",
                        use_container_width=True,
                    ):
                        st.session_state[f"att_data_{att['storage_path']}"] = (
                            supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(att["storage_path"])
                        )
                    data = st.session_state.get(f"att_data_{att['storage_path']}")
                    if data:
                        st.download_button(
                            "💾 Speichern",
                            data=data,
                            file_name=att["filename"],
                            mime=att.get("content_type") or "application/octet-stream",
                            key=f"dlbtn_{mail_id}_{att['storage_path']}",
                            use_container_width=True,
                        )

        # KI-Ergebnis + Convert
        if mail_row.get("ai_extracted_payload"):
            st.markdown("#### 🤖 KI-Extraktion")
            with st.expander("Roh-JSON", expanded=False):
                st.json(mail_row["ai_extracted_payload"])
            if mail_row.get("linked_beleg_id"):
                bt = mail_row.get("linked_beleg_type")
                bid = mail_row["linked_beleg_id"][:8]
                st.success(f"✅ Verknüpft: {bt} `{bid}…`")
            else:
                _render_convert_buttons(mail_row)

        if mail_row.get("ai_error"):
            st.error(f"KI-Fehler: {mail_row['ai_error']}")


# ============================================================
# Antworten-Form (Versand strikt per Knopf)
# ============================================================

def _render_reply_form(mail_row: dict[str, Any]) -> None:
    """Inline-Antwort-Komposer. Versand öffnet das normale Mail-Modal (User klickt Senden)."""
    mail_id = mail_row["id"]
    with st.container(border=True):
        st.markdown("#### ↩️ Antworten")
        to_default = mail_row.get("reply_to") or mail_row.get("from_email") or ""
        subj_default = mail_row.get("subject") or ""
        if not subj_default.lower().startswith("re:"):
            subj_default = f"Re: {subj_default}"

        # Zitat des Original-Bodies
        orig_body = mail_row.get("body_text") or ""
        if not orig_body and mail_row.get("body_html"):
            orig_body = re.sub(r"<[^>]+>", "", _sanitize_html(mail_row["body_html"]))
        sent_date = format_date(mail_row.get("date_sent") or mail_row.get("received_at")) or ""
        from_disp = mail_row.get("from_email") or "?"
        quote = "\n\n".join("> " + ln for ln in orig_body.splitlines() if ln.strip())[:2000]
        body_default = (
            f"\n\nAm {sent_date} schrieb {from_disp}:\n\n{quote}"
        )

        with st.form(f"reply_form_{mail_id}"):
            to = st.text_input("An", value=to_default, key=f"reply_to_{mail_id}")
            subject = st.text_input("Betreff", value=subj_default, key=f"reply_subj_{mail_id}")
            body = st.text_area("Nachricht", value=body_default, height=300, key=f"reply_body_{mail_id}")
            c_send, c_cancel = st.columns(2)
            do_send = c_send.form_submit_button("📤 Senden", type="primary", use_container_width=True)
            do_cancel = c_cancel.form_submit_button("Abbrechen", use_container_width=True)

        if do_cancel:
            st.session_state[f"reply_open_{mail_id}"] = False
            st.rerun()
        if do_send:
            if not to or "@" not in to:
                st.error("Bitte gültige Empfänger-Email.")
                return
            if not subject:
                st.error("Bitte Betreff eingeben.")
                return
            try:
                me = (st.session_state.get("user") or {})
                result = mail.send_mail(
                    to_email=to,
                    subject=subject,
                    body_text=body,
                    reply_to=me.get("email"),
                    beleg_type="reply",
                    beleg_id=mail_id,
                    beleg_number=f"Reply to {mail_row.get('subject') or ''}"[:80],
                )
                st.success(f"📤 Antwort gesendet (Resend-ID: {result.get('resend_id')})")
                st.session_state[f"reply_open_{mail_id}"] = False
                st.rerun()
            except mail.MailError as e:
                st.error(f"Versand fehlgeschlagen: {e}")


# ============================================================
# KI-Klassifikation (delegiert an Pipeline)
# ============================================================

def _run_ai_classification(mail_row: dict[str, Any]) -> None:
    try:
        result = mail_pipeline.classify_and_extract(mail_row["id"])
    except Exception as e:
        st.error(f"KI-Fehler: {e}")
        return
    if result.get("status") == "failed":
        st.error(f"KI-Fehler: {result.get('ai_error') or 'unbekannt'}")
    else:
        st.success(f"KI-Analyse fertig: {result.get('ai_category')} ({result.get('ai_confidence')})")


# ============================================================
# Convert-Buttons (unverändert)
# ============================================================

def _render_convert_buttons(mail_row: dict[str, Any]) -> None:
    cat = mail_row.get("ai_category")
    payload = mail_row.get("ai_extracted_payload") or {}
    actor = (st.session_state.get("user") or {}).get("email")

    if cat == "sales_order":
        so = payload.get("sales_order")
        if not so:
            st.warning("Keine Sales-Order-Daten — KI erneut analysieren.")
            return
        items = so.get("items") or []
        st.caption(
            f"**Kunde:** {html.escape(str(so.get('customer_name') or '?'))} · "
            f"**Items:** {len(items)} · "
            f"**Konfidenz:** {so.get('confidence', '?')}"
        )
        if so.get("requested_delivery_date"):
            st.caption(f"**Wunsch-Liefertermin:** {so['requested_delivery_date']}")
        if st.button(
            "→ Auftrag (Draft) anlegen", type="primary",
            use_container_width=True, key=f"toorder_{mail_row['id']}",
        ):
            try:
                with st.spinner("Auftrag wird angelegt …"):
                    order_id = mail_to_beleg.convert_mail_to_order(
                        mail_id=mail_row["id"],
                        sales_order_payload=so,
                        mail_from_email=mail_row.get("from_email") or "",
                        actor_email=actor,
                    )
                st.success(f"✓ Auftrag-Draft: `{order_id[:8]}…`")
                st.rerun()
            except Exception as e:
                st.error(f"Fehler: {e}")

    elif cat == "incoming_invoice":
        ii = payload.get("incoming_invoice")
        if not ii:
            st.warning("Keine OCR-Daten — KI erneut analysieren.")
            return
        items = ii.get("items") or []
        st.caption(
            f"**Lieferant:** {html.escape(str(ii.get('supplier_name') or '?'))} · "
            f"**Rg-Nr:** {ii.get('invoice_number', '?')} · "
            f"**Brutto:** {ii.get('gross_total_eur', 0):.2f} € · "
            f"**Items:** {len(items)} · "
            f"**Konfidenz:** {ii.get('confidence', '?')}"
        )
        if st.button(
            "→ Eingangsrechnung anlegen", type="primary",
            use_container_width=True, key=f"toinv_{mail_row['id']}",
        ):
            try:
                atts = mail_row.get("attachments_meta") or []
                pdf_bytes = pdf_filename = None
                for att in atts:
                    if (att.get("content_type") or "").lower() == "application/pdf":
                        try:
                            pdf_bytes = supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(att["storage_path"])
                            pdf_filename = att.get("filename")
                            break
                        except Exception:
                            continue
                with st.spinner("Eingangsrechnung wird angelegt …"):
                    inv_id = mail_to_beleg.convert_mail_to_incoming_invoice(
                        mail_id=mail_row["id"],
                        parsed_invoice=ii,
                        pdf_bytes=pdf_bytes,
                        pdf_filename=pdf_filename,
                        actor_email=actor,
                    )
                st.success(f"✓ Eingangsrechnung: `{inv_id[:8]}…`")
                st.rerun()
            except Exception as e:
                st.error(f"Fehler: {e}")

    elif cat == "po_acknowledgment":
        st.caption("Auftragsbestätigung vom Lieferanten — wir suchen die zugehörige BE-Nr.")
        if st.button(
            "🔗 Mit unserer Bestellung verknüpfen",
            type="primary",
            use_container_width=True,
            key=f"linkpo_{mail_row['id']}",
        ):
            try:
                with st.spinner("BE-Nr wird gesucht …"):
                    res = mail_to_beleg.link_po_acknowledgment(
                        mail_id=mail_row["id"],
                        actor_email=actor,
                    )
                if res.get("linked"):
                    st.success(f"✓ Verknüpft mit PO **{res['po_number']}**")
                    st.rerun()
                else:
                    st.warning(f"Kein Match: {res.get('reason')}")
            except Exception as e:
                st.error(f"Fehler: {e}")

    else:
        st.caption("Keine Convert-Aktion für diese Kategorie.")


# ============================================================
# Entry
# ============================================================

def render() -> None:
    render_header(
        title="Posteingang",
        subtitle="Mail-Client + KI-Klassifikation für sales@ / invoice@ / info@.",
    )

    _render_pull_section()
    st.divider()

    filters = _render_filter_sidebar()
    selected_id = _render_mail_list(filters)
    if selected_id:
        st.divider()
        _render_detail(selected_id)

    render_footer()
