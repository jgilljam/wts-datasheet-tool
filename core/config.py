"""Cross-page Konstanten + Settings-Loader für das WTS-Tool."""

from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = ROOT / "assets" / "logo.png"


# WTS Brand Colors
PRIMARY = "#0A2540"
ACCENT = "#D84B41"
ANTHRACITE = "#1A1918"
TEXT_SECONDARY = "#52525B"
SUBTLE = "#F5F5F7"
BORDER = "#E4E4E7"


def gemini_settings() -> tuple[str, str]:
    """Liest GEMINI_API_KEY + GEMINI_MODEL aus den Streamlit-Secrets.

    Stoppt die App mit einer Fehlermeldung, wenn der Key fehlt.
    """
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY fehlt in den App-Secrets.")
        st.stop()
    model = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    return api_key, model
