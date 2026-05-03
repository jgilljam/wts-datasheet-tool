"""User-Verwaltung + Authentifizierung — bcrypt + TOTP.

Modelliert:
- users-Tabelle (email + bcrypt-Password + optional TOTP-Secret + Rolle)
- auth_events (append-only Login-Audit)
- Failed-Login-Lockout (5 Fehlversuche → 15 min gesperrt)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import pyotp

from .db import supabase


# ============================================================
# Konstanten
# ============================================================

MAX_FAILED_LOGINS = 5
LOCK_DURATION_MIN = 15
BCRYPT_ROUNDS = 12  # ≈250ms auf Modern-CPU — guter Trade-off Speed/Security


# ============================================================
# Password-Hashing
# ============================================================

def hash_password(plain: str) -> str:
    """bcrypt-Hash mit eingebettetem Salt. Rounds=12 = sicher 2026+."""
    return bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ============================================================
# Audit
# ============================================================

def log_auth_event(
    *,
    event_type: str,
    email: str | None = None,
    user_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Schreibt einen Audit-Eintrag in auth_events (append-only)."""
    supabase().table("auth_events").insert({
        "event_type": event_type,
        "email": email,
        "user_id": user_id,
        "payload": payload or {},
    }).execute()


# ============================================================
# User-CRUD
# ============================================================

def list_users(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    q = (
        supabase()
        .table("users")
        .select("id, email, full_name, role, totp_enabled, is_active, "
                "last_login_at, failed_login_count, locked_until, created_at")
        .order("email")
    )
    if not include_inactive:
        q = q.eq("is_active", True)
    return q.execute().data or []


def get_user_by_email(email: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("users")
        .select("*")
        .eq("email", email.strip().lower())
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def get_user(user_id: str) -> dict[str, Any] | None:
    res = (
        supabase()
        .table("users")
        .select("*")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


def create_user(
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    role: str = "mitarbeiter",
) -> str:
    """Legt einen neuen User an. Returns user_id."""
    email_clean = email.strip().lower()
    if get_user_by_email(email_clean):
        raise ValueError(f"User mit Email {email_clean} existiert bereits.")
    if role not in ("admin", "mitarbeiter", "viewer"):
        raise ValueError(f"Ungültige Rolle: {role}")

    pw_hash = hash_password(password)
    res = supabase().table("users").insert({
        "email": email_clean,
        "full_name": full_name,
        "password_hash": pw_hash,
        "role": role,
    }).execute()
    user_id = res.data[0]["id"]
    log_auth_event(
        event_type="user_created",
        email=email_clean,
        user_id=user_id,
        payload={"role": role},
    )
    return user_id


def set_password(user_id: str, new_password: str) -> None:
    pw_hash = hash_password(new_password)
    supabase().table("users").update({
        "password_hash": pw_hash,
        "failed_login_count": 0,
        "locked_until": None,
    }).eq("id", user_id).execute()
    log_auth_event(event_type="password_changed", user_id=user_id)


def deactivate_user(user_id: str) -> None:
    supabase().table("users").update({"is_active": False}).eq("id", user_id).execute()
    log_auth_event(event_type="user_deactivated", user_id=user_id)


def reactivate_user(user_id: str) -> None:
    supabase().table("users").update({
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
    }).eq("id", user_id).execute()
    log_auth_event(event_type="user_reactivated", user_id=user_id)


def set_role(user_id: str, role: str) -> None:
    if role not in ("admin", "mitarbeiter", "viewer"):
        raise ValueError(f"Ungültige Rolle: {role}")
    supabase().table("users").update({"role": role}).eq("id", user_id).execute()
    log_auth_event(event_type="role_changed", user_id=user_id, payload={"role": role})


# ============================================================
# TOTP / 2FA
# ============================================================

def generate_totp_secret() -> str:
    """Neues TOTP-Secret (base32). Wird beim Setup angezeigt + im
    User-Datensatz gespeichert sobald die Verifikation klappt."""
    return pyotp.random_base32()


def totp_provisioning_uri(email: str, secret: str, issuer: str = "WTS-Tool") -> str:
    """URI für QR-Code-Generation (otpauth://...)."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """Verifiziert einen 6-stelligen TOTP-Code. Toleranz ±1 Window (30s)."""
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def enable_totp(user_id: str, secret: str) -> None:
    """Aktiviert 2FA für einen User. Secret muss vorher per verify_totp
    bestätigt sein (UI-Verantwortung)."""
    supabase().table("users").update({
        "totp_secret": secret,
        "totp_enabled": True,
    }).eq("id", user_id).execute()
    log_auth_event(event_type="totp_enabled", user_id=user_id)


def disable_totp(user_id: str) -> None:
    supabase().table("users").update({
        "totp_secret": None,
        "totp_enabled": False,
    }).eq("id", user_id).execute()
    log_auth_event(event_type="totp_disabled", user_id=user_id)


# ============================================================
# Login-Flow
# ============================================================

class AuthError(Exception):
    """Login fehlgeschlagen — Message ist user-facing (Deutsch)."""


def authenticate(email: str, password: str, totp_code: str | None = None) -> dict[str, Any]:
    """Prüft Login-Daten. Bei Erfolg: Returns User-Dict.

    Wirft AuthError mit user-facing Message bei jedem Fail.
    Schreibt auth_events bei jedem Versuch.
    Implementiert Lockout nach MAX_FAILED_LOGINS Versuchen.
    """
    email_clean = email.strip().lower()
    user = get_user_by_email(email_clean)

    if not user:
        log_auth_event(event_type="login_fail_unknown_email", email=email_clean)
        raise AuthError("Email oder Passwort falsch.")

    # Lockout prüfen
    locked_until = user.get("locked_until")
    if locked_until:
        if isinstance(locked_until, str):
            locked_until_dt = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
        else:
            locked_until_dt = locked_until
        if locked_until_dt > datetime.now(timezone.utc):
            log_auth_event(
                event_type="login_fail_locked",
                email=email_clean,
                user_id=user["id"],
            )
            mins = int((locked_until_dt - datetime.now(timezone.utc)).total_seconds() / 60) + 1
            raise AuthError(f"Konto gesperrt — versuch's in {mins} Minuten erneut.")

    if not user.get("is_active"):
        log_auth_event(event_type="login_fail_inactive", email=email_clean, user_id=user["id"])
        raise AuthError("Konto ist deaktiviert.")

    # Password
    if not verify_password(password, user["password_hash"]):
        _record_failed_login(user)
        raise AuthError("Email oder Passwort falsch.")

    # 2FA wenn aktiv
    if user.get("totp_enabled"):
        if not totp_code:
            raise AuthError("Bitte 2FA-Code eingeben.")
        if not verify_totp(user["totp_secret"], totp_code):
            log_auth_event(event_type="login_fail_totp", email=email_clean, user_id=user["id"])
            _record_failed_login(user)
            raise AuthError("2FA-Code falsch.")

    # Erfolg
    supabase().table("users").update({
        "last_login_at": datetime.now(timezone.utc).isoformat(),
        "failed_login_count": 0,
        "locked_until": None,
    }).eq("id", user["id"]).execute()
    log_auth_event(event_type="login_success", email=email_clean, user_id=user["id"])

    return user


def _record_failed_login(user: dict[str, Any]) -> None:
    """Erhöht failed_login_count, sperrt bei MAX_FAILED_LOGINS."""
    new_count = int(user.get("failed_login_count") or 0) + 1
    update: dict[str, Any] = {"failed_login_count": new_count}
    if new_count >= MAX_FAILED_LOGINS:
        update["locked_until"] = (
            datetime.now(timezone.utc) + timedelta(minutes=LOCK_DURATION_MIN)
        ).isoformat()
        update["failed_login_count"] = 0  # Reset, Lockout-Mechanik übernimmt
    supabase().table("users").update(update).eq("id", user["id"]).execute()
    log_auth_event(
        event_type="login_fail_password",
        email=user["email"],
        user_id=user["id"],
        payload={"failed_count": new_count},
    )
