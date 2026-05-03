"""Authentifizierung — aktuell simples Passwort, später st.login() + Google OIDC."""

import streamlit as st

from .branding import logo_b64


def require_login() -> None:
    """Stoppt die App, falls der Nutzer nicht eingeloggt ist.

    Liest `APP_PASSWORD` aus den Streamlit-Secrets. Bei Treffer wird
    `st.session_state["authed"] = True` gesetzt; sonst Login-Form.
    """
    expected = st.secrets.get("APP_PASSWORD")
    if not expected:
        st.error(
            "Konfigurationsfehler: APP_PASSWORD ist nicht gesetzt. "
            "Admin: in `.streamlit/secrets.toml` (lokal) oder App-Settings (Cloud) hinterlegen."
        )
        st.stop()

    if st.session_state.get("authed"):
        return

    st.markdown(
        f"""
        <div class="wts-login-wrap">
          <img src="data:image/png;base64,{logo_b64()}" alt="WTS">
          <h2>Datenblatt-Tool</h2>
          <div class="sub">Anmeldung für WTS-Mitarbeiter</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("login", clear_on_submit=False):
            pwd = st.text_input(
                "Passwort",
                type="password",
                label_visibility="collapsed",
                placeholder="Passwort",
            )
            ok = st.form_submit_button("Anmelden", use_container_width=True, type="primary")
        if ok:
            if pwd == expected:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Falsches Passwort.")
    st.stop()
