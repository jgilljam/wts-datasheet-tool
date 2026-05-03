"""WTS-Branding: globales CSS, Logo-Helper, Header/Footer-Components."""

import base64
from datetime import date

import streamlit as st

from .config import (
    ACCENT,
    ANTHRACITE,
    BORDER,
    LOGO_PATH,
    PRIMARY,
    SUBTLE,
    TEXT_SECONDARY,
)


@st.cache_data
def logo_b64() -> str:
    return base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")


def apply_branding() -> None:
    """Globales WTS-CSS in die aktuelle Page injizieren."""
    st.markdown(
        f"""
        <style>
          .block-container {{ padding-top: 2rem; padding-bottom: 4rem; max-width: 880px; }}
          h1, h2, h3 {{ letter-spacing: -0.02em; color: {PRIMARY}; }}
          .wts-eyebrow {{
            font-family: ui-monospace, "JetBrains Mono", monospace;
            font-size: 0.72rem;
            letter-spacing: 0.22em;
            text-transform: uppercase;
            color: {TEXT_SECONDARY};
            font-weight: 500;
          }}
          .wts-header {{
            display: flex;
            align-items: center;
            gap: 1rem;
            padding-bottom: 1rem;
            border-bottom: 2px solid {PRIMARY};
            margin-bottom: 2rem;
          }}
          .wts-header img {{ height: 44px; width: auto; }}
          .wts-header-text {{ flex: 1; }}
          .wts-header-text h1 {{ margin: 0; font-size: 1.6rem; line-height: 1.1; }}
          .wts-header-text .sub {{
            font-family: ui-monospace, "JetBrains Mono", monospace;
            font-size: 0.7rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: {TEXT_SECONDARY};
            margin-top: 4px;
          }}
          .wts-pill {{
            display: inline-block;
            padding: 2px 10px;
            background: {SUBTLE};
            border: 1px solid {BORDER};
            border-radius: 999px;
            font-size: 0.78rem;
            font-family: ui-monospace, "JetBrains Mono", monospace;
            color: {TEXT_SECONDARY};
            margin-right: 6px;
          }}
          .wts-pill.accent {{ color: {ACCENT}; border-color: {ACCENT}; }}
          .wts-card {{
            background: {SUBTLE};
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 1rem;
          }}
          .wts-card h3 {{ margin-top: 0; font-size: 1.05rem; }}
          .wts-meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin-top: 8px;
          }}
          .wts-meta-grid .item .label {{
            font-family: ui-monospace, "JetBrains Mono", monospace;
            font-size: 0.65rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: {TEXT_SECONDARY};
            margin-bottom: 2px;
          }}
          .wts-meta-grid .item .val {{
            font-size: 0.92rem;
            color: {ANTHRACITE};
            font-weight: 500;
          }}
          .wts-spec-group {{
            font-family: ui-monospace, "JetBrains Mono", monospace;
            font-size: 0.7rem;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: {ACCENT};
            margin-top: 1rem;
            margin-bottom: 4px;
            font-weight: 500;
          }}
          .wts-footer {{
            margin-top: 4rem;
            padding-top: 1rem;
            border-top: 1px solid {BORDER};
            font-size: 0.75rem;
            color: {TEXT_SECONDARY};
            display: flex;
            justify-content: space-between;
          }}
          /* hide hamburger + footer */
          [data-testid="stToolbar"] {{ display: none; }}
          footer {{ visibility: hidden; }}
          header[data-testid="stHeader"] {{ background: transparent; }}

          /* PDF iframe */
          iframe.wts-pdf-preview {{
            width: 100%;
            height: 720px;
            border: 1px solid {BORDER};
            border-radius: 8px;
          }}

          /* Login centering */
          .wts-login-wrap {{
            max-width: 400px;
            margin: 4rem auto 0 auto;
            padding: 2rem;
            border: 1px solid {BORDER};
            border-radius: 16px;
            background: white;
          }}
          .wts-login-wrap img {{ height: 56px; display: block; margin: 0 auto 1.5rem auto; }}
          .wts-login-wrap h2 {{ text-align: center; font-size: 1.2rem; margin-bottom: 0.25rem; }}
          .wts-login-wrap .sub {{ text-align: center; color: {TEXT_SECONDARY}; font-size: 0.85rem; margin-bottom: 1.5rem; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(title: str, subtitle: str) -> None:
    """WTS-Header mit Logo, Titel, Untertitel und Logout-Button."""
    header_col, logout_col = st.columns([5, 1])
    with header_col:
        st.markdown(
            f"""
            <div class="wts-header">
              <img src="data:image/png;base64,{logo_b64()}" alt="WTS">
              <div class="wts-header-text">
                <h1>{title}</h1>
                <div class="sub">{subtitle}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with logout_col:
        st.write("")
        if st.button("Abmelden", use_container_width=True, help="Sitzung beenden"):
            st.session_state.clear()
            st.rerun()


def render_footer() -> None:
    """WTS-Footer mit Firmenangabe + Jahr."""
    st.markdown(
        f"""
        <div class="wts-footer">
          <div><strong style="color: {PRIMARY};">Weber Trading & Service</strong> · Kaiserstraße 35</div>
          <div>WTS-internes Tool · {date.today().year}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
