"""KPI-Row — 1..N Metriken in einer Zeile via st.metric."""

from __future__ import annotations

from typing import Sequence

import streamlit as st


def render_kpis(items: Sequence[tuple[str, int | str | float]]) -> None:
    """Rendert eine Reihe `st.metric`-Cards (gleichmäßig verteilt).

    items: [(label, value), ...] — z.B. [("Offen", 12), ("Überfällig", 3), ...]
    """
    if not items:
        return
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)
