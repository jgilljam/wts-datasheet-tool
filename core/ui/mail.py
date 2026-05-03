"""mailto-Helper: pragmatische E-Mail-Hilfe ohne SMTP-Backend.

Streamlit kann keine PDFs als Anhang per mailto anhängen (Browser-/OS-
Limitation). Pragmatischer Workflow:
  1. Mitarbeiter klickt „PDF generieren" → Download-Button erscheint
  2. Klick „Per E-Mail senden" öffnet Mail-Programm mit vorbefülltem
     Subject + Body (Empfänger optional)
  3. Mitarbeiter zieht das gerade heruntergeladene PDF als Anhang rein
"""

from __future__ import annotations

import urllib.parse

import streamlit as st


def render_mail_link(
    *,
    to: str | None,
    subject: str,
    body: str,
    button_label: str = "✉ Per E-Mail senden",
    caption: str | None = "ℹ️ Mail-Programm öffnet sich. PDF bitte als Anhang manuell hinzufügen.",
) -> None:
    """Rendert einen mailto-Link als Streamlit-Link-Button."""
    qs = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    href = f"mailto:{to or ''}?{qs}"
    st.link_button(button_label, href, use_container_width=True)
    if caption:
        st.caption(caption)


def get_customer_email(party: dict) -> str | None:
    """Sucht primäre E-Mail einer Partei (über Kontakte oder Default-Feld)."""
    # parties hat aktuell kein direktes email-Feld — Kontakte werden separat geladen
    contacts = party.get("contacts") or []
    primary = next((c for c in contacts if c.get("is_primary")), None)
    if primary and primary.get("email"):
        return primary["email"]
    if contacts and contacts[0].get("email"):
        return contacts[0]["email"]
    return None
