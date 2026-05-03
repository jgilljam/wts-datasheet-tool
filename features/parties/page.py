"""Parteien-Stammdaten-Page: Liste · Neu anlegen · Bearbeiten (mit Adressen + Kontakten)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from core.branding import render_footer, render_header
from core.db import supabase
from core.ui.empty import render_empty_data, render_empty_filter


PARTY_TYPE_LABELS = {
    "customer": "👥 Kunde",
    "supplier": "🏭 Lieferant",
    "both": "👥🏭 Beides",
}
PARTY_TYPES = list(PARTY_TYPE_LABELS.keys())

ADDRESS_KIND_LABELS = {
    "billing": "Rechnung",
    "shipping": "Lieferung",
    "pickup": "Abholung",
    "registered": "Sitz",
}
ADDRESS_KINDS = list(ADDRESS_KIND_LABELS.keys())


# ---------- Datenzugriff ----------


@st.cache_data(ttl=30)
def _list_parties(
    type_filter: str | None = None,
    only_active: bool = True,
) -> list[dict[str, Any]]:
    q = supabase().table("parties").select("*")
    if only_active:
        q = q.eq("is_active", True)
    if type_filter == "customer":
        q = q.in_("type", ["customer", "both"])
    elif type_filter == "supplier":
        q = q.in_("type", ["supplier", "both"])
    return q.order("legal_name").execute().data


def _list_addresses(party_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("addresses")
        .select("*")
        .eq("party_id", party_id)
        .order("is_default", desc=True)
        .execute()
        .data
    )


def _list_contacts(party_id: str) -> list[dict[str, Any]]:
    return (
        supabase()
        .table("contacts")
        .select("*")
        .eq("party_id", party_id)
        .order("is_primary", desc=True)
        .execute()
        .data
    )


def _clear_caches() -> None:
    _list_parties.clear()


# ---------- Tab: Liste ----------


def _render_list_tab() -> None:
    c1, c2, c3 = st.columns([2, 2, 1])
    type_filter = c1.selectbox(
        "Typ",
        ["alle", "customer", "supplier"],
        format_func=lambda v: {
            "alle": "Alle",
            "customer": "👥 Kunden",
            "supplier": "🏭 Lieferanten",
        }[v],
        key="parties_list_type",
    )
    search = (
        c2.text_input("Suche Name", "", key="parties_list_search")
        .strip()
        .lower()
    )
    show_inactive = c3.checkbox("inkl. inaktive", key="parties_list_show_inactive")

    try:
        parties = _list_parties(
            type_filter=None if type_filter == "alle" else type_filter,
            only_active=not show_inactive,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Parteien nicht laden: {exc}")
        return

    rows: list[dict[str, Any]] = []
    customers = 0
    suppliers = 0
    for p in parties:
        if search and not (
            search in (p.get("legal_name") or "").lower()
            or search in (p.get("short_name") or "").lower()
        ):
            continue
        if p.get("type") in ("customer", "both"):
            customers += 1
        if p.get("type") in ("supplier", "both"):
            suppliers += 1
        rows.append(
            {
                "Typ": PARTY_TYPE_LABELS.get(p.get("type"), p.get("type") or ""),
                "Kurzname": p.get("short_name") or "",
                "Firmenname": p.get("legal_name") or "",
                "USt-IdNr.": p.get("vat_id") or "",
                "Zahlungsziel": (
                    f"{p['payment_terms_days']} Tage"
                    if p.get("payment_terms_days")
                    else ""
                ),
                "Aktiv": bool(p.get("is_active", True)),
            }
        )

    m1, m2, m3 = st.columns(3)
    m1.metric("Treffer", len(rows))
    m2.metric("Kunden", customers)
    m3.metric("Lieferanten", suppliers)

    if not rows:
        render_empty_filter(
            label="Keine Parteien mit diesen Filtern.",
            reset_keys=["parties_list_type", "parties_list_search", "parties_list_show_inactive"],
        )
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Typ": st.column_config.TextColumn(width="small"),
            "Kurzname": st.column_config.TextColumn(width="small"),
            "Firmenname": st.column_config.TextColumn(width="large"),
            "USt-IdNr.": st.column_config.TextColumn(width="small"),
            "Zahlungsziel": st.column_config.TextColumn(width="small"),
            "Aktiv": st.column_config.CheckboxColumn(width="small"),
        },
    )


# ---------- Tab: Neu anlegen ----------


def _render_create_tab() -> None:
    with st.form("new_party", clear_on_submit=True):
        c1, c2 = st.columns([3, 2])
        legal_name = c1.text_input("Firmenname *")
        short_name = c2.text_input("Kurzname", help="Wird in Listen/Dropdowns angezeigt.")

        c3, c4 = st.columns(2)
        ptype = c3.selectbox(
            "Typ *",
            PARTY_TYPES,
            format_func=lambda v: PARTY_TYPE_LABELS[v],
        )
        country = c4.text_input("Land (ISO-Code)", value="DE", max_chars=2)

        c5, c6, c7 = st.columns(3)
        vat_id = c5.text_input("USt-IdNr.", help="z. B. DE123456789")
        eori = c6.text_input("EORI-Nr.", help="Für Drittland-Geschäfte (Zoll).")
        payment_terms = c7.number_input(
            "Zahlungsziel (Tage)", value=0, min_value=0, max_value=365, step=7
        )

        notes = st.text_area("Notizen", height=60)

        submitted = st.form_submit_button(
            "➕ Partei anlegen", type="primary", use_container_width=True
        )

        if submitted:
            if not legal_name.strip():
                st.warning("Firmenname ist Pflicht.")
                return
            payload: dict[str, Any] = {
                "legal_name": legal_name.strip(),
                "type": ptype,
                "is_active": True,
            }
            if short_name.strip():
                payload["short_name"] = short_name.strip()
            if vat_id.strip():
                payload["vat_id"] = vat_id.strip()
            if eori.strip():
                payload["eori"] = eori.strip()
            if payment_terms > 0:
                payload["payment_terms_days"] = payment_terms
            if notes.strip():
                payload["notes"] = notes.strip()

            try:
                res = supabase().table("parties").insert(payload).execute()
                new_party = res.data[0]
            except Exception as exc:  # noqa: BLE001
                st.error(f"Anlegen fehlgeschlagen: {exc}")
                return

            # Default-Adresse anlegen, damit Lieferungs-Form was zur Auswahl hat
            try:
                supabase().table("addresses").insert({
                    "party_id": new_party["id"],
                    "kind": "billing",
                    "label": "Hauptadresse",
                    "street": "(noch nicht gepflegt)",
                    "city": "(noch nicht gepflegt)",
                    "country_code": (country.strip().upper() or "DE")[:2],
                    "is_default": True,
                }).execute()
            except Exception:  # noqa: BLE001
                pass  # nicht kritisch

            _clear_caches()
            st.success(f"Partei '{legal_name}' angelegt.")
            st.rerun()


# ---------- Tab: Bearbeiten ----------


def _render_edit_tab() -> None:
    try:
        parties = _list_parties(only_active=False)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Konnte Parteien nicht laden: {exc}")
        return

    if not parties:
        render_empty_data(
            title="Noch keine Parteien",
            description="Leg deinen ersten Kunden oder Lieferanten im Tab „Neu anlegen“ an.",
            icon="👥",
        )
        return

    options = {
        p["id"]: f"{PARTY_TYPE_LABELS.get(p.get('type'), p.get('type'))} {p['legal_name']}"
        for p in parties
    }
    selected_id = st.selectbox(
        "Partei wählen",
        list(options.keys()),
        format_func=lambda v: options[v],
        key="parties_edit_select",
    )
    if not selected_id:
        return

    party = next((p for p in parties if p["id"] == selected_id), None)
    if not party:
        return

    # ---------- Stammdaten-Form ----------
    with st.form(f"edit_party_{selected_id}", clear_on_submit=False):
        c1, c2 = st.columns([3, 2])
        legal_name = c1.text_input("Firmenname", value=party.get("legal_name") or "")
        short_name = c2.text_input("Kurzname", value=party.get("short_name") or "")

        c3, c4 = st.columns(2)
        ptype = c3.selectbox(
            "Typ",
            PARTY_TYPES,
            index=PARTY_TYPES.index(party.get("type"))
            if party.get("type") in PARTY_TYPES
            else 0,
            format_func=lambda v: PARTY_TYPE_LABELS[v],
        )
        is_active = c4.toggle("Aktiv", value=bool(party.get("is_active", True)))

        c5, c6, c7 = st.columns(3)
        vat_id = c5.text_input("USt-IdNr.", value=party.get("vat_id") or "")
        eori = c6.text_input("EORI-Nr.", value=party.get("eori") or "")
        payment_terms = c7.number_input(
            "Zahlungsziel (Tage)",
            value=int(party.get("payment_terms_days") or 0),
            min_value=0,
            max_value=365,
            step=7,
        )

        notes = st.text_area("Notizen", value=party.get("notes") or "", height=60)

        save = st.form_submit_button(
            "💾 Änderungen speichern", type="primary", use_container_width=True
        )

    if save:
        if not legal_name.strip():
            st.warning("Firmenname darf nicht leer sein.")
            return
        payload = {
            "legal_name": legal_name.strip(),
            "short_name": short_name.strip() or None,
            "type": ptype,
            "is_active": is_active,
            "vat_id": vat_id.strip() or None,
            "eori": eori.strip() or None,
            "payment_terms_days": payment_terms if payment_terms > 0 else None,
            "notes": notes.strip() or None,
        }
        try:
            supabase().table("parties").update(payload).eq("id", selected_id).execute()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Speichern fehlgeschlagen: {exc}")
            return
        _clear_caches()
        st.success(f"Gespeichert: {legal_name}")
        st.rerun()

    st.divider()

    # ---------- Adressen ----------
    st.markdown("### 📮 Adressen")
    addresses = _list_addresses(selected_id)
    if not addresses:
        st.caption("Noch keine Adressen.")
    else:
        addr_rows = [
            {
                "Art": ADDRESS_KIND_LABELS.get(a.get("kind"), a.get("kind") or ""),
                "Bezeichnung": a.get("label") or "",
                "Straße": a.get("street") or "",
                "PLZ": a.get("zip") or "",
                "Ort": a.get("city") or "",
                "Land": a.get("country_code") or "",
                "Default": "✓" if a.get("is_default") else "",
            }
            for a in addresses
        ]
        st.dataframe(pd.DataFrame(addr_rows), hide_index=True, use_container_width=True)

    with st.expander("➕ Neue Adresse anlegen"):
        with st.form(f"new_addr_{selected_id}", clear_on_submit=True):
            c1, c2 = st.columns(2)
            kind = c1.selectbox(
                "Art",
                ADDRESS_KINDS,
                format_func=lambda v: ADDRESS_KIND_LABELS[v],
                key=f"addr_kind_{selected_id}",
            )
            label = c2.text_input("Bezeichnung (z. B. 'Hauptlager', 'Filiale 2')")

            street = st.text_input("Straße + Nr.")
            c3, c4, c5 = st.columns([1, 2, 1])
            zip_code = c3.text_input("PLZ")
            city = c4.text_input("Ort")
            country = c5.text_input("Land", value="DE", max_chars=2)
            is_default = st.checkbox("Standard-Adresse")

            if st.form_submit_button("➕ Adresse anlegen", use_container_width=True):
                if not street.strip() or not city.strip():
                    st.warning("Straße und Ort sind Pflicht.")
                else:
                    try:
                        supabase().table("addresses").insert({
                            "party_id": selected_id,
                            "kind": kind,
                            "label": label.strip() or None,
                            "street": street.strip(),
                            "zip": zip_code.strip() or None,
                            "city": city.strip(),
                            "country_code": (country.strip().upper() or "DE")[:2],
                            "is_default": is_default,
                        }).execute()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Adresse anlegen fehlgeschlagen: {exc}")
                        return
                    st.success("Adresse angelegt.")
                    st.rerun()

    st.divider()

    # ---------- Kontakte ----------
    st.markdown("### 👤 Kontakte")
    contacts = _list_contacts(selected_id)
    if not contacts:
        st.caption("Noch keine Kontakte.")
    else:
        contact_rows = [
            {
                "Name": c.get("name") or "",
                "Rolle": c.get("role") or "",
                "Email": c.get("email") or "",
                "Telefon": c.get("phone") or "",
                "Mobil": c.get("mobile") or "",
                "Primär": "✓" if c.get("is_primary") else "",
            }
            for c in contacts
        ]
        st.dataframe(pd.DataFrame(contact_rows), hide_index=True, use_container_width=True)

    with st.expander("➕ Neuen Kontakt anlegen"):
        with st.form(f"new_contact_{selected_id}", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name *")
            role = c2.text_input("Rolle (z. B. Einkauf, Disposition)")

            c3, c4 = st.columns(2)
            email = c3.text_input("Email")
            phone = c4.text_input("Telefon")

            c5, c6 = st.columns(2)
            mobile = c5.text_input("Mobil")
            is_primary = c6.checkbox("Hauptkontakt", value=False)

            if st.form_submit_button("➕ Kontakt anlegen", use_container_width=True):
                if not name.strip():
                    st.warning("Name ist Pflicht.")
                else:
                    try:
                        supabase().table("contacts").insert({
                            "party_id": selected_id,
                            "name": name.strip(),
                            "role": role.strip() or None,
                            "email": email.strip() or None,
                            "phone": phone.strip() or None,
                            "mobile": mobile.strip() or None,
                            "is_primary": is_primary,
                        }).execute()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Kontakt anlegen fehlgeschlagen: {exc}")
                        return
                    st.success("Kontakt angelegt.")
                    st.rerun()


# ---------- Entry ----------


def render() -> None:
    render_header("Parteien", "Stammdaten · Kunden · Lieferanten · Adressen · Kontakte")

    tab_list, tab_new, tab_edit = st.tabs(
        ["📋 Liste", "➕ Neu anlegen", "✏️ Bearbeiten"]
    )
    with tab_list:
        _render_list_tab()
    with tab_new:
        _render_create_tab()
    with tab_edit:
        _render_edit_tab()

    render_footer()
