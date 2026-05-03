"""Status-Visualisierung — Pill (einzeln) + horizontaler Stepper."""

from __future__ import annotations

import html as html_lib
from typing import Iterable

import streamlit as st


def render_status_pill(
    status: str,
    labels: dict[str, str],
    colors: dict[str, str],
    *,
    size: str = "md",
) -> str:
    """Liefert das HTML einer Status-Pill (kein direktes st.markdown — Caller entscheidet)."""
    label = labels.get(status, status or "—")
    color = colors.get(status, "#6B7280")
    pad = "3px 12px" if size == "md" else "2px 8px"
    fs = "0.85rem" if size == "md" else "0.72rem"
    return (
        f'<span style="display:inline-block;padding:{pad};'
        f"background:{color}22;border:1px solid {color};color:{color};"
        f"border-radius:999px;font-size:{fs};font-weight:600;"
        f'letter-spacing:0.02em;">{html_lib.escape(label)}</span>'
    )


def render_status_stepper(
    flow: Iterable[str],
    current: str,
    labels: dict[str, str],
    colors: dict[str, str],
    *,
    terminal_states: set[str] | None = None,
) -> None:
    """Horizontaler Stepper: passierte Stati gefüllt-farbig, current dick umrandet, künftige grau.

    `terminal_states`: Stati wie 'cancelled' / 'returned', die quer zum normalen Flow stehen — werden
    am Ende als rote Pille gezeigt, wenn aktuell.
    """
    flow_list = [s for s in flow if s not in (terminal_states or set())]
    if current in (terminal_states or set()):
        # Sonderzustand: zeig nur die Pill, kein Stepper
        pill_html = render_status_pill(current, labels, colors, size="md")
        st.markdown(
            f'<div style="margin:0.5rem 0;">{pill_html}</div>',
            unsafe_allow_html=True,
        )
        return

    try:
        cur_idx = flow_list.index(current)
    except ValueError:
        cur_idx = -1

    parts: list[str] = []
    for i, s in enumerate(flow_list):
        label = labels.get(s, s)
        color = colors.get(s, "#9CA3AF")
        if i < cur_idx:
            # passiert
            chip = (
                f'<div style="flex:1;text-align:center;padding:6px 4px;'
                f"background:{color};color:white;border-radius:6px;"
                f'font-size:0.72rem;font-weight:600;">'
                f"✓ {html_lib.escape(label)}</div>"
            )
        elif i == cur_idx:
            # aktuell
            chip = (
                f'<div style="flex:1;text-align:center;padding:6px 4px;'
                f"background:{color};color:white;border-radius:6px;"
                f"font-size:0.78rem;font-weight:700;"
                f'box-shadow:0 0 0 2px {color}55;">'
                f"● {html_lib.escape(label)}</div>"
            )
        else:
            # künftig
            chip = (
                f'<div style="flex:1;text-align:center;padding:6px 4px;'
                f"background:#F3F4F6;color:#9CA3AF;border-radius:6px;"
                f"font-size:0.72rem;font-weight:500;"
                f'border:1px dashed #D1D5DB;">'
                f"{html_lib.escape(label)}</div>"
            )
        parts.append(chip)
        if i < len(flow_list) - 1:
            parts.append('<div style="width:6px;color:#D1D5DB;align-self:center;">›</div>')

    html = (
        '<div style="display:flex;align-items:stretch;gap:2px;'
        'margin:0.5rem 0 1rem 0;flex-wrap:wrap;">'
        + "".join(parts)
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
