"""Posteingang — Mail-Client + KI-Pipeline.

Design-Prinzipien:
  - **Eine Hauptaktion** pro Mail (Auftrag anlegen / Verknüpfen)
  - **Filter als Tabs** statt Sidebar — weniger kognitive Last
  - **KI-Karte prominent** — Roh-JSON nur auf Wunsch
  - **Body direkt sichtbar** — kein Text/HTML-Toggle
  - **Sekundäre Aktionen dezent** — Archiv/Ignorieren am Rand
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.utils import format_date, sanitize_search

from lib import imap_inbox, mail, mail_pipeline, mail_to_beleg

from . import review as review_mod


# ============================================================
# Konstanten
# ============================================================

CATEGORY_LABELS = {
    "sales_order": "🛒 Kunden-Bestellung",
    "po_acknowledgment": "📑 Auftragsbestätigung",
    "incoming_invoice": "📥 Eingangsrechnung",
    "reply": "↩️ Antwort",
    "other": "❓ Sonstiges",
    None: "—",
}

# Filter-Presets als Tabs (Reihenfolge = UI-Reihenfolge)
FILTER_TABS = [
    ("inbox", "📨 Eingang", {"exclude_status": ["archived", "ignored", "linked"]}),
    ("unread", "🆕 Ungelesen", {"only_unread": True, "exclude_status": ["archived", "ignored"]}),
    ("sales", "🛒 Bestellungen", {"category": "sales_order", "exclude_status": ["archived", "ignored"]}),
    ("invoices", "📥 Rechnungen", {"category": "incoming_invoice", "exclude_status": ["archived", "ignored"]}),
    ("starred", "⭐ Markiert", {"only_starred": True}),
    ("archive", "📁 Archiv", {"only_status": "archived"}),
]


# ============================================================
# Daten-Layer
# ============================================================

def _list_mails(
    *,
    only_unread: bool = False,
    only_starred: bool = False,
    category: str | None = None,
    exclude_status: list[str] | None = None,
    only_status: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = supabase().table("incoming_mails").select("*")
    if only_unread:
        q = q.eq("read_status", "unread")
    if only_starred:
        q = q.eq("starred", True)
    if category:
        q = q.eq("ai_category", category)
    if only_status:
        q = q.eq("status", only_status)
    if exclude_status:
        # Whitelist aller existierenden Stati abzüglich der ausgeschlossenen.
        # WICHTIG: 'failed' MUSS in der Default-Liste bleiben, sonst verschwinden
        # Mails mit KI-Crash komplett aus der UI.
        all_status = ["received", "ai_processing", "ai_classified", "linked", "failed"]
        from_status = [s for s in all_status if s not in exclude_status]
        if from_status:
            q = q.in_("status", from_status)
    if search:
        s = sanitize_search(search)
        if s:
            q = q.or_(
                f"subject.ilike.%{s}%,from_email.ilike.%{s}%,body_text.ilike.%{s}%"
            )
    return q.order("received_at", desc=True).limit(limit).execute().data or []


def _count_mails_per_filter() -> dict[str, int]:
    """Zählt Mails pro Filter-Tab (für Badge in Tab-Label)."""
    counts: dict[str, int] = {}
    for key, _, kwargs in FILTER_TABS:
        rows = _list_mails(**{**kwargs, "limit": 500})
        counts[key] = len(rows)
    return counts


def _list_thread(thread_id: str) -> list[dict[str, Any]]:
    if not thread_id:
        return []
    return (
        supabase().table("incoming_mails")
        .select("id, subject, from_email, received_at, read_status")
        .eq("thread_id", thread_id)
        .order("received_at", desc=False)
        .execute().data
    ) or []


def _mark_read(mail_id: str, read: bool = True) -> None:
    supabase().table("incoming_mails").update(
        {"read_status": "read" if read else "unread"}
    ).eq("id", mail_id).execute()


def _toggle_starred(mail_id: str, current: bool) -> None:
    supabase().table("incoming_mails").update(
        {"starred": not current}
    ).eq("id", mail_id).execute()


def _set_status(mail_id: str, status: str) -> None:
    supabase().table("incoming_mails").update({"status": status}).eq("id", mail_id).execute()


# ============================================================
# Top-Bar: Pull + Auto-Refresh + Suche
# ============================================================

def _render_topbar() -> None:
    info_ok = imap_inbox.has_credentials("info")

    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        st.text_input(
            "🔍",
            placeholder="Suche in Betreff, Absender, Body …",
            key="inbox_search",
            label_visibility="collapsed",
        )
    with c2:
        do_pull_new = st.button(
            "📥 Neue Mails",
            type="primary",
            use_container_width=True,
            disabled=not info_ok,
            help="Pullt nur ungelesene Mails (täglicher Use)",
        )
    with c3:
        do_pull_backfill = st.button(
            "📚 Backfill 90 Tage",
            use_container_width=True,
            disabled=not info_ok,
            help="Pullt ALLE Mails der letzten 90 Tage (auch gelesene). Idempotent — kein Duplikat-Risiko.",
        )
    if do_pull_new or do_pull_backfill:
        backfill_days = 90 if do_pull_backfill else 0
        spinner_label = (
            "Backfill 90 Tage läuft (kann etwas dauern, Gemini pro Mail) …"
            if do_pull_backfill else "IMAP + KI läuft …"
        )
        with st.spinner(spinner_label):
            results = imap_inbox.pull_all_mailboxes(backfill_days=backfill_days)
            new_total = sum(
                r.get("new", 0) for r in results.values() if isinstance(r, dict)
            )
            nl_total = sum(
                r.get("newsletters_filtered", 0)
                for r in results.values() if isinstance(r, dict)
            )
            sent_total = sum(
                (r.get("sent") or {}).get("new", 0)
                for r in results.values() if isinstance(r, dict)
            )
            converted = sum(
                1
                for r in results.values()
                if isinstance(r, dict)
                for pr in (r.get("pipeline_results") or [])
                if pr.get("auto_convert", {}).get("converted")
            )
            if new_total:
                st.toast(f"📥 {new_total} neue Mail{'s' if new_total != 1 else ''}", icon="✅")
            else:
                st.toast("Keine neuen Mails", icon="ℹ️")
            if nl_total:
                st.toast(f"🗑️ {nl_total} Newsletter gefiltert", icon="ℹ️")
            if sent_total:
                st.toast(f"📤 {sent_total} versendete Mail{'s' if sent_total != 1 else ''}", icon="✅")
            if converted:
                st.toast(f"⚡ {converted} Belege auto-converted", icon="✨")
            st.rerun()
    with c4:
        st.toggle(
            "⏱ Auto",
            key="inbox_auto_refresh",
            help="Alle 60 sec automatisch pullen.",
        )

    if not info_ok:
        st.info(
            "ℹ️ IMAP-Login fehlt — trag IMAP_INFO_USER + IMAP_INFO_PASSWORD "
            "in Streamlit Cloud Secrets ein (alle Geschäftspost läuft über info@)."
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
# HTML sicher rendern
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
    if not raw:
        return ""
    s = _DANGEROUS_TAGS.sub("", raw)
    s = _LONE_DANGEROUS_TAGS.sub("", s)
    s = _EVENT_HANDLERS.sub("", s)
    s = _JS_URL.sub("blocked:", s)
    return s


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"<[^>]+>", "", _sanitize_html(html))


# ============================================================
# Mail-Liste (kompakt)
# ============================================================

def _render_filter_tabs() -> dict[str, Any]:
    """Filter-Tabs mit Counts. Returns die aktiven Filter-kwargs."""
    counts = _count_mails_per_filter()
    labels = [f"{label} ({counts.get(key, 0)})" for key, label, _ in FILTER_TABS]
    keys = [k for k, _, _ in FILTER_TABS]

    chosen = st.radio(
        "Filter",
        options=keys,
        format_func=lambda k: labels[keys.index(k)],
        horizontal=True,
        label_visibility="collapsed",
        key="inbox_active_filter",
    )
    # WICHTIG: Kopie statt Referenz — sonst wird die Modul-Konstante FILTER_TABS
    # mutiert und die Tab-Counts zählen ab dem 2. Render mit Suchterm.
    kwargs = dict(next((kw for k, _, kw in FILTER_TABS if k == chosen), {}))
    kwargs["search"] = st.session_state.get("inbox_search") or None
    return kwargs


def _render_mail_list(filter_kwargs: dict[str, Any]) -> str | None:
    rows = _list_mails(**filter_kwargs)
    if not rows:
        st.info("📭 Keine Mails in diesem Filter.")
        return None

    df_data = []
    for r in rows:
        atts = r.get("attachments_meta") or []
        unread = r.get("read_status") == "unread"
        starred = r.get("starred")
        cat = r.get("ai_category")
        marker = ("⭐ " if starred else "") + ("🆕 " if unread else "")
        from_disp = (r.get("from_name") or r.get("from_email") or "?")[:32]
        subject = r.get("subject") or "(kein Betreff)"
        df_data.append({
            "": marker.strip(),
            "Von": from_disp,
            "Betreff": subject[:65],
            "Datum": format_date(r.get("received_at")) or "—",
            "Kategorie": CATEGORY_LABELS.get(cat, "—"),
            "📎": len(atts) if atts else "",
        })
    df = pd.DataFrame(df_data)
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="inbox_table",
    )
    sel_idx = sel.get("selection", {}).get("rows", [])
    if not sel_idx:
        st.caption(f"💡 {len(rows)} Mail{'s' if len(rows) != 1 else ''} — wähle eine Zeile für Details.")
        return None
    return rows[sel_idx[0]]["id"]


# ============================================================
# Detail — fokussiert auf Hauptaktion
# ============================================================

def _render_detail(mail_id: str) -> None:
    mail_row = (
        supabase().table("incoming_mails").select("*").eq("id", mail_id).maybe_single().execute().data
    )
    if not mail_row:
        st.error("Mail nicht gefunden.")
        return

    # Review-Mode (Side-by-Side PDF + editierbare Felder)
    if st.session_state.get(f"review_mode_{mail_id}"):
        review_mod.render_review(mail_row)
        return

    if mail_row.get("read_status") == "unread":
        _mark_read(mail_id, True)
        mail_row["read_status"] = "read"

    # Header — Subject + dezente Meta-Zeile
    st.markdown(f"## {mail_row.get('subject') or '(kein Betreff)'}")
    received = format_date(mail_row.get("received_at")) or "—"
    from_disp = mail_row.get("from_name") or mail_row.get("from_email") or "?"
    from_email = mail_row.get("from_email") or ""
    starred = bool(mail_row.get("starred"))
    star_icon = "⭐" if starred else "☆"

    h1, h2 = st.columns([5, 1])
    h1.caption(f"**{from_disp}** `<{from_email}>` · {received} · an `{mail_row.get('to_email')}`")
    if h2.button(f"{star_icon} Markieren", key=f"star_{mail_id}", use_container_width=True):
        _toggle_starred(mail_id, starred)
        st.rerun()

    st.divider()

    # === KI-Karte (oberste Priorität wenn klassifiziert) ===
    if mail_row.get("ai_extracted_payload"):
        _render_ai_card(mail_row)
    elif mail_row.get("status") in ("received", "failed"):
        # KI noch nicht gelaufen — Button zum Triggern
        cc1, cc2 = st.columns([3, 1])
        cc1.caption("🤖 Noch nicht von der KI analysiert.")
        if cc2.button("KI starten", type="primary", use_container_width=True, key=f"start_ai_{mail_id}"):
            with st.spinner("Gemini …"):
                _trigger_ai(mail_row)
            st.rerun()

    if mail_row.get("ai_error"):
        st.error(f"KI-Fehler: {mail_row['ai_error']}")

    st.divider()

    # === Inhalt + Anhänge vertikal gestapelt (Mail-Client-Stil) ===
    st.markdown("#### 📄 Inhalt")
    body = mail_row.get("body_text") or _html_to_text(mail_row.get("body_html") or "")
    st.text_area(
        "body",
        value=body or "(leer)",
        height=280,
        label_visibility="collapsed",
        disabled=True,
        key=f"body_{mail_id}",
    )

    atts = mail_row.get("attachments_meta") or []
    if atts:
        st.markdown(f"#### 📎 Anhänge ({len(atts)})")
        for att in atts:
            with st.container(border=True):
                a1, a2 = st.columns([3, 2])
                with a1:
                    st.markdown(f"**{att.get('filename') or '?'}**")
                    size_kb = (att.get("size_bytes") or 0) // 1024
                    st.caption(f"{att.get('content_type', '?')} · {size_kb} KB")
                with a2:
                    if att.get("storage_path"):
                        # Direkt-Download: Bytes werden bei Render geladen,
                        # damit ein Klick reicht (statt 2-Klick "Download + Speichern")
                        cache_key = f"att_data_{att['storage_path']}"
                        if cache_key not in st.session_state:
                            try:
                                st.session_state[cache_key] = (
                                    supabase().storage
                                    .from_(imap_inbox.ATTACHMENTS_BUCKET)
                                    .download(att["storage_path"])
                                )
                            except Exception as e:
                                st.session_state[cache_key] = None
                                st.caption(f"⚠️ Lade-Fehler: {str(e)[:50]}")
                        data = st.session_state.get(cache_key)
                        if data:
                            st.download_button(
                                "⬇ Download",
                                data=data,
                                file_name=att["filename"],
                                mime=att.get("content_type") or "application/octet-stream",
                                key=f"dlbtn_{mail_id}_{att['storage_path']}",
                                use_container_width=True,
                            )

    # === Thread (wenn Replies vorhanden) ===
    thread = _list_thread(mail_row.get("thread_id") or "")
    if len(thread) > 1:
        with st.expander(f"🧵 Thread mit {len(thread)} Nachrichten", expanded=False):
            for t in thread:
                marker = "▶ " if t["id"] == mail_id else "  "
                date = format_date(t.get("received_at")) or ""
                st.caption(f"{marker}{date} · {t.get('from_email', '?')} · {(t.get('subject') or '')[:60]}")

    st.divider()

    # === Sekundäre Aktionen (dezent) ===
    a1, a2, a3, a4 = st.columns(4)
    if a1.button("↩️ Antworten", use_container_width=True, key=f"reply_{mail_id}"):
        st.session_state[f"reply_open_{mail_id}"] = True
    if a2.button("🤖 Neu analysieren", use_container_width=True, key=f"reai_{mail_id}", help="KI nochmal laufen lassen"):
        with st.spinner("Gemini …"):
            _trigger_ai(mail_row)
        st.rerun()
    if a3.button("📁 Archivieren", use_container_width=True, key=f"arch_{mail_id}"):
        _set_status(mail_id, "archived")
        st.rerun()
    if a4.button("🗑 Ignorieren", use_container_width=True, key=f"ign_{mail_id}"):
        _set_status(mail_id, "ignored")
        st.rerun()

    if st.session_state.get(f"reply_open_{mail_id}"):
        _render_reply_form(mail_row)


# ============================================================
# KI-Karte — die WICHTIGSTE UI, prominent
# ============================================================

def _render_ai_card(mail_row: dict[str, Any]) -> None:
    payload = mail_row.get("ai_extracted_payload") or {}
    cat = mail_row.get("ai_category")
    conf = mail_row.get("ai_confidence") or "medium"

    # Bei verlinktem Beleg: kompakte Erfolgs-Karte
    if mail_row.get("linked_beleg_id"):
        bt = mail_row.get("linked_beleg_type") or "?"
        bid = mail_row["linked_beleg_id"]
        st.success(
            f"✅ **Verknüpft mit {bt.replace('_', ' ').title()}** · `{bid[:8]}…` "
            f"(KI: {CATEGORY_LABELS.get(cat, cat)} / {conf})"
        )
        return

    with st.container(border=True):
        # Confidence-Badge
        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
        st.markdown(
            f"#### 🤖 KI-Analyse · {CATEGORY_LABELS.get(cat, '—')} "
            f"· {conf_emoji} {conf.title()}-Konfidenz"
        )

        if cat == "sales_order":
            _render_sales_order_card(mail_row, payload)
        elif cat == "incoming_invoice":
            _render_invoice_card(mail_row, payload)
        elif cat == "po_acknowledgment":
            _render_po_ack_card(mail_row)
        elif cat == "reply":
            st.caption("↩️ Antwort auf eine unserer Mails — keine Auto-Aktion.")
        else:
            st.caption("❓ Sonstige Mail — keine Auto-Aktion.")

        # Validation-Warnings
        warnings = payload.get("validation_warnings") or []
        if warnings:
            with st.expander(f"⚠️ {len(warnings)} Hinweis(e)", expanded=conf != "high"):
                for w in warnings:
                    st.warning(w.get("msg") or w.get("type") or "?")

        # Roh-JSON nur für Debug-Power-User
        with st.expander("🔍 Roh-Daten (Debug)", expanded=False):
            st.json(payload)


def _render_sales_order_card(mail_row: dict[str, Any], payload: dict[str, Any]) -> None:
    so = payload.get("sales_order")
    if not so:
        st.warning("Keine strukturierten Bestelldaten — bitte erneut analysieren.")
        return

    items = so.get("items") or []
    customer = so.get("customer_name") or "?"
    cust_ref = so.get("customer_reference")
    requested = so.get("requested_delivery_date")

    # Prominente Zusammenfassung
    cols = st.columns(3)
    cols[0].metric("Kunde", customer[:30])
    cols[1].metric("Positionen", len(items))
    if requested:
        cols[2].metric("Wunschtermin", requested)

    # Items kompakt
    if items:
        items_df = pd.DataFrame([
            {
                "Pos": it.get("pos_nr", "?"),
                "SKU": it.get("sku") or "—",
                "Bezeichnung": (it.get("description") or "")[:50],
                "Menge": f"{it.get('qty', 0)} {it.get('unit', 'Stk')}",
                "Preis": f"{(it.get('target_price_eur') or 0):.2f} €",
            }
            for it in items
        ])
        st.dataframe(items_df, use_container_width=True, hide_index=True)

    if cust_ref:
        st.caption(f"Kunden-Bestell-Nr: `{cust_ref}`")

    actor = (st.session_state.get("user") or {}).get("email")
    btn_review, btn_create = st.columns(2)
    if btn_review.button(
        "📝 Prüfen & bearbeiten",
        use_container_width=True,
        key=f"toreview_{mail_row['id']}",
        help="Side-by-Side: PDF links, editierbare Felder rechts.",
    ):
        st.session_state[f"review_mode_{mail_row['id']}"] = True
        st.rerun()
    if btn_create.button(
        "→ Auftrag (Draft) anlegen",
        type="primary",
        use_container_width=True,
        key=f"toorder_{mail_row['id']}",
    ):
        try:
            with st.spinner("Auftrag wird angelegt …"):
                order_id = mail_to_beleg.convert_mail_to_order(
                    mail_id=mail_row["id"],
                    sales_order_payload=so,
                    mail_from_email=mail_row.get("from_email") or "",
                    actor_email=actor,
                )
            st.success(f"✓ Auftrag-Draft angelegt: `{order_id[:8]}…`")
            st.rerun()
        except Exception as e:
            st.error(f"Fehler: {e}")


def _render_invoice_card(mail_row: dict[str, Any], payload: dict[str, Any]) -> None:
    ii = payload.get("incoming_invoice")
    if not ii:
        st.warning("Keine OCR-Daten — bitte erneut analysieren.")
        return

    items = ii.get("items") or []

    cols = st.columns(3)
    cols[0].metric("Lieferant", (ii.get("supplier_name") or "?")[:25])
    cols[1].metric("Brutto", f"{(ii.get('gross_total_eur') or 0):.2f} €")
    cols[2].metric("Positionen", len(items))

    rg = ii.get("invoice_number")
    dt = ii.get("invoice_date")
    if rg:
        st.caption(f"Rechnung: `{rg}` · Datum: {dt}")

    actor = (st.session_state.get("user") or {}).get("email")
    if st.button(
        "→ Eingangsrechnung anlegen",
        type="primary",
        use_container_width=True,
        key=f"toinv_{mail_row['id']}",
    ):
        try:
            atts = mail_row.get("attachments_meta") or []
            pdf_bytes = pdf_filename = None
            primary_idx = (payload.get("classification") or {}).get("primary_attachment_index", -1)
            for i, att in enumerate(atts):
                if (att.get("content_type") or "").lower() != "application/pdf":
                    continue
                if 0 <= primary_idx and i != primary_idx:
                    continue
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


def _render_po_ack_card(mail_row: dict[str, Any]) -> None:
    st.caption("Lieferant bestätigt eine unserer Bestellungen.")
    actor = (st.session_state.get("user") or {}).get("email")
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


# ============================================================
# Reply-Form
# ============================================================

def _render_reply_form(mail_row: dict[str, Any]) -> None:
    mail_id = mail_row["id"]
    with st.container(border=True):
        st.markdown("### ↩️ Antworten")
        to_default = mail_row.get("reply_to") or mail_row.get("from_email") or ""
        subj_default = mail_row.get("subject") or ""
        if not subj_default.lower().startswith("re:"):
            subj_default = f"Re: {subj_default}"

        orig_body = mail_row.get("body_text") or _html_to_text(mail_row.get("body_html") or "")
        sent_date = format_date(mail_row.get("date_sent") or mail_row.get("received_at")) or ""
        from_disp = mail_row.get("from_email") or "?"
        quote = "\n\n".join("> " + ln for ln in orig_body.splitlines() if ln.strip())[:2000]
        body_default = f"\n\nAm {sent_date} schrieb {from_disp}:\n\n{quote}"

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


def _trigger_ai(mail_row: dict[str, Any]) -> None:
    try:
        result = mail_pipeline.classify_and_extract(mail_row["id"])
    except Exception as e:
        st.error(f"KI-Fehler: {e}")
        return
    if result and result.get("status") == "failed":
        st.error(f"KI-Fehler: {result.get('ai_error') or 'unbekannt'}")
    else:
        st.toast("KI fertig", icon="✅")


# ============================================================
# Entry
# ============================================================

def render() -> None:
    render_header(
        title="Posteingang",
        subtitle="Mails aus info@ — Newsletter werden gefiltert, KI klassifiziert automatisch.",
    )

    _render_topbar()
    st.divider()

    filter_kwargs = _render_filter_tabs()

    # 2-Spalten-Layout wie ein klassischer Mail-Client:
    # - Links: Mail-Liste (kompakter)
    # - Rechts: Detail-View der ausgewählten Mail
    col_list, col_detail = st.columns([2, 3], gap="medium")

    with col_list:
        selected_id = _render_mail_list(filter_kwargs)

    with col_detail:
        if selected_id:
            _render_detail(selected_id)
        else:
            st.info("👈 Wähle eine Mail aus der Liste links.")

    render_footer()
