"""IMAP-Polling für info@ Postfach (IONOS).

Holt UNSEEN-Mails, persistiert in `incoming_mails` + Anhänge in Storage-Bucket
`mail-incoming`. Idempotent über `message_id` (RFC2822 Mail-Header).
Newsletter werden vor Insert gefiltert (List-Unsubscribe-Header + Heuristik).

IONOS-Standardwerte: imap.ionos.de:993 (IMAP+SSL).

Streamlit-Secrets erwartet:
    IMAP_INFO_USER     = "info@wts-trading.de"
    IMAP_INFO_PASSWORD = "..."
    # optional Override
    IMAP_INFO_HOST     = "imap.ionos.de"
    IMAP_INFO_PORT     = 993

Backwards-Compat: Falls IMAP_SALES_* oder IMAP_INVOICE_* gesetzt sind,
werden diese ebenfalls gepullt (für sauberen Übergang während Migration).
"""

from __future__ import annotations

import email
import email.utils
import hashlib
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from typing import Any

import streamlit as st

from core.db import supabase

ATTACHMENTS_BUCKET = "mail-incoming"
OUTGOING_ATTACHMENTS_BUCKET = "mail-outgoing"
DEFAULT_IMAP_HOST = "imap.ionos.de"
DEFAULT_IMAP_PORT = 993

# IONOS-Sent-Folder hat je nach Account-Setup unterschiedliche Namen
SENT_FOLDER_CANDIDATES = ("Sent", "INBOX.Sent", "Gesendet", "INBOX.Gesendet")

# Sent-Pull-Lookback: 90 Tage reicht für Quartals-Übersicht.
# Idempotenz über message_id verhindert Duplikate bei Re-Run.
SENT_LOOKBACK_DAYS = 90


# ============================================================
# Header-Decoder
# ============================================================

def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(txt.decode("utf-8", errors="replace"))
        else:
            out.append(txt)
    return "".join(out).strip()


def _parse_addr(raw: str | None) -> tuple[str, str]:
    if not raw:
        return "", ""
    name, addr = email.utils.parseaddr(raw)
    return _decode_header(name), addr.strip().lower()


# ============================================================
# Storage-Bucket für Mail-Anhänge
# ============================================================

def _ensure_bucket(bucket: str = ATTACHMENTS_BUCKET) -> None:
    try:
        supabase().storage.create_bucket(bucket, options={"public": False})
    except Exception:
        pass  # existiert bereits


# ============================================================
# Credentials
# ============================================================

def _imap_credentials(mailbox: str) -> tuple[str, int, str, str]:
    pfx = f"IMAP_{mailbox.upper()}"
    host = st.secrets.get(f"{pfx}_HOST", DEFAULT_IMAP_HOST)
    port = int(st.secrets.get(f"{pfx}_PORT", DEFAULT_IMAP_PORT))
    user = st.secrets.get(f"{pfx}_USER")
    password = st.secrets.get(f"{pfx}_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            f"IMAP-Zugang für '{mailbox}' fehlt. "
            f"Bitte {pfx}_USER + {pfx}_PASSWORD in .streamlit/secrets.toml setzen."
        )
    return host, port, user, password


def has_credentials(mailbox: str) -> bool:
    pfx = f"IMAP_{mailbox.upper()}"
    return bool(st.secrets.get(f"{pfx}_USER")) and bool(st.secrets.get(f"{pfx}_PASSWORD"))


# ============================================================
# Newsletter-Filter (RFC2369 + Heuristik)
# ============================================================

# Bekannte Marketing-/Newsletter-X-Mailer (lowercase Substring-Match)
_NEWSLETTER_XMAILER_HINTS = (
    "mailchimp", "sendgrid", "mailgun", "constantcontact", "campaignmonitor",
    "klaviyo", "hubspot", "amazon ses", "amazonses", "salesforce marketing",
    "rapidmail", "cleverreach", "sendinblue", "brevo", "newsletter2go",
    "activecampaign", "getresponse", "convertkit",
)

# Subject-Keywords die eindeutig Newsletter sind (lowercase).
# Bewusst eng gehalten — „% Rabatt" könnte ein legitimes Lieferanten-Angebot sein.
_NEWSLETTER_SUBJECT_HINTS = (
    "newsletter",
    "abmelden",
    "unsubscribe",
)


def _is_newsletter(msg: Message, from_email: str) -> tuple[bool, str | None]:
    """Heuristische Newsletter-/Marketing-Erkennung.

    Returns: (is_newsletter, reason). reason ist None wenn keine Newsletter.
    """
    # RFC2369: praktisch jeder echte Newsletter setzt List-Unsubscribe.
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True, "list-unsubscribe-header"

    # Bulk-Mail-Marker
    prec = (msg.get("Precedence") or "").strip().lower()
    if prec in {"bulk", "list", "junk"}:
        return True, f"precedence-{prec}"
    if (msg.get("Auto-Submitted") or "").strip().lower() not in {"", "no"}:
        return True, "auto-submitted"

    # X-Mailer-Heuristik (bekannte Marketing-Tools)
    xmailer = (msg.get("X-Mailer") or "").lower()
    for hint in _NEWSLETTER_XMAILER_HINTS:
        if hint in xmailer:
            return True, f"x-mailer:{hint}"

    # Sender-Pattern (no-reply, newsletter@*, noreply@*)
    local = (from_email or "").split("@", 1)[0].lower()
    if local in {"newsletter", "no-reply", "noreply", "donotreply", "do-not-reply"}:
        return True, f"sender-local:{local}"
    if "newsletter" in local or "marketing" in local:
        return True, f"sender-local:{local}"

    return False, None


# ============================================================
# Body + Attachment-Extraktion
# ============================================================

def _extract_text_html(msg: Message) -> tuple[str, str]:
    text = html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and not text:
                text = decoded
            elif ctype == "text/html" and not html:
                html = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html = decoded
            else:
                text = decoded
    return text, html


def _extract_attachments(msg: Message) -> list[dict[str, Any]]:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        filename = _decode_header(part.get_filename())
        # entweder explizites Attachment, oder inline mit Filename
        if "attachment" not in disp and not filename:
            continue
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        out.append({
            "filename": filename,
            "content_type": part.get_content_type(),
            "size_bytes": len(payload),
            "content_bytes": payload,
        })
    return out


def _store_attachments(
    mailbox: str,
    message_id: str,
    attachments: list[dict[str, Any]],
    *,
    bucket: str = ATTACHMENTS_BUCKET,
) -> list[dict[str, Any]]:
    if not attachments:
        return []
    _ensure_bucket(bucket)
    msg_hash = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]
    year = datetime.now(timezone.utc).strftime("%Y")
    out = []
    for idx, att in enumerate(attachments):
        safe_name = att["filename"].replace("/", "_").replace("\\", "_")[:120]
        path = f"{mailbox}/{year}/{msg_hash}/{idx:02d}_{safe_name}"
        try:
            supabase().storage.from_(bucket).upload(
                path=path,
                file=att["content_bytes"],
                file_options={
                    "content-type": att["content_type"],
                    "upsert": "true",
                },
            )
        except Exception:
            pass  # Best-effort — Metadata wird trotzdem persistiert
        out.append({
            "filename": att["filename"],
            "content_type": att["content_type"],
            "size_bytes": att["size_bytes"],
            "storage_path": path,
            "bucket": bucket,
            "sha256": hashlib.sha256(att["content_bytes"]).hexdigest(),
        })
    return out


# ============================================================
# Duplicate-Check
# ============================================================

def _existing_message_ids(message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()
    res = (
        supabase()
        .table("incoming_mails")
        .select("message_id")
        .in_("message_id", message_ids)
        .execute()
        .data
    ) or []
    return {r["message_id"] for r in res if r.get("message_id")}


def _existing_outgoing_message_ids(message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()
    res = (
        supabase()
        .table("outgoing_mails")
        .select("message_id")
        .in_("message_id", message_ids)
        .execute()
        .data
    ) or []
    return {r["message_id"] for r in res if r.get("message_id")}


# ============================================================
# Hauptfunktion: pull_mailbox
# ============================================================

def pull_mailbox(
    mailbox: str,
    *,
    limit: int = 50,
    mark_seen: bool = True,
    folder: str = "INBOX",
    run_pipeline: bool = True,
) -> dict[str, Any]:
    """Holt UNSEEN-Mails, persistiert sie in `incoming_mails`.

    Args:
        run_pipeline: nach Insert sofort mail_pipeline.process_new_mail aufrufen
                      (Auto-Klassifikation + ggf. Auto-Convert).

    Returns: dict mit {fetched, new, duplicates, errors, pipeline_results}.
    """
    host, port, user, password = _imap_credentials(mailbox)
    fetched = new = duplicates = errors = newsletters = 0
    pipeline_results: list[dict[str, Any]] = []

    M = imaplib.IMAP4_SSL(host, port)
    try:
        M.login(user, password)
        M.select(folder)
        typ, data = M.search(None, "UNSEEN")
        if typ != "OK":
            raise RuntimeError(f"IMAP SEARCH fehlgeschlagen: {typ}")
        uids = (data[0] or b"").split()
        uids = uids[-limit:]  # neueste zuerst
        if not uids:
            return {"fetched": 0, "new": 0, "duplicates": 0, "errors": 0}

        msg_blobs: dict[bytes, tuple[bytes, Message, str]] = {}
        message_ids_in_batch: list[str] = []
        for uid in uids:
            typ, msg_data = M.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                errors += 1
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            mid = (msg.get("Message-ID") or "").strip().strip("<>").strip()
            if not mid:
                mid = hashlib.sha256(raw[:2048]).hexdigest() + "@synth.wts"
            message_ids_in_batch.append(mid)
            msg_blobs[uid] = (raw, msg, mid)
            fetched += 1

        existing = _existing_message_ids(message_ids_in_batch)

        for uid, (raw, msg, mid) in msg_blobs.items():
            if mid in existing:
                duplicates += 1
                if mark_seen:
                    M.store(uid, "+FLAGS", "\\Seen")
                continue
            try:
                from_name, from_email = _parse_addr(msg.get("From"))
                _, to_email = _parse_addr(msg.get("To"))
                subject = _decode_header(msg.get("Subject"))

                # Newsletter-Filter VOR Insert (kein Token-Verbrauch, kein DB-Müll)
                is_nl, nl_reason = _is_newsletter(msg, from_email)
                if not is_nl:
                    # Subject-Heuristik als zweite Stufe (nur wenn keine harten Header-Marker)
                    subj_lower = (subject or "").lower()
                    for hint in _NEWSLETTER_SUBJECT_HINTS:
                        if hint in subj_lower:
                            is_nl, nl_reason = True, f"subject:{hint}"
                            break
                if is_nl:
                    newsletters += 1
                    if mark_seen:
                        M.store(uid, "+FLAGS", "\\Seen")
                    continue
                date_hdr = msg.get("Date")
                try:
                    date_sent = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
                    date_iso = date_sent.isoformat() if date_sent else None
                except Exception:
                    date_iso = None
                body_text, body_html = _extract_text_html(msg)
                attachments = _extract_attachments(msg)
                attachments_meta = _store_attachments(mailbox, mid, attachments)
                cc_raw = msg.get("Cc") or ""
                cc_list = None
                if cc_raw:
                    cc_list = [
                        a for _, a in [_parse_addr(p) for p in cc_raw.split(",")] if a
                    ] or None
                _, reply_to = _parse_addr(msg.get("Reply-To"))

                # Threading-Header
                in_reply_to = (msg.get("In-Reply-To") or "").strip().strip("<>").strip() or None
                refs_raw = (msg.get("References") or "").strip()
                references_ids: list[str] | None = None
                if refs_raw:
                    references_ids = [
                        r.strip().strip("<>").strip()
                        for r in refs_raw.split()
                        if r.strip()
                    ] or None
                # thread_id: erste Reference oder in_reply_to oder eigene Message-ID
                thread_id = (
                    (references_ids[0] if references_ids else None)
                    or in_reply_to
                    or mid
                )

                payload = {
                    "message_id": mid,
                    "imap_uid": int(uid.decode() if isinstance(uid, bytes) else uid),
                    "imap_folder": folder,
                    "mailbox": mailbox,
                    "from_email": from_email or "(unknown)",
                    "from_name": from_name or None,
                    "to_email": to_email or f"{mailbox}@wts-trading.de",
                    "cc_emails": cc_list,
                    "reply_to": reply_to or None,
                    "subject": subject or None,
                    "date_sent": date_iso,
                    "body_text": body_text or None,
                    "body_html": body_html or None,
                    "attachments_meta": attachments_meta,
                    "status": "received",
                    "in_reply_to": in_reply_to,
                    "references_ids": references_ids,
                    "thread_id": thread_id,
                }
                ins = supabase().table("incoming_mails").insert(payload).execute()
                new_mail_id = ins.data[0]["id"] if ins.data else None
                new += 1
                if mark_seen:
                    M.store(uid, "+FLAGS", "\\Seen")

                # Pipeline (Klassifikation + ggf. Auto-Convert) — lazy import
                if run_pipeline and new_mail_id:
                    try:
                        from . import mail_pipeline
                        pr = mail_pipeline.process_new_mail(new_mail_id)
                        pipeline_results.append(pr)
                    except Exception as pe:
                        pipeline_results.append({
                            "mail_id": new_mail_id,
                            "pipeline_error": str(pe)[:200],
                        })
            except Exception:
                errors += 1

        return {
            "fetched": fetched,
            "new": new,
            "duplicates": duplicates,
            "newsletters_filtered": newsletters,
            "errors": errors,
            "pipeline_results": pipeline_results,
        }
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass


# ============================================================
# Sent-Folder-Pull (versendete Mails → outgoing_mails)
# ============================================================

def _resolve_sent_folder(M: imaplib.IMAP4_SSL) -> str | None:
    """Findet den richtigen Sent-Folder-Namen via SELECT-Probe."""
    for candidate in SENT_FOLDER_CANDIDATES:
        typ, _ = M.select(candidate, readonly=True)
        if typ == "OK":
            return candidate
    return None


def pull_sent_folder(
    mailbox: str,
    *,
    lookback_days: int = SENT_LOOKBACK_DAYS,
    limit: int = 500,
) -> dict[str, Any]:
    """Pullt versendete Mails aus dem Sent-Folder in `outgoing_mails`.

    Im Gegensatz zu pull_mailbox():
    - Pullt ALLE Mails der letzten N Tage (nicht nur UNSEEN — Sent-Mails
      sind oft schon als Seen markiert).
    - Idempotent über message_id (unique index in outgoing_mails).
    - Markiert nichts (read-only auf Sent ist sauberer).
    - Schreibt in outgoing_mails mit source='imap_pull' + status='imap_received'.
    - Attachments in Bucket 'mail-outgoing'.
    - Klassifikation/Matching kommt später via separater Pipeline.

    Returns: dict mit {fetched, new, duplicates, errors, folder}.
    """
    host, port, user, password = _imap_credentials(mailbox)
    fetched = new = duplicates = errors = 0

    M = imaplib.IMAP4_SSL(host, port)
    try:
        M.login(user, password)
        folder = _resolve_sent_folder(M)
        if folder is None:
            return {
                "fetched": 0, "new": 0, "duplicates": 0, "errors": 0,
                "folder": None, "error": "Sent-Folder nicht gefunden",
            }
        # Erneut select, diesmal mit Schreibrechten falls nötig
        M.select(folder, readonly=True)

        # SINCE-Filter: nur Mails der letzten N Tage
        since_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f'(SINCE "{since_date}")')
        if typ != "OK":
            raise RuntimeError(f"IMAP SEARCH fehlgeschlagen: {typ}")
        uids = (data[0] or b"").split()
        uids = uids[-limit:]  # neueste zuerst
        if not uids:
            return {
                "fetched": 0, "new": 0, "duplicates": 0, "errors": 0,
                "folder": folder,
            }

        msg_blobs: dict[bytes, tuple[bytes, Message, str]] = {}
        message_ids_in_batch: list[str] = []
        for uid in uids:
            typ, msg_data = M.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                errors += 1
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            mid = (msg.get("Message-ID") or "").strip().strip("<>").strip()
            if not mid:
                mid = hashlib.sha256(raw[:2048]).hexdigest() + "@synth.wts"
            message_ids_in_batch.append(mid)
            msg_blobs[uid] = (raw, msg, mid)
            fetched += 1

        existing = _existing_outgoing_message_ids(message_ids_in_batch)

        for uid, (raw, msg, mid) in msg_blobs.items():
            if mid in existing:
                duplicates += 1
                continue
            try:
                from_name, from_email = _parse_addr(msg.get("From"))
                _, to_email = _parse_addr(msg.get("To"))
                subject = _decode_header(msg.get("Subject")) or "(ohne Betreff)"
                date_hdr = msg.get("Date")
                try:
                    date_sent = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
                    date_iso = date_sent.isoformat() if date_sent else None
                except Exception:
                    date_iso = None

                body_text, body_html = _extract_text_html(msg)
                attachments = _extract_attachments(msg)
                attachments_meta = _store_attachments(
                    mailbox, mid, attachments, bucket=OUTGOING_ATTACHMENTS_BUCKET,
                )

                cc_raw = msg.get("Cc") or ""
                cc_list: list[str] | None = None
                if cc_raw:
                    cc_list = [
                        a for _, a in [_parse_addr(p) for p in cc_raw.split(",")] if a
                    ] or None
                bcc_raw = msg.get("Bcc") or ""
                bcc_list: list[str] | None = None
                if bcc_raw:
                    bcc_list = [
                        a for _, a in [_parse_addr(p) for p in bcc_raw.split(",")] if a
                    ] or None
                _, reply_to = _parse_addr(msg.get("Reply-To"))

                in_reply_to = (msg.get("In-Reply-To") or "").strip().strip("<>").strip() or None
                refs_raw = (msg.get("References") or "").strip()
                references_ids: list[str] | None = None
                if refs_raw:
                    references_ids = [
                        r.strip().strip("<>").strip()
                        for r in refs_raw.split()
                        if r.strip()
                    ] or None
                thread_id = (
                    (references_ids[0] if references_ids else None)
                    or in_reply_to
                    or mid
                )

                payload = {
                    "source": "imap_pull",
                    "message_id": mid,
                    "imap_uid": int(uid.decode() if isinstance(uid, bytes) else uid),
                    "imap_folder": folder,
                    "mailbox": mailbox,
                    "to_email": to_email or "(unknown)",
                    "cc_emails": cc_list,
                    "bcc_emails": bcc_list,
                    "reply_to": reply_to or None,
                    "from_email": from_email or f"{mailbox}@wts-trading.de",
                    "subject": subject,
                    "body_preview": (body_text or "")[:500] or None,
                    "body_text": body_text or None,
                    "body_html": body_html or None,
                    "attachments_meta": attachments_meta,
                    "date_sent_hdr": date_iso,
                    "in_reply_to": in_reply_to,
                    "references_ids": references_ids,
                    "thread_id": thread_id,
                    "status": "imap_received",
                    # beleg_type / beleg_id bleiben null bis Klassifikation
                }
                supabase().table("outgoing_mails").insert(payload).execute()
                new += 1
            except Exception:
                errors += 1

        return {
            "fetched": fetched,
            "new": new,
            "duplicates": duplicates,
            "errors": errors,
            "folder": folder,
        }
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass


def pull_all_mailboxes() -> dict[str, dict[str, Any]]:
    """Pollt alle konfigurierten Postfächer (Inbox + Sent).

    Primärquelle ist info@ (alle Geschäftspost läuft faktisch dort ein).
    Newsletter werden in pull_mailbox() bereits vor DB-Insert gefiltert,
    daher kann info@ direkt durch die KI-Pipeline.

    Für info@ wird zusätzlich der Sent-Folder gepullt (→ outgoing_mails),
    um versendete Rechnungen + Bestellungen zu erfassen.

    sales@ / invoice@ werden nur gepullt wenn deren Secrets noch gesetzt
    sind (Backwards-Compat während Migration). Können nach Cut entfernt
    werden.
    """
    results: dict[str, dict[str, Any]] = {}
    for mb in ("info", "sales", "invoice"):
        if not has_credentials(mb):
            results[mb] = {"skipped": "no credentials"}
            continue
        try:
            inbox_res = pull_mailbox(mb, run_pipeline=True)
        except Exception as e:
            inbox_res = {"error": str(e)[:300]}
        # Sent-Pull nur für info@ — sales@/invoice@ sind reine Empfangs-Postfächer
        if mb == "info":
            try:
                sent_res = pull_sent_folder(mb)
                inbox_res["sent"] = sent_res
            except Exception as e:
                inbox_res["sent"] = {"error": str(e)[:300]}
        results[mb] = inbox_res
    return results
