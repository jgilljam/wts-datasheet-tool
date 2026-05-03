"""Wiederverwendbarer Lieferadressen-Picker.

WICHTIG (Streamlit-Limitation): Diese Komponente MUSS außerhalb einer
`st.form` aufgerufen werden, weil Selectboxen innerhalb einer Form nicht
auf Änderungen anderer Selectboxen reagieren. Caller-Pattern:

    party_id = st.selectbox("Kunde", ...)             # außerhalb der Form
    addr_id = render_address_picker(party_id, "neu")  # außerhalb der Form
    with st.form("..."):                               # Pflichtfelder + Submit
        ...
"""

from __future__ import annotations

from typing import Any

import streamlit as st


_KIND_LABELS = {
    "billing": "Rechnungsadresse",
    "shipping": "Lieferadresse",
    "pickup": "Abholadresse",
    "registered": "Geschäftsanschrift",
}


def _format_address(a: dict[str, Any]) -> str:
    parts = []
    if a.get("label"):
        parts.append(f"[{a['label']}]")
    if a.get("kind"):
        parts.append(f"({_KIND_LABELS.get(a['kind'], a['kind'])})")
    addr = f"{a.get('street') or ''}"
    if a.get("zip") or a.get("city"):
        addr += f", {(a.get('zip') or '').strip()} {(a.get('city') or '').strip()}".strip()
    if a.get("country_code") and a.get("country_code") != "DE":
        addr += f" ({a['country_code']})"
    return f"{' '.join(parts)} {addr}".strip()


def render_address_picker(
    party_id: str | None,
    key_suffix: str,
    label: str = "Lieferadresse",
    *,
    kinds: list[str] | None = None,
    optional: bool = True,
) -> str | None:
    """Rendert einen Selectbox für Adressen einer Partei.

    Args:
        party_id: Die ID der gewählten Partei (oder None).
        key_suffix: Suffix für den Streamlit-Widget-Key (Isolation pro Form).
        label: Beschriftung der Selectbox.
        kinds: Filtere auf bestimmte Adress-Typen (z.B. ["shipping","registered"]).
               None = alle anzeigen.
        optional: Wenn True, gibt es eine "— ohne Adresse —"-Option.

    Returns:
        Die address_id oder None.
    """
    if not party_id:
        st.caption(f"ℹ️ {label}: erst Partei wählen.")
        return None

    # Lazy-Import um Circular-Import zu vermeiden
    from features.deliveries import repo as delivery_repo
    addresses = delivery_repo.list_addresses_for_party(party_id)
    if kinds:
        addresses = [a for a in addresses if a.get("kind") in kinds]

    if not addresses:
        st.warning(
            f"⚠️ Keine {label.lower()} hinterlegt. "
            "Bitte über **Parteien → Bearbeiten** anlegen."
        )
        return None

    NONE_KEY = "__none__"
    choices: dict[str, str] = {}
    if optional:
        choices[NONE_KEY] = "— ohne Adresse —"

    # Default-Adresse zuerst
    addresses_sorted = sorted(addresses, key=lambda a: (not a.get("is_default"), a.get("kind") or ""))
    for a in addresses_sorted:
        choices[a["id"]] = _format_address(a)

    selected = st.selectbox(
        label,
        list(choices.keys()),
        format_func=lambda v: choices[v],
        key=f"addr_picker_{key_suffix}",
    )
    return None if selected == NONE_KEY else selected
