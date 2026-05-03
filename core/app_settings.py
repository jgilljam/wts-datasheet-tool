"""Runtime-Toggle-Layer (Key-Value).

Read-Reihenfolge: app_settings (DB) → st.secrets → Default.
DB-Werte gewinnen, damit Power-User per Settings-UI Pipeline-Toggles flippen
können, ohne secrets.toml anfassen zu müssen.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from .db import supabase


_TRUE = {"true", "1", "yes", "on", "ja"}
_FALSE = {"false", "0", "no", "off", "nein", ""}


def _coerce_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


@st.cache_data(ttl=30)
def _load_all() -> dict[str, str]:
    """Liest alle app_settings einmal. Cache 30 sec damit Settings-Änderungen
    sich sichtbar auswirken ohne sofortigen DB-Hammer-Pull."""
    try:
        rows = supabase().table("app_settings").select("key, value").execute().data or []
    except Exception:
        return {}
    return {r["key"]: r.get("value") or "" for r in rows}


def clear_cache() -> None:
    _load_all.clear()


def _secret(key: str) -> Any:
    """st.secrets ist nicht echt dict — getattr-Style zugriff sicher kapseln."""
    try:
        return st.secrets.get(key, None)
    except Exception:
        return None


def get_bool(key: str, *, default: bool, secret_fallback: str | None = None) -> bool:
    db = _load_all().get(key)
    if db is not None and db != "":
        return _coerce_bool(db, default)
    if secret_fallback:
        return _coerce_bool(_secret(secret_fallback), default)
    return default


def get_str(key: str, *, default: str, secret_fallback: str | None = None) -> str:
    db = _load_all().get(key)
    if db is not None and db != "":
        return str(db)
    if secret_fallback:
        s = _secret(secret_fallback)
        if s is not None:
            return str(s)
    return default


def get_int(key: str, *, default: int, secret_fallback: str | None = None) -> int:
    db = _load_all().get(key)
    if db is not None and db != "":
        return _coerce_int(db, default)
    if secret_fallback:
        return _coerce_int(_secret(secret_fallback), default)
    return default


def set_value(key: str, value: Any, *, actor_email: str | None = None) -> None:
    """Upsert in app_settings. Bool → 'true'/'false', alles andere als String."""
    if isinstance(value, bool):
        s = "true" if value else "false"
    else:
        s = str(value)
    payload = {"key": key, "value": s, "updated_by": actor_email}
    try:
        supabase().table("app_settings").upsert(payload, on_conflict="key").execute()
    except Exception:
        # Fallback: insert/update getrennt
        existing = (
            supabase().table("app_settings").select("key").eq("key", key)
            .maybe_single().execute().data
        )
        if existing:
            supabase().table("app_settings").update({"value": s, "updated_by": actor_email}).eq("key", key).execute()
        else:
            supabase().table("app_settings").insert(payload).execute()
    clear_cache()
