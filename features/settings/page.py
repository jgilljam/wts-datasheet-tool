"""Firmen-Einstellungen — Single-Row-Datensatz pflegen."""

from __future__ import annotations

import streamlit as st

from core import app_settings as cfg
from core.branding import render_footer, render_header
from core.db import supabase
from features.invoices import repo as inv_repo


def _load_settings() -> dict:
    res = supabase().table("company_settings").select("*").limit(1).execute()
    if res.data:
        return res.data[0]
    # Falls leer — Default-Datensatz anlegen
    res = (
        supabase()
        .table("company_settings")
        .insert({"legal_name": "Weber Trading & Service"})
        .execute()
    )
    return res.data[0]


def _save_settings(settings_id: str, changes: dict) -> None:
    # Strings: leer → None damit DB konsistente NULLs hat
    cleaned = {}
    for k, v in changes.items():
        if isinstance(v, str):
            cleaned[k] = v.strip() or None
        else:
            cleaned[k] = v
    supabase().table("company_settings").update(cleaned).eq("id", settings_id).execute()
    inv_repo.clear_company_settings_cache()


def _completeness_check(s: dict) -> tuple[int, int, list[str]]:
    """Liefert (filled, total, missing_critical_fields)."""
    critical = {
        "legal_name": "Firmenname",
        "street": "Straße",
        "zip": "PLZ",
        "city": "Stadt",
        "tax_number": "Steuer-Nr.",
        "vat_id": "USt-IdNr.",
        "iban": "IBAN",
        "bic": "BIC",
        "managing_director": "Geschäftsführer",
        "hr_register": "HR-Eintrag",
        "phone": "Telefon",
        "email": "Email",
    }
    missing = [label for k, label in critical.items() if not (s.get(k) or "").strip()]
    return len(critical) - len(missing), len(critical), missing


def render() -> None:
    render_header(
        "Einstellungen",
        "Firmen-Stammdaten · Bankverbindung · Zoll · Mahnwesen",
    )

    settings = _load_settings()
    sid = settings["id"]

    filled, total, missing = _completeness_check(settings)
    if missing:
        st.warning(
            f"**{filled}/{total} Pflichtfelder ausgefüllt.** Es fehlen: "
            + ", ".join(missing)
            + ". Diese Felder erscheinen in allen Belegen (Footer/Header) und sind "
            "rechtlich teilweise vorgeschrieben (UStG §14)."
        )
    else:
        st.success(f"✅ Alle {total} relevanten Pflichtfelder sind ausgefüllt.")

    tab_firma, tab_kontakt, tab_steuer, tab_bank, tab_mahn, tab_pipeline = st.tabs(
        ["🏢 Firma", "📞 Kontakt", "💼 Steuer & HR", "🏦 Bank", "📨 Mahnwesen", "🤖 Mail-Pipeline"]
    )

    with tab_firma:
        st.subheader("Firmenadresse")
        st.caption("Erscheint im Briefkopf aller Belege.")
        with st.form("settings_firma"):
            c1, c2 = st.columns(2)
            legal_name = c1.text_input("Firmenname (rechtlich)", value=settings.get("legal_name") or "")
            country = c2.text_input("Land (ISO-Code)", value=settings.get("country_code") or "DE",
                                     max_chars=2)
            street = c1.text_input("Straße + Hausnummer", value=settings.get("street") or "")
            zip_ = c2.text_input("PLZ", value=settings.get("zip") or "")
            city = st.text_input("Stadt", value=settings.get("city") or "")
            website = st.text_input("Website", value=settings.get("website") or "",
                                     placeholder="https://wts-trading.de")
            if st.form_submit_button("💾 Firma speichern", type="primary"):
                _save_settings(sid, {
                    "legal_name": legal_name,
                    "street": street,
                    "zip": zip_,
                    "city": city,
                    "country_code": country.upper() or "DE",
                    "website": website,
                })
                st.toast("Firma gespeichert.", icon="✅")
                st.rerun()

    with tab_kontakt:
        st.subheader("Kontaktdaten")
        st.caption("Erscheint im Header (Phone/Email/Fax) und Footer aller Belege.")
        with st.form("settings_kontakt"):
            c1, c2 = st.columns(2)
            phone = c1.text_input("Telefon", value=settings.get("phone") or "",
                                   placeholder="+49 2161 1234567")
            fax = c2.text_input("Fax", value=settings.get("fax") or "")
            email = c1.text_input("Email (allgemein)", value=settings.get("email") or "",
                                   placeholder="info@wts-trading.de")
            if st.form_submit_button("💾 Kontakt speichern", type="primary"):
                _save_settings(sid, {
                    "phone": phone,
                    "fax": fax,
                    "email": email,
                })
                st.toast("Kontakt gespeichert.", icon="✅")
                st.rerun()

    with tab_steuer:
        st.subheader("Steuer & Handelsregister")
        st.caption(
            "USt-IdNr. ist bei innergemeinschaftlichen Lieferungen Pflicht. "
            "HRB-Eintrag und Geschäftsführer sind bei UG/GmbH/AG vorgeschrieben (§35a HGB)."
        )
        with st.form("settings_steuer"):
            c1, c2 = st.columns(2)
            tax_number = c1.text_input(
                "Steuer-Nr. (Finanzamt)",
                value=settings.get("tax_number") or "",
                placeholder="123/456/78901",
            )
            tax_office = c2.text_input(
                "Finanzamt-Name",
                value=settings.get("tax_office") or "",
                placeholder="Finanzamt Mönchengladbach",
            )
            vat_id = c1.text_input(
                "USt-IdNr.",
                value=settings.get("vat_id") or "",
                placeholder="DE123456789",
                help="Format: 2 Buchstaben Ländercode + 9-12 Ziffern.",
            )
            eori = c2.text_input(
                "EORI-Nr. (Zoll)",
                value=settings.get("eori") or "",
                placeholder="DE123456789012345",
                help="Optional — bei Drittland-Lieferungen / DAP/DDP relevant.",
            )
            md = c1.text_input(
                "Geschäftsführer",
                value=settings.get("managing_director") or "",
                placeholder="Julian Gilljam",
            )
            hr = c2.text_input(
                "HR-Eintrag",
                value=settings.get("hr_register") or "",
                placeholder="HRB 12345 Amtsgericht Mönchengladbach",
            )
            if st.form_submit_button("💾 Steuer & HR speichern", type="primary"):
                _save_settings(sid, {
                    "tax_number": tax_number,
                    "tax_office": tax_office,
                    "vat_id": vat_id.upper().replace(" ", ""),
                    "eori": eori.upper().replace(" ", ""),
                    "managing_director": md,
                    "hr_register": hr,
                })
                st.toast("Steuer-Daten gespeichert.", icon="✅")
                st.rerun()

    with tab_bank:
        st.subheader("Bank-Verbindung")
        st.caption(
            "Erscheint auf Rechnungen und Mahnungen — der Kunde nutzt diese Daten "
            "für die Überweisung."
        )
        with st.form("settings_bank"):
            c1, c2 = st.columns(2)
            bank = c1.text_input(
                "Bank-Name",
                value=settings.get("bank_name") or "",
                placeholder="Volksbank Mönchengladbach",
            )
            iban = c2.text_input(
                "IBAN",
                value=settings.get("iban") or "",
                placeholder="DE89 3704 0044 0532 0130 00",
            )
            bic = c1.text_input(
                "BIC / SWIFT",
                value=settings.get("bic") or "",
                placeholder="GENODED1MGL",
            )
            if st.form_submit_button("💾 Bank speichern", type="primary"):
                _save_settings(sid, {
                    "bank_name": bank,
                    "iban": iban.upper().replace(" ", ""),
                    "bic": bic.upper().replace(" ", ""),
                })
                st.toast("Bank gespeichert.", icon="✅")
                st.rerun()

    with tab_mahn:
        st.subheader("Mahnwesen")
        st.caption(
            "Standard-Mahngebühren pro Stufe. Wird beim Erstellen einer Mahnung "
            "automatisch verrechnet (kann pro Mahnung manuell überschrieben werden)."
        )
        with st.form("settings_mahn"):
            c1, c2 = st.columns(2)
            l1 = c1.number_input(
                "Stufe 1 (Erinnerung) — Gebühr in Cent",
                min_value=0, max_value=10000, step=100,
                value=int(settings.get("dunning_fee_l1_cents") or 0),
                help="Bei freundlicher Erinnerung üblicherweise 0 €.",
            )
            l2 = c2.number_input(
                "Stufe 2 (1. Mahnung) — Gebühr in Cent",
                min_value=0, max_value=10000, step=100,
                value=int(settings.get("dunning_fee_l2_cents") or 500),
                help="Üblich: 500 (= 5,00 €).",
            )
            l3 = c1.number_input(
                "Stufe 3 (2. Mahnung) — Gebühr in Cent",
                min_value=0, max_value=10000, step=100,
                value=int(settings.get("dunning_fee_l3_cents") or 1500),
                help="Üblich: 1500 (= 15,00 €).",
            )
            grace = c2.number_input(
                "Zahlungsfrist nach Mahnung (Tage)",
                min_value=1, max_value=30, step=1,
                value=int(settings.get("dunning_grace_days") or 7),
            )
            if st.form_submit_button("💾 Mahn-Defaults speichern", type="primary"):
                _save_settings(sid, {
                    "dunning_fee_l1_cents": l1,
                    "dunning_fee_l2_cents": l2,
                    "dunning_fee_l3_cents": l3,
                    "dunning_grace_days": grace,
                })
                st.toast("Mahnwesen gespeichert.", icon="✅")
                st.rerun()

    with tab_pipeline:
        st.subheader("Mail-Pipeline")
        st.caption(
            "Steuert was die KI beim eingehenden Mail-Pull automatisch macht. "
            "Werte hier gewinnen über `secrets.toml` — du brauchst keinen Deploy für Anpassungen."
        )

        cur_classify = cfg.get_bool("mail.auto_classify", default=True, secret_fallback="MAIL_AUTO_CLASSIFY")
        cur_convert = cfg.get_bool("mail.auto_convert", default=False, secret_fallback="MAIL_AUTO_CONVERT")
        cur_min_conf = cfg.get_str(
            "mail.auto_convert_min_confidence",
            default="high",
            secret_fallback="MAIL_AUTO_CONVERT_MIN_CONFIDENCE",
        )
        actor = (st.session_state.get("user") or {}).get("email")

        with st.form("settings_pipeline"):
            st.markdown("**Auto-KI**")
            auto_classify = st.toggle(
                "🤖 KI direkt beim Pull starten",
                value=cur_classify,
                help=(
                    "Aktiv: jede neue Mail wird direkt nach dem IMAP-Pull klassifiziert + extrahiert. "
                    "Aus: KI nur manuell per Knopf in der Mail-Detail-Ansicht."
                ),
            )

            st.markdown("**Auto-Convert** (Beleg-Anlage ohne User-Klick)")
            st.caption(
                "🔒 Sicherheits-Schutz: Auto-Convert läuft nur bei Domain-Match (Absender-Domain ≠ "
                "Freemail-Provider) UND Konfidenz ≥ Mindeststufe. Mails werden NICHT versendet — "
                "nur Drafts angelegt."
            )
            auto_convert = st.toggle(
                "⚡ Bei high-Konfidenz Auftrag automatisch anlegen",
                value=cur_convert,
                help=(
                    "Aktiv: bei Sales-Order mit Konfidenz ≥ Mindeststufe und bekannter Domain wird "
                    "direkt ein Auftrag-Draft erzeugt. Aus: Auftrag entsteht erst nach Klick auf "
                    "'→ Auftrag (Draft) anlegen' in der Posteingang-UI."
                ),
            )
            min_conf = st.select_slider(
                "Mindest-Konfidenz für Auto-Convert",
                options=["low", "medium", "high"],
                value=cur_min_conf if cur_min_conf in ("low", "medium", "high") else "high",
                disabled=not auto_convert,
                help="Empfehlung: 'high' — sonst können Drafts mit unklaren Mengen entstehen.",
            )

            if st.form_submit_button("💾 Pipeline-Settings speichern", type="primary"):
                cfg.set_value("mail.auto_classify", auto_classify, actor_email=actor)
                cfg.set_value("mail.auto_convert", auto_convert, actor_email=actor)
                cfg.set_value("mail.auto_convert_min_confidence", min_conf, actor_email=actor)
                st.toast("Pipeline-Settings gespeichert.", icon="✅")
                st.rerun()

        st.divider()
        st.caption(
            f"Aktive Werte: classify={cur_classify} · convert={cur_convert} · min_conf={cur_min_conf}"
        )

    st.divider()
    with st.expander("🔍 Aktueller Datensatz (Debug)"):
        st.json({k: v for k, v in settings.items() if k not in ("id", "created_at", "updated_at")})

    render_footer()
