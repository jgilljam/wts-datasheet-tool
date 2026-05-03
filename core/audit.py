"""Generischer Audit-Logger für Belegtabellen.

Jede Belegtabelle hat eine Schwester-Tabelle `<x>_events` mit Spalten:
  - id (bigserial)
  - <ref_field>     uuid not null  → references <table>(id)
  - at              timestamptz default now()
  - actor_label     text
  - event_type      text
  - payload         jsonb

Beispiel:  log_event("delivery_events", "delivery_id", id, "status_change", {...})
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from .db import supabase


def actor_label() -> str:
    """Wer ist gerade angemeldet? Email aus core.auth-Session,
    Fallback 'Mitarbeiter' für Hintergrund-Jobs ohne Session."""
    return st.session_state.get("user_email") or "Mitarbeiter"


def log_event(
    events_table: str,
    ref_field: str,
    ref_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Schreibt einen Audit-Eintrag in <events_table>."""
    supabase().table(events_table).insert({
        ref_field: ref_id,
        "event_type": event_type,
        "actor_label": actor_label(),
        "payload": payload or {},
    }).execute()
