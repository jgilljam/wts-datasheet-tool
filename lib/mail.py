"""Mail-Versand via Resend API + Audit in outgoing_mails.

API-Doku: https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from jinja2 import Template

try:
    import requests
except ImportError:
    import urllib.request
    import urllib.error
    requests = None  # type: ignore

from core.db import supabase


RESEND_ENDPOINT = "https://api.resend.com/emails"


# ============================================================
# Templates — Subject + Body pro Beleg-Typ (Jinja2)
# ============================================================

TEMPLATES: dict[str, dict[str, str]] = {
    "invoice": {
        "subject": "Rechnung {{ beleg_number }} — {{ company_name }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "im Anhang finden Sie unsere Rechnung {{ beleg_number }} "
            "vom {{ issued_at }}"
            "{% if customer_reference %} zu Ihrer Bestellung {{ customer_reference }}{% endif %}.\n\n"
            "{% if due_date %}Wir bitten um Begleichung bis {{ due_date }}.\n\n{% endif %}"
            "Bei Rückfragen stehen wir Ihnen gerne zur Verfügung.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
    "quotation": {
        "subject": "Angebot {{ beleg_number }} — {{ company_name }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "vielen Dank für Ihre Anfrage. Im Anhang finden Sie unser Angebot "
            "{{ beleg_number }}.\n\n"
            "{% if valid_until %}Das Angebot ist gültig bis {{ valid_until }}.\n\n{% endif %}"
            "Für Rückfragen oder eine Bestellung stehen wir Ihnen gerne zur Verfügung.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
    "order": {
        "subject": "Auftragsbestätigung {{ beleg_number }} — {{ company_name }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "vielen Dank für Ihren Auftrag. Im Anhang finden Sie unsere "
            "Auftragsbestätigung {{ beleg_number }}"
            "{% if customer_reference %} zu Ihrer Bestellung {{ customer_reference }}{% endif %}.\n\n"
            "Bei Rückfragen stehen wir Ihnen gerne zur Verfügung.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
    "po": {
        "subject": "Bestellung {{ beleg_number }} — {{ company_name }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "im Anhang finden Sie unsere Bestellung {{ beleg_number }}.\n\n"
            "Bitte bestätigen Sie uns den Auftrag mit verbindlichem Liefertermin.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
    "delivery": {
        "subject": "Lieferschein {{ beleg_number }} — {{ company_name }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "im Anhang finden Sie den Lieferschein {{ beleg_number }} zu Ihrer "
            "Sendung.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
    "dunning": {
        "subject": "Zahlungserinnerung — Rechnung {{ beleg_number }}",
        "body": (
            "Sehr geehrte Damen und Herren,\n\n"
            "leider konnten wir noch keinen Zahlungseingang zu unserer "
            "Rechnung {{ beleg_number }} verzeichnen.\n\n"
            "Bitte begleichen Sie den ausstehenden Betrag bis {{ due_date }}.\n\n"
            "Sollten Sie die Zahlung bereits veranlasst haben, betrachten Sie diese Mail als gegenstandslos.\n\n"
            "Mit freundlichen Grüßen\n"
            "{{ sender_name }}\n"
            "{{ company_name }}"
        ),
    },
}


def render_template(beleg_type: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """Rendert (subject, body) für einen Beleg-Typ. Fallback: 'other'."""
    tmpl = TEMPLATES.get(beleg_type) or {
        "subject": "{{ beleg_number }} — {{ company_name }}",
        "body": "Sehr geehrte Damen und Herren,\n\nim Anhang das Dokument.\n\nMit freundlichen Grüßen\n{{ sender_name }}\n{{ company_name }}",
    }
    subject = Template(tmpl["subject"]).render(**ctx)
    body = Template(tmpl["body"]).render(**ctx)
    return subject, body


# ============================================================
# Resend API-Client
# ============================================================

class MailError(Exception):
    """User-facing Mail-Fehler."""


def _api_key() -> str:
    key = st.secrets.get("RESEND_API_KEY")
    if not key:
        raise MailError(
            "RESEND_API_KEY ist nicht gesetzt. Mail-Versand ist erst möglich, "
            "wenn der Admin den Resend-API-Key in den App-Secrets eingetragen hat."
        )
    return key


def _post_resend(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrapper für Resend POST — nutzt requests wenn da, sonst urllib."""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    if requests is not None:
        r = requests.post(RESEND_ENDPOINT, json=payload, headers=headers, timeout=30)
        if r.status_code >= 400:
            raise MailError(f"Resend-Fehler {r.status_code}: {r.text[:300]}")
        return r.json()
    # Fallback urllib
    import json as _json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=_json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise MailError(f"Resend-Fehler {e.code}: {body}")


# ============================================================
# High-Level send_mail
# ============================================================

def send_mail(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    reply_to: str | None = None,
    from_email: str = "info@wts-trading.de",
    from_name: str = "WTS Trading & Service",
    attachments: list[dict[str, Any]] | None = None,
    beleg_type: str = "other",
    beleg_id: str | None = None,
    beleg_number: str | None = None,
) -> dict[str, Any]:
    """Sendet Mail via Resend + persistiert in outgoing_mails.

    Args:
        attachments: Liste {filename, content_bytes, storage_path?}.
                     content_bytes wird base64-encoded an Resend.

    Returns: Dict {mail_id, resend_id, status}.
    Wirft MailError bei API-Fehler (mit Audit-Eintrag status=failed).
    """
    sender_email = st.session_state.get("user_email") or from_email

    # 1. Audit-Eintrag im Status 'sending'
    audit_payload = {
        "beleg_type": beleg_type,
        "beleg_id": beleg_id,
        "beleg_number": beleg_number,
        "to_email": to_email,
        "cc_emails": cc_emails,
        "bcc_emails": bcc_emails,
        "reply_to": reply_to,
        "from_email": from_email,
        "subject": subject,
        "body_preview": (body_text or "")[:500],
        "body_html": body_html or body_text,
        "attachments_meta": (
            [
                {
                    "filename": a.get("filename"),
                    "storage_path": a.get("storage_path"),
                    "size_bytes": len(a.get("content_bytes") or b""),
                }
                for a in (attachments or [])
            ]
            if attachments else None
        ),
        "status": "sending",
        "sent_by": sender_email,
    }
    audit = supabase().table("outgoing_mails").insert(audit_payload).execute()
    mail_id = audit.data[0]["id"]

    # 2. Resend-Payload bauen
    payload: dict[str, Any] = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html
    if cc_emails:
        payload["cc"] = cc_emails
    if bcc_emails:
        payload["bcc"] = bcc_emails
    if reply_to:
        payload["reply_to"] = reply_to
    if attachments:
        payload["attachments"] = [
            {
                "filename": a["filename"],
                "content": base64.b64encode(a["content_bytes"]).decode("ascii"),
            }
            for a in attachments
            if a.get("content_bytes")
        ]

    # 3. POST + Audit-Update
    try:
        resp = _post_resend(payload)
    except MailError as e:
        supabase().table("outgoing_mails").update({
            "status": "failed",
            "error_message": str(e)[:500],
        }).eq("id", mail_id).execute()
        raise

    resend_id = resp.get("id")
    supabase().table("outgoing_mails").update({
        "status": "sent",
        "resend_message_id": resend_id,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", mail_id).execute()

    return {"mail_id": mail_id, "resend_id": resend_id, "status": "sent"}


# ============================================================
# Helper: Attachment aus Beleg-Storage holen
# ============================================================

# ============================================================
# Status-Polling: GET /emails/{id} — synct Resend-Status in outgoing_mails
# ============================================================

def _get_resend_email(resend_id: str) -> dict[str, Any]:
    """Holt einen einzelnen Mail-Status von Resend."""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = f"{RESEND_ENDPOINT}/{resend_id}"
    if requests is not None:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code >= 400:
            raise MailError(f"Resend GET {r.status_code}: {r.text[:200]}")
        return r.json()
    import json as _json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise MailError(f"Resend GET {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")


# Resend last_event → unsere status-Werte
_RESEND_EVENT_TO_STATUS = {
    "sent": "sent",
    "delivered": "delivered",
    "delivery_delayed": "sent",  # vorübergehend, kein Endstatus
    "bounced": "bounced",
    "complained": "complained",
    "opened": "delivered",       # impliziert delivered
    "clicked": "delivered",
    "failed": "failed",
}


def sync_outgoing_status(limit: int = 50) -> dict[str, int]:
    """Pollt Resend für alle outgoing_mails mit status='sent' und updatet Endstatus.

    Returns: {checked, updated, errors}
    """
    rows = (
        supabase()
        .table("outgoing_mails")
        .select("id, resend_message_id, status")
        .eq("status", "sent")
        .not_.is_("resend_message_id", "null")
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
        .data
    ) or []

    checked = updated = errors = 0
    for row in rows:
        checked += 1
        try:
            data = _get_resend_email(row["resend_message_id"])
        except MailError:
            errors += 1
            continue
        last_event = data.get("last_event") or "sent"
        new_status = _RESEND_EVENT_TO_STATUS.get(last_event, "sent")
        if new_status == row["status"]:
            continue
        update_payload: dict[str, Any] = {"status": new_status}
        if new_status == "delivered":
            update_payload["delivered_at"] = data.get("created_at") or datetime.now(timezone.utc).isoformat()
        elif new_status in {"bounced", "complained", "failed"}:
            err_summary = (data.get("last_event") or "").upper()
            update_payload["error_message"] = f"Resend last_event: {err_summary}"
        supabase().table("outgoing_mails").update(update_payload).eq("id", row["id"]).execute()
        updated += 1

    return {"checked": checked, "updated": updated, "errors": errors}


def attachment_from_storage(filename: str, storage_path: str) -> dict[str, Any]:
    """Lädt PDF aus Supabase Storage und packt als Attachment-Dict."""
    from .pdf_storage import download_pdf
    pdf_bytes = download_pdf(storage_path)
    return {
        "filename": filename,
        "content_bytes": pdf_bytes,
        "storage_path": storage_path,
    }


# ============================================================
# Helper: Default-Empfänger aus parties.contacts
# ============================================================

def default_recipient_for_party(party_id: str, role_hint: str = "Buchhaltung") -> str | None:
    """Findet die beste Default-Mail für eine Partei.

    Priorität:
      1. is_primary=true UND role enthält role_hint
      2. is_primary=true
      3. role enthält role_hint
      4. erste Adresse mit Email
    """
    if not party_id:
        return None
    contacts = (
        supabase()
        .table("contacts")
        .select("email, role, is_primary")
        .eq("party_id", party_id)
        .not_.is_("email", "null")
        .execute()
        .data
    ) or []
    if not contacts:
        return None

    # 1. Primary + role
    for c in contacts:
        if c.get("is_primary") and role_hint.lower() in (c.get("role") or "").lower():
            return c["email"]
    # 2. Primary
    for c in contacts:
        if c.get("is_primary"):
            return c["email"]
    # 3. role match
    for c in contacts:
        if role_hint.lower() in (c.get("role") or "").lower():
            return c["email"]
    # 4. erster
    return contacts[0]["email"]
