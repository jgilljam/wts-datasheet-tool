"""Smart Empty-States mit CTAs.

Zwei Varianten:
  - render_empty_filter() — Liste hat Daten, aktuelle Filter blenden alles aus.
    Bietet einen "Filter zurücksetzen"-Button.
  - render_empty_data() — Tabelle ist komplett leer. Bietet eine größere Card
    mit Icon + Beschreibung + optionalem CTA.
"""

from __future__ import annotations

import html as html_lib
from typing import Callable

import streamlit as st


def render_empty_filter(
    *,
    label: str = "Keine Treffer mit diesen Filtern.",
    reset_keys: list[str],
    button_label: str = "Filter zurücksetzen",
    extra_caption: str | None = None,
) -> None:
    """Hint + Reset-Button. `reset_keys` = session_state-Keys, die geleert werden."""
    c1, c2 = st.columns([3, 1])
    c1.info(label)
    if c2.button(button_label, key=f"reset_{'_'.join(reset_keys)}", use_container_width=True):
        for k in reset_keys:
            st.session_state.pop(k, None)
        st.rerun()
    if extra_caption:
        st.caption(extra_caption)


def render_empty_data(
    *,
    title: str,
    description: str,
    icon: str = "📭",
    cta_label: str | None = None,
    cta_callback: Callable[[], None] | None = None,
    cta_help: str | None = None,
) -> None:
    """Card-style Empty-State für komplett leere Tabellen."""
    title_html = html_lib.escape(title)
    desc_html = html_lib.escape(description)
    icon_html = html_lib.escape(icon)
    box = st.container(border=True)
    with box:
        st.markdown(
            f'<div style="text-align:center;padding:1.5rem 1rem 0.5rem 1rem;">'
            f'<div style="font-size:2.5rem;line-height:1;margin-bottom:.5rem;">{icon_html}</div>'
            f'<h3 style="margin:0 0 .35rem 0;">{title_html}</h3>'
            f'<div style="color:#52525B;margin-bottom:.75rem;">{desc_html}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
        if cta_label and cta_callback is not None:
            c1, c2, c3 = st.columns([1, 2, 1])
            with c2:
                if st.button(
                    cta_label, type="primary",
                    use_container_width=True, help=cta_help,
                    key=f"empty_cta_{cta_label}",
                ):
                    cta_callback()
