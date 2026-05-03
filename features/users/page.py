"""Mitarbeiter-Verwaltung — nur für Admins.

Tabs:
- Liste:    Übersicht aller User mit Aktionen (deaktivieren, Passwort zurücksetzen)
- Anlegen:  neuen Mitarbeiter einladen (Email + Initial-Passwort)
- Mein Account:  Passwort ändern, 2FA-Setup (alle Rollen)
- Auth-Log: append-only Login-Audit (nur Admin)
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import streamlit as st

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from core import auth, users
from core.branding import render_footer, render_header
from core.db import supabase


def render() -> None:
    auth.require_login()
    render_header(
        title="Mitarbeiter",
        subtitle="Accounts, Passwörter, 2FA und Login-Audit",
    )

    me = auth.current_user() or {}
    is_admin = me.get("role") == "admin"

    if is_admin:
        tab_list, tab_new, tab_self, tab_audit = st.tabs(
            ["Liste", "Neuen Mitarbeiter anlegen", "Mein Account", "Login-Audit"]
        )
        with tab_list:
            _render_user_list()
        with tab_new:
            _render_create_user()
        with tab_self:
            _render_self_settings(me)
        with tab_audit:
            _render_auth_log()
    else:
        # Nicht-Admin sieht nur "Mein Account"
        st.info(
            "Nur Admins können Mitarbeiter verwalten. Hier kannst du dein "
            "eigenes Passwort ändern und 2FA aktivieren.",
            icon="👤",
        )
        _render_self_settings(me)

    render_footer()


# ============================================================
# Tab: Liste
# ============================================================

def _render_user_list() -> None:
    show_inactive = st.checkbox("Deaktivierte User anzeigen", value=False)
    rows = users.list_users(include_inactive=show_inactive)
    if not rows:
        st.info("Noch keine User angelegt.")
        return

    me = auth.current_user() or {}
    for u in rows:
        with st.container(border=True):
            cols = st.columns([3, 2, 1, 1, 2])
            cols[0].markdown(f"**{u['email']}**")
            if u.get("full_name"):
                cols[0].caption(u["full_name"])
            cols[1].markdown(
                f"`{u['role']}`"
                + (" · 🛡️ 2FA" if u.get("totp_enabled") else "")
            )
            if u.get("last_login_at"):
                last = u["last_login_at"]
                if isinstance(last, str):
                    last = last[:16].replace("T", " ")
                cols[2].caption(f"zuletzt: {last}")
            else:
                cols[2].caption("nie")
            if not u.get("is_active"):
                cols[3].markdown(":gray[**deaktiviert**]")
            elif u.get("locked_until"):
                cols[3].markdown(":red[gesperrt]")

            with cols[4]:
                act_col1, act_col2 = st.columns(2)
                # Eigenen Account nicht deaktivieren / nicht entrollen
                if u["id"] != me.get("id"):
                    if u.get("is_active"):
                        if act_col1.button(
                            "Deaktivieren",
                            key=f"deact_{u['id']}",
                            use_container_width=True,
                            type="secondary",
                        ):
                            users.deactivate_user(u["id"])
                            st.rerun()
                    else:
                        if act_col1.button(
                            "Aktivieren",
                            key=f"act_{u['id']}",
                            use_container_width=True,
                        ):
                            users.reactivate_user(u["id"])
                            st.rerun()

                if act_col2.button(
                    "Passwort zurücksetzen",
                    key=f"resetpw_{u['id']}",
                    use_container_width=True,
                ):
                    st.session_state[f"resetpw_open_{u['id']}"] = True

            if st.session_state.get(f"resetpw_open_{u['id']}"):
                with st.form(f"resetpw_form_{u['id']}"):
                    new_pw = st.text_input("Neues Passwort", type="password", key=f"resetpw_in_{u['id']}")
                    new_pw2 = st.text_input("Wiederholen", type="password", key=f"resetpw_in2_{u['id']}")
                    cc1, cc2 = st.columns(2)
                    if cc1.form_submit_button("Setzen", type="primary", use_container_width=True):
                        if not new_pw or len(new_pw) < 8:
                            st.error("Mindestens 8 Zeichen.")
                        elif new_pw != new_pw2:
                            st.error("Passwörter stimmen nicht überein.")
                        else:
                            users.set_password(u["id"], new_pw)
                            st.session_state[f"resetpw_open_{u['id']}"] = False
                            st.success(f"Passwort für {u['email']} gesetzt.")
                            st.rerun()
                    if cc2.form_submit_button("Abbrechen", use_container_width=True):
                        st.session_state[f"resetpw_open_{u['id']}"] = False
                        st.rerun()


# ============================================================
# Tab: Neuen Mitarbeiter anlegen
# ============================================================

def _render_create_user() -> None:
    st.markdown("Neuen Mitarbeiter mit Initial-Passwort anlegen. Er kann das später selbst ändern und 2FA aktivieren.")
    with st.form("new_user"):
        email = st.text_input("Email", placeholder="vorname.nachname@wts-trading.de")
        full_name = st.text_input("Name (optional)")
        role = st.selectbox(
            "Rolle",
            ["mitarbeiter", "admin", "viewer"],
            index=0,
            help="Admin: kann Mitarbeiter verwalten. Mitarbeiter: voller Zugriff auf Belege. Viewer: nur lesen.",
        )
        pwd = st.text_input("Initial-Passwort", type="password", help="Mindestens 8 Zeichen")
        pwd2 = st.text_input("Wiederholen", type="password")
        ok = st.form_submit_button("Anlegen", type="primary", use_container_width=True)
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
            users.create_user(
                email=email,
                password=pwd,
                full_name=full_name or None,
                role=role,
            )
        except ValueError as e:
            st.error(str(e))
            return
        st.success(f"Mitarbeiter {email} angelegt. Bitte das Initial-Passwort sicher übermitteln.")


# ============================================================
# Tab: Mein Account
# ============================================================

def _render_self_settings(me: dict) -> None:
    st.subheader("Passwort ändern")
    with st.form("change_pw"):
        old_pw = st.text_input("Aktuelles Passwort", type="password")
        new_pw = st.text_input("Neues Passwort", type="password")
        new_pw2 = st.text_input("Neues Passwort wiederholen", type="password")
        ok = st.form_submit_button("Passwort ändern", type="primary")
    if ok:
        # Aktuelles Password prüfen via authenticate
        try:
            users.authenticate(
                email=me["email"], password=old_pw,
                totp_code=None if not me.get("totp_enabled") else "skip-check",
            )
            # Wenn 2FA aktiv ist, würde authenticate eine TOTP-Anforderung werfen.
            # Für Password-Change machen wir's pragmatisch: nur Password-Check.
        except users.AuthError as e:
            # AuthError für totp ist OK in diesem Kontext
            if "2FA" not in str(e) and "Code" not in str(e):
                st.error("Aktuelles Passwort ist falsch.")
                return
        if not new_pw or len(new_pw) < 8:
            st.error("Neues Passwort muss mindestens 8 Zeichen haben.")
            return
        if new_pw != new_pw2:
            st.error("Passwörter stimmen nicht überein.")
            return
        users.set_password(me["id"], new_pw)
        st.success("Passwort geändert. Beim nächsten Login mit dem neuen Passwort anmelden.")

    st.divider()
    st.subheader("Zwei-Faktor-Authentifizierung (TOTP)")
    if me.get("totp_enabled"):
        st.success("2FA ist aktiv. Bei jedem Login wird ein 6-stelliger Code aus deiner Authenticator-App gefragt.", icon="🛡️")
        if st.button("2FA deaktivieren", type="secondary"):
            users.disable_totp(me["id"])
            st.session_state["user"]["totp_enabled"] = False
            st.success("2FA deaktiviert.")
            st.rerun()
    else:
        st.markdown(
            "Aktiviere 2FA für mehr Sicherheit. Nutze eine Authenticator-App "
            "wie Google Authenticator, Microsoft Authenticator, Authy oder 1Password."
        )
        if "totp_setup_secret" not in st.session_state:
            if st.button("2FA einrichten"):
                st.session_state["totp_setup_secret"] = users.generate_totp_secret()
                st.rerun()
        else:
            secret = st.session_state["totp_setup_secret"]
            uri = users.totp_provisioning_uri(me["email"], secret)
            col_qr, col_info = st.columns([1, 2])
            with col_qr:
                if HAS_QRCODE:
                    img = qrcode.make(uri)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    st.image(buf.getvalue(), width=200)
                else:
                    st.warning("qrcode-Library nicht installiert.")
            with col_info:
                st.markdown("**Schritt 1:** QR-Code mit Authenticator-App scannen")
                st.code(secret, language=None)
                st.caption("Falls QR nicht funktioniert: Secret manuell eingeben.")
                st.markdown("**Schritt 2:** Code aus der App eingeben:")
                with st.form("totp_verify"):
                    code = st.text_input("6-stelliger Code", max_chars=6)
                    cc1, cc2 = st.columns(2)
                    if cc1.form_submit_button("Aktivieren", type="primary"):
                        if users.verify_totp(secret, code):
                            users.enable_totp(me["id"], secret)
                            st.session_state["user"]["totp_enabled"] = True
                            del st.session_state["totp_setup_secret"]
                            st.success("2FA aktiviert. Beim nächsten Login wird der Code gefragt.")
                            st.rerun()
                        else:
                            st.error("Code ist falsch — versuch's nochmal (Codes wechseln alle 30s).")
                    if cc2.form_submit_button("Abbrechen"):
                        del st.session_state["totp_setup_secret"]
                        st.rerun()

    st.divider()
    st.subheader("Abmelden")
    if st.button("Abmelden", type="secondary"):
        auth.logout()
        st.rerun()


# ============================================================
# Tab: Auth-Log
# ============================================================

def _render_auth_log() -> None:
    rows = (
        supabase()
        .table("auth_events")
        .select("*")
        .order("at", desc=True)
        .limit(200)
        .execute()
        .data
    ) or []
    if not rows:
        st.info("Noch keine Auth-Events aufgezeichnet.")
        return

    st.caption(f"Letzte {len(rows)} Login-Versuche (jüngste zuerst).")
    for ev in rows:
        at = ev.get("at", "")
        if isinstance(at, str):
            at = at[:19].replace("T", " ")
        et = ev.get("event_type", "")
        email = ev.get("email") or "?"
        icon = "✅" if et == "login_success" else (
            "🚪" if et == "logout" else (
                "⚠️" if "fail" in et else "🛡️"
            )
        )
        payload = ev.get("payload") or {}
        meta = ""
        if payload.get("failed_count"):
            meta = f" (failed_count={payload['failed_count']})"
        if payload.get("role"):
            meta = f" (role={payload['role']})"
        st.markdown(f"`{at}` {icon} **{et}** — {email}{meta}")
