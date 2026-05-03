"""Authentifizierung — Multi-User mit bcrypt + optional TOTP-2FA.

Login-Flow:
1. Wenn `users`-Tabelle leer: Erst-Einrichtung-Form (legt ersten Admin an)
2. Sonst: Email + Password (+ TOTP wenn user.totp_enabled)
3. Bei Erfolg: st.session_state['user'] wird gefüllt mit
   {id, email, full_name, role, totp_enabled}

Sicherheit:
- Session-Timeout nach SESSION_TIMEOUT_SEC Inaktivität → automatischer Logout
"""

from __future__ import annotations

import time

import streamlit as st

from . import users
from .branding import logo_b64


# 60 Minuten Inaktivität → Auto-Logout
SESSION_TIMEOUT_SEC = 60 * 60


def require_login() -> None:
    """Stoppt die App, falls der Nutzer nicht eingeloggt ist.

    Zusätzlich: prüft Session-Timeout (Inaktivität > SESSION_TIMEOUT_SEC).
    """

    # Session-State-Schlüssel:
    #   user           : volles User-Dict (nach Login)
    #   user_email     : Komfort-Shortcut für audit-Log (string)
    #   authed         : Boolean (legacy — wird mit-gepflegt)
    #   last_activity  : Unix-Timestamp letzter require_login()-Aufruf
    if st.session_state.get("user"):
        last = st.session_state.get("last_activity", 0)
        now = time.time()
        if last and (now - last) > SESSION_TIMEOUT_SEC:
            # Auto-Logout wegen Inaktivität
            logout()
            st.warning(
                f"Session abgelaufen ({SESSION_TIMEOUT_SEC // 60} Minuten Inaktivität). "
                "Bitte erneut anmelden.",
                icon="⏱️",
            )
            _render_login_form()
            st.stop()
        st.session_state["last_activity"] = now
        return

    # Erst-Einrichtung — keine User in DB
    all_users = users.list_users(include_inactive=True)
    if not all_users:
        _render_first_setup()
        st.stop()

    _render_login_form()
    st.stop()


def logout() -> None:
    email = st.session_state.get("user_email")
    user_id = (st.session_state.get("user") or {}).get("id")
    if user_id:
        try:
            users.log_auth_event(event_type="logout", email=email, user_id=user_id)
        except Exception:
            pass
    for k in ("user", "user_email", "authed", "last_activity"):
        st.session_state.pop(k, None)


def current_user() -> dict | None:
    return st.session_state.get("user")


def require_role(*allowed_roles: str) -> None:
    """Zusätzlich zu require_login: prüft Rolle. Nur in Admin-Bereichen."""
    user = current_user()
    if not user:
        require_login()
        return
    if user.get("role") not in allowed_roles:
        st.error(f"Diese Seite ist nur für Rollen {', '.join(allowed_roles)} sichtbar.")
        st.stop()


# ============================================================
# UI-Forms
# ============================================================

def _render_login_form() -> None:
    """Standard-Login: Email + Password + optional TOTP."""
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown(
            f"""
            <div class="wts-login-head">
              <img src="data:image/png;base64,{logo_b64()}" alt="WTS">
              <h2>WTS-Tool</h2>
              <div class="sub">Anmeldung für WTS-Mitarbeiter</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("login", clear_on_submit=False):
            email = st.text_input("Email", placeholder="vorname.nachname@wts-trading.de")
            pwd = st.text_input("Passwort", type="password")
            totp = st.text_input(
                "2FA-Code (falls aktiv)",
                max_chars=6,
                placeholder="123456",
                help="Nur ausfüllen, wenn du 2FA in den Einstellungen aktiviert hast.",
            )
            ok = st.form_submit_button("Anmelden", use_container_width=True, type="primary")
        if ok:
            try:
                user = users.authenticate(
                    email=email,
                    password=pwd,
                    totp_code=totp or None,
                )
            except users.AuthError as e:
                st.error(str(e))
                return

            st.session_state["user"] = {
                "id": user["id"],
                "email": user["email"],
                "full_name": user.get("full_name"),
                "role": user.get("role"),
                "totp_enabled": bool(user.get("totp_enabled")),
            }
            st.session_state["user_email"] = user["email"]
            st.session_state["authed"] = True
            st.session_state["last_activity"] = time.time()
            st.rerun()


def _render_first_setup() -> None:
    """Erst-Einrichtung: ersten Admin-User anlegen."""
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown(
            f"""
            <div class="wts-login-head">
              <img src="data:image/png;base64,{logo_b64()}" alt="WTS">
              <h2>WTS-Tool — Erst-Einrichtung</h2>
              <div class="sub">Lege den ersten Admin-Account an.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.info(
            "Dies ist die einmalige Erst-Einrichtung. Der erste Account ist "
            "automatisch Admin und kann später weitere Mitarbeiter einladen.",
            icon="🔐",
        )
        with st.form("first_setup"):
            email = st.text_input("Email", placeholder="julian@wts-trading.de")
            full_name = st.text_input("Name", placeholder="Julian Gilljam")
            pwd = st.text_input("Passwort", type="password", help="Mindestens 8 Zeichen")
            pwd2 = st.text_input("Passwort wiederholen", type="password")
            ok = st.form_submit_button(
                "Admin-Account anlegen",
                use_container_width=True,
                type="primary",
            )
        if ok:
            if not email or not pwd:
                st.error("Email und Passwort sind Pflicht.")
                return
            if len(pwd) < 8:
                st.error("Passwort muss mindestens 8 Zeichen haben.")
                return
            if pwd != pwd2:
                st.error("Passwörter stimmen nicht überein.")
                return
            try:
                user_id = users.create_user(
                    email=email,
                    password=pwd,
                    full_name=full_name or None,
                    role="admin",
                )
            except ValueError as e:
                st.error(str(e))
                return
            st.success(
                f"Admin-Account {email} angelegt. Bitte melde dich jetzt an."
            )
            st.rerun()
