"""WTS-Tool — Multipage-Entry.

Globale Cross-Page-Schicht (Page-Config, Branding, Auth) läuft hier;
jede Page rendert ihren eigenen Header+Body+Footer.

Neue Module: weitere st.Page() in `pages` registrieren.
"""

import streamlit as st

from core.auth import require_login
from core.branding import apply_branding
from core.config import LOGO_PATH
from features import articles, datasheet, deliveries, parties, stock


st.set_page_config(
    page_title="WTS-Tool",
    page_icon=str(LOGO_PATH),
    layout="centered",
    initial_sidebar_state="expanded",
)

apply_branding()
require_login()

pages = [
    st.Page(
        datasheet.render,
        title="Datenblatt",
        icon="📄",
        url_path="datenblatt",
        default=True,
    ),
    st.Page(
        deliveries.render,
        title="Lieferungen",
        icon="📦",
        url_path="lieferungen",
    ),
    st.Page(
        stock.render,
        title="Lager",
        icon="📊",
        url_path="lager",
    ),
    st.Page(
        articles.render,
        title="Artikel",
        icon="🔧",
        url_path="artikel",
    ),
    st.Page(
        parties.render,
        title="Parteien",
        icon="👥",
        url_path="parteien",
    ),
]

nav = st.navigation(pages)
nav.run()
