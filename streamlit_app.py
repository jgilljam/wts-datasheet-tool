"""WTS-Tool — Multipage-Entry.

Globale Cross-Page-Schicht (Page-Config, Branding, Auth) läuft hier;
jede Page rendert ihren eigenen Header+Body+Footer.

Neue Module: weitere st.Page() in `pages` registrieren.
"""

import streamlit as st

from core.auth import require_login
from core.branding import apply_branding
from core.config import LOGO_PATH
from features import (
    articles,
    dashboard,
    datasheet,
    deliveries,
    dunning,
    inbox,
    incoming_invoices,
    invoices,
    orders,
    parties,
    purchase_orders,
    quotations,
    settings,
    stock,
    users,
)


st.set_page_config(
    page_title="WTS-Tool",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_branding()
require_login()

pages_map = {
    "dashboard": st.Page(dashboard.render, title="Dashboard", icon="🏠", url_path="dashboard", default=True),
    "quotations": st.Page(quotations.render, title="Angebote", icon="📨", url_path="angebote"),
    "orders": st.Page(orders.render, title="Aufträge", icon="📑", url_path="auftraege"),
    "purchase_orders": st.Page(purchase_orders.render, title="Bestellungen", icon="🛒", url_path="bestellungen"),
    "deliveries": st.Page(deliveries.render, title="Lieferungen", icon="📦", url_path="lieferungen"),
    "invoices": st.Page(invoices.render, title="Rechnungen", icon="📄", url_path="rechnungen"),
    "dunning": st.Page(dunning.render, title="OP-Liste", icon="💼", url_path="op-liste"),
    "incoming_invoices": st.Page(incoming_invoices.render, title="Eingangsrechnungen", icon="📥", url_path="eingangsrechnungen"),
    "inbox": st.Page(inbox.render, title="Posteingang", icon="📬", url_path="posteingang"),
    "stock": st.Page(stock.render, title="Lager", icon="📊", url_path="lager"),
    "articles": st.Page(articles.render, title="Artikel", icon="🔧", url_path="artikel"),
    "parties": st.Page(parties.render, title="Parteien", icon="👥", url_path="parteien"),
    "datasheet": st.Page(datasheet.render, title="Datenblatt", icon="📋", url_path="datenblatt"),
    "users": st.Page(users.render, title="Mitarbeiter", icon="👤", url_path="mitarbeiter"),
    "settings": st.Page(settings.render, title="Einstellungen", icon="⚙️", url_path="einstellungen"),
}

# Pages-Registry für Cross-Page-Navigation per st.switch_page (Dashboard-CTAs)
st.session_state["__wts_pages"] = pages_map

# Gruppierte Sidebar-Navigation — st.navigation akzeptiert ein dict
# {Section-Header: [pages]} und rendert die Section-Header als getrennte
# Gruppen in der Sidebar.
sidebar_groups = {
    "Übersicht": [pages_map["dashboard"]],
    "Verkauf": [
        pages_map["quotations"],
        pages_map["orders"],
        pages_map["deliveries"],
        pages_map["invoices"],
        pages_map["dunning"],
    ],
    "Einkauf": [
        pages_map["purchase_orders"],
        pages_map["incoming_invoices"],
    ],
    "Kommunikation": [pages_map["inbox"]],
    "Stammdaten": [
        pages_map["parties"],
        pages_map["articles"],
        pages_map["stock"],
    ],
    "Werkzeuge": [pages_map["datasheet"]],
    "Verwaltung": [
        pages_map["users"],
        pages_map["settings"],
    ],
}

nav = st.navigation(sidebar_groups)
nav.run()
