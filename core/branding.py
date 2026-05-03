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
          /* Inter Variable als Body-Font + JetBrains Mono für Code/Specs.
             Bunny Fonts (GDPR-konform, kein Google-Tracking). */
          @import url('https://fonts.bunny.net/css?family=inter:400,500,600,700|jetbrains-mono:400,500&display=swap');

          html, body, [class*="css"], [class*="st-"], button, input, textarea, select {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif !important;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            font-feature-settings: 'cv11', 'ss01', 'ss03';
          }}

          /* Wide-Layout mit selbstgesetztem Max-Width für ruhige Lesbarkeit */
          .block-container {{
            padding-top: 1.75rem;
            padding-bottom: 4rem;
            max-width: 1240px;
          }}
          h1, h2, h3 {{ letter-spacing: -0.02em; color: {PRIMARY}; }}
          h1 {{ font-weight: 700; letter-spacing: -0.03em; }}
          h2 {{ font-weight: 650; letter-spacing: -0.025em; }}
          h3 {{ font-weight: 600; }}

          /* Sidebar — Group-Header-Style + smoothere Selection */
          [data-testid="stSidebarNavLink"] {{
            border-radius: 8px !important;
            transition: background .12s ease, padding-left .12s ease;
          }}
          [data-testid="stSidebarNavLink"]:hover {{
            padding-left: 14px !important;
          }}
          [data-testid="stSidebarNavSeparator"] {{
            margin: 14px 0 6px 0 !important;
          }}
          /* Sidebar-Section-Heading via aria-label */
          .wts-sidebar-section {{
            font-family: 'JetBrains Mono', ui-monospace, monospace;
            font-size: 0.62rem;
            letter-spacing: 0.22em;
            text-transform: uppercase;
            color: {TEXT_SECONDARY};
            font-weight: 500;
            padding: 14px 0 4px 4px;
            margin-top: 8px;
          }}

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

          /* Pills — klassische Variante (default = subtle) */
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
          .wts-pill.accent {{ color: {ACCENT}; border-color: {ACCENT}; background: {ACCENT}1A; }}
          .wts-pill.primary {{ color: {PRIMARY}; border-color: {PRIMARY}; background: {PRIMARY}14; }}
          .wts-pill.success {{ color: #047857; border-color: #10B98155; background: #10B9811A; }}
          .wts-pill.warn    {{ color: #B45309; border-color: #F59E0B55; background: #F59E0B1A; }}
          .wts-pill.danger  {{ color: #B91C1C; border-color: #EF444455; background: #EF44441A; }}

          /* Cards mit 2-Step Shadow + Hover-Lift */
          .wts-card {{
            background: white;
            border: 1px solid {BORDER};
            border-radius: 14px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.05);
            transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
          }}
          .wts-card:hover {{
            box-shadow: 0 4px 12px rgba(15,23,42,.08), 0 2px 4px rgba(15,23,42,.04);
            transform: translateY(-1px);
            border-color: #D4D4D8;
          }}
          .wts-card.subtle {{ background: {SUBTLE}; }}
          .wts-card.flat   {{ box-shadow: none; }}
          .wts-card.flat:hover {{ transform: none; box-shadow: none; }}
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

          /* Native Streamlit-Container (st.container(border=True)) im WTS-Look */
          [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 14px !important;
            border-color: {BORDER} !important;
            box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.05);
            transition: box-shadow .18s ease, transform .18s ease;
          }}
          [data-testid="stVerticalBlockBorderWrapper"]:hover {{
            box-shadow: 0 4px 12px rgba(15,23,42,.08), 0 2px 4px rgba(15,23,42,.04);
          }}

          /* Toast — WTS-Akzent, Slide-In */
          [data-testid="stToast"] {{
            border-left: 3px solid {ACCENT} !important;
            border-radius: 10px !important;
            box-shadow: 0 8px 24px rgba(15,23,42,.10), 0 2px 4px rgba(15,23,42,.06) !important;
          }}

          /* Buttons — Micro-Interaktionen */
          .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
            border-radius: 8px;
            font-weight: 500;
            transition: transform .08s ease, box-shadow .12s ease, background .12s ease;
          }}
          .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
            box-shadow: 0 2px 6px rgba(15,23,42,.08);
          }}
          .stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {{
            transform: translateY(1px);
          }}
          /* Primary-Button: WTS-Anthrazit/Primary statt Streamlit-Default-Rot */
          .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
            background: {PRIMARY};
            border-color: {PRIMARY};
          }}
          .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
            background: #143358;
            border-color: #143358;
          }}

          /* st.metric — feinerer Look */
          [data-testid="stMetric"] {{
            background: white;
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 0.75rem 1rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.03);
          }}
          [data-testid="stMetricLabel"] {{
            font-family: ui-monospace, "JetBrains Mono", monospace !important;
            font-size: 0.65rem !important;
            letter-spacing: 0.16em !important;
            text-transform: uppercase !important;
            color: {TEXT_SECONDARY} !important;
          }}
          [data-testid="stMetricValue"] {{
            color: {PRIMARY} !important;
            font-weight: 700 !important;
            letter-spacing: -0.02em !important;
          }}

          /* Tabs — schlanker, mit Akzent-Underline */
          .stTabs [data-baseweb="tab-list"] {{
            border-bottom: 1px solid {BORDER};
            gap: 4px;
          }}
          .stTabs [data-baseweb="tab"] {{
            padding-top: 8px;
            padding-bottom: 8px;
            font-weight: 500;
          }}
          .stTabs [aria-selected="true"] {{
            color: {PRIMARY} !important;
          }}

          /* Pills/Segmented-Control (st.pills, st.segmented_control) */
          [data-testid="stPills"] button, [data-testid="stSegmentedControl"] button {{
            border-radius: 999px !important;
            font-weight: 500;
          }}

          /* Dialog (st.dialog) — moderner Schatten */
          [data-testid="stDialog"] > div {{
            border-radius: 16px !important;
            box-shadow: 0 24px 64px rgba(15,23,42,.18), 0 4px 12px rgba(15,23,42,.10) !important;
          }}

          /* Sidebar — abgesetzt mit dezentem Hintergrund */
          section[data-testid="stSidebar"] {{
            background: {SUBTLE};
            border-right: 1px solid {BORDER};
          }}
          section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {{
            border-radius: 8px;
          }}

          /* Inputs — etwas weicher */
          .stTextInput input, .stNumberInput input, .stDateInput input,
          .stTextArea textarea, [data-baseweb="select"] > div {{
            border-radius: 8px !important;
          }}

          /* DataFrames — etwas Luft + abgerundet */
          [data-testid="stDataFrame"] {{
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid {BORDER};
          }}

          /* Deploy-Button + Hamburger ausblenden — aber NICHT die ganze
             Toolbar, sonst verschwindet der Sidebar-Aufklapp-Pfeil. */
          [data-testid="stDeployButton"],
          .stAppDeployButton,
          [data-testid="stToolbarActions"] {{ display: none !important; }}
          footer {{ visibility: hidden; }}
          header[data-testid="stHeader"] {{ background: transparent; }}

          /* PDF iframe */
          iframe.wts-pdf-preview {{
            width: 100%;
            height: 720px;
            border: 1px solid {BORDER};
            border-radius: 10px;
            box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.05);
          }}

          /* Login — Logo + Form als ein Block visuell */
          .wts-login-head {{
            margin-top: 4rem;
            padding: 2rem 2rem 1rem 2rem;
            border-radius: 18px 18px 0 0;
            background: white;
            border: 1px solid {BORDER};
            border-bottom: none;
            text-align: center;
          }}
          .wts-login-head img {{ height: 56px; display: block; margin: 0 auto 1.25rem auto; }}
          .wts-login-head h2 {{ text-align: center; font-size: 1.2rem; margin: 0 0 0.25rem 0; }}
          .wts-login-head .sub {{ color: {TEXT_SECONDARY}; font-size: 0.85rem; margin-bottom: 0; }}

          /* Form direkt unter dem Login-Head — visuell verbunden */
          .wts-login-head + div [data-testid="stForm"] {{
            background: white;
            border: 1px solid {BORDER};
            border-top: 1px solid {BORDER};
            border-radius: 0 0 18px 18px;
            padding: 1.5rem 2rem 2rem 2rem;
            box-shadow: 0 12px 32px rgba(15,23,42,.08), 0 2px 6px rgba(15,23,42,.04);
          }}
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
