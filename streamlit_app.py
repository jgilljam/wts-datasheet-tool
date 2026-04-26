"""WTS Datenblatt-Tool — Web-UI für Mitarbeiter.

Hersteller-PDF reinziehen → strukturierte Komponente + WTS-gebrandete Datenblätter (DE/EN).
"""

import base64
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

import streamlit as st

from lib.generator import render_pdf_bytes
from lib.library_search import LibraryEntry, find_best_match
from lib.normalizer import NormalizerError, normalize, normalize_from_description
from lib.pdf_extract import extract_text_from_uploaded
from lib.pdf_fetch import PdfFetchError, fetch_pdf
from lib.web_search import WebSearchError, WebSuggestion, search_components


ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "logo.png"

PRIMARY = "#0A2540"
ACCENT = "#D84B41"
ANTHRACITE = "#1A1918"
TEXT_SECONDARY = "#52525B"
SUBTLE = "#F5F5F7"
BORDER = "#E4E4E7"


# ---------- Page config ----------

st.set_page_config(
    page_title="WTS Datenblatt-Tool",
    page_icon=str(LOGO_PATH),
    layout="centered",
    initial_sidebar_state="collapsed",
)


# ---------- Custom CSS ----------

st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 2rem; padding-bottom: 4rem; max-width: 880px; }}
      h1, h2, h3 {{ letter-spacing: -0.02em; color: {PRIMARY}; }}
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
      .wts-pill.accent {{ color: {ACCENT}; border-color: {ACCENT}; }}
      .wts-card {{
        background: {SUBTLE};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1rem;
      }}
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
      /* hide hamburger + footer */
      [data-testid="stToolbar"] {{ display: none; }}
      footer {{ visibility: hidden; }}
      header[data-testid="stHeader"] {{ background: transparent; }}

      /* PDF iframe */
      iframe.wts-pdf-preview {{
        width: 100%;
        height: 720px;
        border: 1px solid {BORDER};
        border-radius: 8px;
      }}

      /* Login centering */
      .wts-login-wrap {{
        max-width: 400px;
        margin: 4rem auto 0 auto;
        padding: 2rem;
        border: 1px solid {BORDER};
        border-radius: 16px;
        background: white;
      }}
      .wts-login-wrap img {{ height: 56px; display: block; margin: 0 auto 1.5rem auto; }}
      .wts-login-wrap h2 {{ text-align: center; font-size: 1.2rem; margin-bottom: 0.25rem; }}
      .wts-login-wrap .sub {{ text-align: center; color: {TEXT_SECONDARY}; font-size: 0.85rem; margin-bottom: 1.5rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- Helpers ----------

@st.cache_data
def _logo_b64() -> str:
    return base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")


def _slugify(name: str) -> str:
    s = name.lower()
    s = (s.replace("ä", "ae").replace("ö", "oe")
          .replace("ü", "ue").replace("ß", "ss"))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "komponente"


# ---------- Auth ----------

def _check_password() -> bool:
    expected = st.secrets.get("APP_PASSWORD")
    if not expected:
        st.error(
            "Konfigurationsfehler: APP_PASSWORD ist nicht gesetzt. "
            "Admin: in `.streamlit/secrets.toml` (lokal) oder App-Settings (Cloud) hinterlegen."
        )
        st.stop()

    if st.session_state.get("authed"):
        return True

    st.markdown(
        f"""
        <div class="wts-login-wrap">
          <img src="data:image/png;base64,{_logo_b64()}" alt="WTS">
          <h2>Datenblatt-Tool</h2>
          <div class="sub">Anmeldung für WTS-Mitarbeiter</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("login", clear_on_submit=False):
            pwd = st.text_input("Passwort", type="password", label_visibility="collapsed", placeholder="Passwort")
            ok = st.form_submit_button("Anmelden", use_container_width=True, type="primary")
        if ok:
            if pwd == expected:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Falsches Passwort.")
    return False


if not _check_password():
    st.stop()


# ---------- Header ----------

header_col, logout_col = st.columns([5, 1])
with header_col:
    st.markdown(
        f"""
        <div class="wts-header">
          <img src="data:image/png;base64,{_logo_b64()}" alt="WTS">
          <div class="wts-header-text">
            <h1>Datenblatt-Tool</h1>
            <div class="sub">PDF oder Stichworte → WTS-Komponente + Datenblätter (DE/EN)</div>
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


# ---------- Settings ----------

api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("GEMINI_API_KEY fehlt in den App-Secrets.")
    st.stop()
model = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


# ---------- Mode + Input ----------

MODE_PDF = "📄 PDF hochladen"
MODE_TEXT = "✍️ Komponente beschreiben"
MODE_WIZARD = "🔍 Anfrage suchen"

mode = st.radio(
    "Quelle",
    [MODE_PDF, MODE_TEXT, MODE_WIZARD],
    horizontal=True,
    label_visibility="collapsed",
    key="input_mode",
)

uploaded = None
description = ""
# Wizard kann am Ende einen "synthetischen" key setzen, der durch die normale Pipeline läuft
wizard_prefilled_key: str | None = None

if mode == MODE_PDF:
    uploaded = st.file_uploader(
        "PDF hierher ziehen oder auswählen",
        type=["pdf"],
        accept_multiple_files=False,
        label_visibility="visible",
    )
    if uploaded is None:
        st.markdown(
            f"""
            <div class="wts-card">
              <div class="wts-eyebrow">So funktioniert's</div>
              <ol style="margin: 8px 0 0 1.2rem; color: {ANTHRACITE};">
                <li>Hersteller-Datenblatt als PDF hochladen</li>
                <li>Tool extrahiert Text, KI strukturiert die Komponente, Leitplanken werden geprüft</li>
                <li>JSON für die Website + WTS-PDF auf Deutsch und Englisch herunterladen</li>
              </ol>
              <div style="margin-top: 1rem; font-size: 0.82rem; color: {TEXT_SECONDARY};">
                Maximale Dateigröße: 25 MB · Max. ~30 Seiten Text werden ausgewertet
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
elif mode == MODE_TEXT:
    placeholder = (
        "z.B.\n"
        "Silikon-Heizmatte, 230V, 100W, 200x300mm, selbstklebende Rückseite,\n"
        "für Frostschutz an Außengehäusen und Wärmepumpen-Kondensatoren.\n"
        "\n"
        "Tipp: Je mehr Stichworte du gibst (Spannung, Leistung, Maße, Material,\n"
        "Anwendung), desto präziser ist das Ergebnis. Was du nicht angibst,\n"
        "ergänzt die KI mit branchenüblichen Default-Werten."
    )
    with st.form("describe_form"):
        description = st.text_area(
            "Beschreibe die Komponente in Stichworten",
            value="",
            height=200,
            placeholder=placeholder,
        )
        submitted = st.form_submit_button(
            "Komponente generieren", type="primary", use_container_width=True
        )
    if not (submitted and description.strip()):
        st.markdown(
            f"""
            <div class="wts-card">
              <div class="wts-eyebrow">So funktioniert's</div>
              <ol style="margin: 8px 0 0 1.2rem; color: {ANTHRACITE};">
                <li>Stichworte zur Komponente eingeben (Typ, Spannung, Maße, Material, Anwendung …)</li>
                <li>KI generiert eine vollständige Komponente — fehlende Felder werden plausibel ergänzt</li>
                <li>Im Tab „Bearbeiten" gegenchecken, dann JSON + DE/EN-PDF herunterladen</li>
              </ol>
              <div style="margin-top: 1rem; font-size: 0.82rem; color: {TEXT_SECONDARY};">
                Hinweis: Werte ohne explizite Angabe sind KI-Schätzungen — vor dem Veröffentlichen prüfen.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
else:  # MODE_WIZARD — dreistufiger Anfrage-Wizard
    import io as _io
    from lib.schema import Komponente as _Komponente
    from lib.normalizer import _finalize as _finalize_komponente

    results = st.session_state.setdefault("results", {})
    wiz = st.session_state.setdefault("wizard", {
        "query": "",
        "lib_done": False,
        "lib_match": None,   # tuple (LibraryEntry|None, LibraryMatch)
        "web_done": False,
        "web_results": [],   # list[WebSuggestion]
        "selected_key": None,
    })

    with st.form("wizard_form"):
        query = st.text_area(
            "Kundenanfrage / gesuchte Komponente",
            value=wiz.get("query", ""),
            height=140,
            placeholder=(
                "z.B. Kunde fragt nach Heizpatrone 12V 50W mit Silikonkabel,\n"
                "Schutzart IP67, für Außenmontage am Schaltschrank."
            ),
        )
        cols = st.columns([3, 1])
        with cols[0]:
            wiz_submit = st.form_submit_button(
                "🔍 Suche starten", type="primary", use_container_width=True,
            )
        with cols[1]:
            wiz_reset = st.form_submit_button(
                "↺ Zurücksetzen", use_container_width=True,
            )

    if wiz_reset:
        st.session_state["wizard"] = {
            "query": "", "lib_done": False, "lib_match": None,
            "web_done": False, "web_results": [], "selected_key": None,
        }
        st.rerun()

    if wiz_submit and query.strip():
        # Neue Anfrage → State zurücksetzen, Stufe 1 starten
        wiz.update({
            "query": query.strip(),
            "lib_done": False, "lib_match": None,
            "web_done": False, "web_results": [],
            "selected_key": None,
        })

    if not wiz["query"]:
        st.markdown(
            f"""
            <div class="wts-card">
              <div class="wts-eyebrow">So funktioniert's</div>
              <ol style="margin: 8px 0 0 1.2rem; color: {ANTHRACITE};">
                <li>Anfrage eingeben — Tool prüft erst die eigene Bibliothek</li>
                <li>Falls nichts passt: Web-Recherche bei Herstellern</li>
                <li>Falls auch da nichts: KI generiert aus der Anfrage ein plausibles Datenblatt</li>
              </ol>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()

    # ---- Stufe 1: Bibliothek ----
    if not wiz["lib_done"]:
        with st.spinner("Bibliothek prüfen …"):
            try:
                entry, match = find_best_match(wiz["query"], api_key=api_key, model=model)
                wiz["lib_match"] = (entry, match)
            except Exception as e:
                st.warning(f"Bibliothek-Lookup fehlgeschlagen: {e}")
                wiz["lib_match"] = (None, None)
        wiz["lib_done"] = True

    entry, match = wiz["lib_match"] if wiz["lib_match"] else (None, None)
    if entry is not None:
        st.markdown(
            f"""
            <div class="wts-card" style="border-left: 4px solid {ACCENT};">
              <div class="wts-eyebrow">Stufe 1 · Treffer in der Bibliothek (Score {match.score})</div>
              <h3 style="margin: 6px 0 4px 0;">{entry.data.get('titel','')}</h3>
              <div style="color: {TEXT_SECONDARY}; margin-bottom: 8px;">{entry.data.get('kurzbeschreibung','')}</div>
              <div style="font-size: 0.85rem;">{match.begruendung}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns([2, 1])
        with c1:
            if st.button("✓ Diese Komponente übernehmen", type="primary", use_container_width=True):
                wiz["selected_key"] = f"wiz::lib::{entry.slug}"
        with c2:
            if st.button("Weiter zur Web-Suche", use_container_width=True):
                # explizit weiter, ignoriere Bibliotheks-Match
                wiz["lib_match"] = (None, match)
                st.rerun()
    elif match and match.score > 0:
        st.info(f"Bibliothek: kein passender Treffer ({match.begruendung})")

    # ---- Stufe 2: Web-Suche ----
    if entry is None and wiz["selected_key"] is None:
        if not wiz["web_done"]:
            with st.spinner("Web-Recherche bei Herstellern …"):
                try:
                    wiz["web_results"] = search_components(
                        wiz["query"], api_key=api_key, model=model,
                    )
                except WebSearchError as e:
                    st.warning(f"Web-Suche fehlgeschlagen: {e}")
                    wiz["web_results"] = []
                except Exception as e:
                    st.warning(f"Web-Suche fehlgeschlagen: {type(e).__name__}: {e}")
                    wiz["web_results"] = []
            wiz["web_done"] = True

        treffer: list[WebSuggestion] = wiz["web_results"]
        if treffer:
            st.markdown(
                f'<div class="wts-eyebrow" style="margin-top: 1.5rem;">Stufe 2 · Web-Treffer ({len(treffer)})</div>',
                unsafe_allow_html=True,
            )
            for idx, t in enumerate(treffer):
                with st.container():
                    has_pdf = bool(t.datenblatt_url and t.datenblatt_url.lower().startswith(("http://", "https://")))
                    badge = "📄 mit Datenblatt-PDF" if has_pdf else "ℹ️ Beschreibung (kein direktes PDF)"
                    st.markdown(
                        f"""
                        <div class="wts-card">
                          <div class="wts-eyebrow">{badge}</div>
                          <h3 style="margin: 6px 0 4px 0;">{t.hersteller} — {t.modell}</h3>
                          <div style="color: {TEXT_SECONDARY}; margin-bottom: 8px;">{t.kurzbeschreibung}</div>
                          <div style="font-size: 0.78rem;">
                            <a href="{t.quelle_url}" target="_blank">↗ Quelle</a>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "✓ Datenblatt aus diesem Treffer erstellen",
                        key=f"web_pick_{idx}",
                        type="primary",
                    ):
                        wiz["selected_key"] = f"wiz::web::{idx}"
        else:
            st.info("Keine verwertbaren Web-Treffer gefunden.")

        # Stufe 3: Fallback-Button
        st.divider()
        st.markdown(
            f'<div class="wts-eyebrow">Stufe 3 · Fallback</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "✨ Stattdessen direkt aus der Anfrage generieren (KI)",
            use_container_width=True,
        ):
            wiz["selected_key"] = "wiz::gen"

    # ---- Pipeline für die Auswahl ----
    if wiz["selected_key"] is None:
        st.stop()

    sel = wiz["selected_key"]
    if sel not in results:
        with st.status("Komponente vorbereiten …", expanded=True) as status:
            try:
                if sel.startswith("wiz::lib::"):
                    slug = sel.removeprefix("wiz::lib::")
                    entry, _ = wiz["lib_match"]
                    # entry kann None sein, wenn der User „Weiter zur Web-Suche" geklickt hat
                    # → bei lib::-Auswahl dürfte das aber nicht vorkommen
                    if entry is None or entry.slug != slug:
                        from lib.library_search import load_library
                        entry = next((e for e in load_library() if e.slug == slug), None)
                    if entry is None:
                        status.update(label="Bibliotheks-Eintrag nicht mehr gefunden", state="error")
                        st.stop()
                    data_dict = entry.data
                    warnings = [f"Aus Bibliothek übernommen: {entry.slug}"]
                    st.write("📚  Bibliotheks-JSON geladen")

                elif sel.startswith("wiz::web::"):
                    idx = int(sel.removeprefix("wiz::web::"))
                    t = wiz["web_results"][idx]
                    if t.datenblatt_url and t.datenblatt_url.lower().startswith(("http://", "https://")):
                        st.write(f"⬇️  PDF holen von {t.hersteller} …")
                        try:
                            pdf_bytes = fetch_pdf(t.datenblatt_url)
                            text = extract_text_from_uploaded(_io.BytesIO(pdf_bytes))
                            st.write("🤖  Hersteller-Datenblatt normalisieren …")
                            komponente, warnings = normalize(text, api_key=api_key, model=model)
                            warnings = [f"Aus Hersteller-Datenblatt: {t.hersteller} — {t.modell}"] + warnings
                            data_dict = komponente.model_dump(exclude_none=True)
                        except (PdfFetchError, ValueError) as e:
                            st.warning(f"PDF-Pfad fehlgeschlagen ({e}) — fallback auf Beschreibungs-Modus.")
                            enriched = (
                                f"Hersteller: {t.hersteller}\nModell: {t.modell}\n"
                                f"Beschreibung: {t.kurzbeschreibung}\n"
                                f"Ursprüngliche Anfrage: {wiz['query']}"
                            )
                            st.write("🤖  Komponente aus Web-Beschreibung generieren …")
                            komponente, warnings = normalize_from_description(
                                enriched, api_key=api_key, model=model,
                            )
                            warnings = [f"Aus Web-Recherche (kein PDF): {t.hersteller} — {t.modell}"] + warnings
                            data_dict = komponente.model_dump(exclude_none=True)
                    else:
                        enriched = (
                            f"Hersteller: {t.hersteller}\nModell: {t.modell}\n"
                            f"Beschreibung: {t.kurzbeschreibung}\n"
                            f"Ursprüngliche Anfrage: {wiz['query']}"
                        )
                        st.write("🤖  Komponente aus Web-Beschreibung generieren …")
                        komponente, warnings = normalize_from_description(
                            enriched, api_key=api_key, model=model,
                        )
                        warnings = [f"Aus Web-Recherche (kein PDF): {t.hersteller} — {t.modell}"] + warnings
                        data_dict = komponente.model_dump(exclude_none=True)

                else:  # wiz::gen
                    st.write("🤖  Komponente aus Anfrage generieren …")
                    komponente, warnings = normalize_from_description(
                        wiz["query"], api_key=api_key, model=model,
                    )
                    data_dict = komponente.model_dump(exclude_none=True)

            except NormalizerError as e:
                status.update(label="Leitplanken-Verstoß", state="error")
                st.error(str(e))
                st.stop()
            except Exception as e:
                msg = str(e)
                is_rate_limit = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
                if is_rate_limit:
                    status.update(label="KI-Tageslimit erreicht", state="error")
                    st.error(
                        "**Das KI-Tageslimit ist erreicht.** Morgen erneut probieren oder "
                        "Billing in der Google-AI-Console aktivieren."
                    )
                else:
                    status.update(label="Fehler", state="error")
                    st.error(f"_Technisch: {type(e).__name__}: {msg[:200]}_")
                st.stop()

            try:
                st.write("🎨  Rendere DE-PDF …")
                pdf_de = render_pdf_bytes(data_dict, lang="de")
                st.write("🎨  Rendere EN-PDF …")
                pdf_en = render_pdf_bytes(data_dict, lang="en")
            except Exception as e:
                status.update(label="PDF-Render-Fehler", state="error")
                st.error(f"{type(e).__name__}: {e}")
                st.stop()

            results[sel] = {
                "data": data_dict, "warnings": warnings,
                "pdf_de": pdf_de, "pdf_en": pdf_en,
            }
            status.update(label="Fertig — Komponente unten", state="complete")

    wizard_prefilled_key = sel


# ---------- Pipeline (cached per input) ----------

if mode == MODE_WIZARD:
    key = wizard_prefilled_key  # type: ignore[assignment]
elif mode == MODE_PDF:
    key = f"pdf::{uploaded.name}::{uploaded.size}"
else:
    key = f"text::{hash(description)}"
results = st.session_state.setdefault("results", {})

if mode != MODE_WIZARD and key not in results:
    with st.status("Verarbeite Komponente …", expanded=True) as status:
        if mode == MODE_PDF:
            try:
                st.write("📄  PDF-Text extrahieren …")
                text = extract_text_from_uploaded(uploaded)
            except ValueError as e:
                status.update(label="PDF unlesbar", state="error")
                st.error(str(e))
                st.stop()

        try:
            if mode == MODE_PDF:
                st.write("🤖  Gemini analysiert die Komponente …")
                komponente, warnings = normalize(text, api_key=api_key, model=model)
            else:
                st.write("🤖  Gemini generiert die Komponente …")
                komponente, warnings = normalize_from_description(
                    description, api_key=api_key, model=model
                )
        except NormalizerError as e:
            status.update(label="Leitplanken-Verstoß", state="error")
            st.error(str(e))
            st.stop()
        except Exception as e:
            msg = str(e)
            is_rate_limit = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
            if is_rate_limit:
                status.update(label="KI-Tageslimit erreicht", state="error")
                st.error(
                    "**Das KI-Tageslimit ist erreicht.**\n\n"
                    f"Das aktuelle Modell `{model}` hat im Free-Tier ein Tageslimit, "
                    "das gerade aufgebraucht ist. Versuche es morgen früh wieder, "
                    "oder bitte Julian, das Billing in der Google-AI-Console zu aktivieren "
                    "(dann sind die Limits ~10× höher und Kosten liegen im Cent-Bereich pro PDF)."
                )
            else:
                status.update(label="Fehler beim KI-Aufruf", state="error")
                st.error(
                    "**Unerwarteter Fehler beim KI-Aufruf.**\n\n"
                    "Bitte erneut probieren. Bleibt der Fehler, Julian Bescheid geben.\n\n"
                    f"_Technisch: {type(e).__name__}: {msg[:200]}_"
                )
            st.stop()

        data = komponente.model_dump(exclude_none=True)

        try:
            st.write("🎨  Rendere DE-PDF …")
            pdf_de = render_pdf_bytes(data, lang="de")
            st.write("🎨  Rendere EN-PDF …")
            pdf_en = render_pdf_bytes(data, lang="en")
        except Exception as e:
            status.update(label="PDF-Render-Fehler", state="error")
            st.error(f"{type(e).__name__}: {e}")
            st.stop()

        results[key] = {
            "data": data,
            "warnings": warnings,
            "pdf_de": pdf_de,
            "pdf_en": pdf_en,
        }
        status.update(label="Fertig — alles unten zum Prüfen + Herunterladen", state="complete")


result = results[key]
data = result["data"]
warnings = result["warnings"]
pdf_de = result["pdf_de"]
pdf_en = result["pdf_en"]

for w in warnings:
    st.warning(w)


# ---------- Tabs ----------

tab_overview, tab_de, tab_en, tab_edit, tab_json = st.tabs([
    "📊  Übersicht", "📄  PDF Deutsch", "📄  PDF English", "✏️  Bearbeiten", "🧾  JSON",
])


with tab_overview:
    pills = [
        f'<span class="wts-pill accent">{data["kategorie"]}</span>',
        f'<span class="wts-pill">{len(data["specs"])} Specs</span>',
        f'<span class="wts-pill">{data["verfuegbarkeit"]}</span>',
    ]
    if data.get("hersteller"):
        sicht = "sichtbar" if data.get("herstellerSichtbar") else "intern"
        pills.append(f'<span class="wts-pill">{data["hersteller"]} · {sicht}</span>')

    st.markdown(
        f"""
        <div class="wts-card">
          <div class="wts-eyebrow">Erkannte Komponente</div>
          <h3 style="margin: 4px 0 8px 0;">{data["titel"]}</h3>
          <div style="color: {TEXT_SECONDARY}; margin-bottom: 12px;">{data["kurzbeschreibung"]}</div>
          <div>{"".join(pills)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(f'<div class="wts-eyebrow">Beschreibung</div>', unsafe_allow_html=True)
    st.write(data["beschreibung"])

    st.markdown(f'<div class="wts-eyebrow" style="margin-top: 1.5rem;">Technische Daten</div>', unsafe_allow_html=True)
    grouped: dict[str, list] = {}
    for s in data["specs"]:
        grouped.setdefault(s["group"], []).append(s)
    group_order = ["elektrisch", "thermisch", "abmessungen", "konstruktion", "umgebung",
                   "funktion", "bedienung", "konfiguration", "prozess", "kommunikation",
                   "qualitaet", "geografie", "kommerziell"]
    for g in group_order:
        if g in grouped:
            st.markdown(f'<div class="wts-spec-group">{g}</div>', unsafe_allow_html=True)
            rows = [{"Bezeichnung": s["label"], "Wert": s["value"]} for s in grouped[g]]
            st.dataframe(rows, hide_index=True, use_container_width=True)

    col_a, col_v = st.columns(2)
    with col_a:
        st.markdown(f'<div class="wts-eyebrow">Anwendungen</div>', unsafe_allow_html=True)
        st.markdown("\n".join(f"- {a}" for a in data["anwendungen"]))
    with col_v:
        st.markdown(f'<div class="wts-eyebrow">Verfügbarkeit</div>', unsafe_allow_html=True)
        meta = []
        meta.append(("Status", data["verfuegbarkeit"]))
        meta.append(("Lieferzeit", data["lieferzeit"]))
        if data.get("branchen"):
            meta.append(("Branchen", ", ".join(data["branchen"])))
        if data.get("temperaturbereich"):
            meta.append(("Temperatur", data["temperaturbereich"]))
        for label, val in meta:
            st.markdown(
                f'<div style="margin-bottom: 8px;">'
                f'<div class="wts-eyebrow" style="font-size: 0.62rem;">{label}</div>'
                f'<div>{val}</div></div>',
                unsafe_allow_html=True,
            )


def _embed_pdf(pdf_bytes: bytes) -> str:
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return f'<iframe class="wts-pdf-preview" src="data:application/pdf;base64,{b64}#toolbar=1"></iframe>'


with tab_de:
    st.markdown(_embed_pdf(pdf_de), unsafe_allow_html=True)

with tab_en:
    st.markdown(_embed_pdf(pdf_en), unsafe_allow_html=True)


with tab_edit:
    st.markdown(
        f'<div class="wts-eyebrow">Korrekturen vor dem Download</div>',
        unsafe_allow_html=True,
    )
    st.caption("Hier kannst du die wichtigsten Felder anpassen, falls die KI etwas falsch erkannt hat. Die PDFs werden danach neu gerendert.")
    with st.form("edit_form"):
        new_titel = st.text_input("Titel (Deutsch)", value=data["titel"])
        new_titel_en = st.text_input("Title (English)", value=data["titel_en"])
        new_kurz = st.text_area("Kurzbeschreibung (DE)", value=data["kurzbeschreibung"], height=80)
        new_kurz_en = st.text_area("Short description (EN)", value=data["kurzbeschreibung_en"], height=80)
        new_lang = st.text_area("Lange Beschreibung (DE)", value=data["beschreibung"], height=120)
        new_lang_en = st.text_area("Long description (EN)", value=data["beschreibung_en"], height=120)
        save = st.form_submit_button("Änderungen übernehmen + PDFs neu rendern", type="primary")

    if save:
        data["titel"] = new_titel
        data["titel_en"] = new_titel_en
        data["kurzbeschreibung"] = new_kurz
        data["kurzbeschreibung_en"] = new_kurz_en
        data["beschreibung"] = new_lang
        data["beschreibung_en"] = new_lang_en
        data["updatedAt"] = date.today().isoformat()
        with st.spinner("Rendere PDFs neu …"):
            results[key]["data"] = data
            results[key]["pdf_de"] = render_pdf_bytes(data, lang="de")
            results[key]["pdf_en"] = render_pdf_bytes(data, lang="en")
        st.success("Übernommen — PDF-Tabs sind aktualisiert.")
        st.rerun()

    st.divider()
    st.markdown(
        f'<div class="wts-eyebrow">Technische Daten bearbeiten</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Werte überschreiben, Zeilen löschen oder unten neue Specs hinzufügen. "
        "Mindestens 3 Specs müssen erhalten bleiben. Pflichtfelder: Bezeichnung (DE/EN), "
        "Wert (DE/EN), Gruppe."
    )

    SPEC_GROUPS = [
        "elektrisch", "thermisch", "abmessungen", "konstruktion", "umgebung",
        "funktion", "bedienung", "konfiguration", "prozess", "kommunikation",
        "qualitaet", "geografie", "kommerziell",
    ]
    edited_specs = st.data_editor(
        data["specs"],
        num_rows="dynamic",
        use_container_width=True,
        key=f"specs_editor_{key}",
        column_config={
            "label": st.column_config.TextColumn("Bezeichnung (DE)", required=True),
            "value": st.column_config.TextColumn("Wert (DE)", required=True),
            "label_en": st.column_config.TextColumn("Label (EN)", required=True),
            "value_en": st.column_config.TextColumn("Value (EN)", required=True),
            "group": st.column_config.SelectboxColumn(
                "Gruppe", options=SPEC_GROUPS, required=True,
            ),
        },
        column_order=["label", "value", "label_en", "value_en", "group"],
    )
    if st.button("Specs übernehmen + PDFs neu rendern", type="primary"):
        cleaned = []
        for row in edited_specs:
            if not row:
                continue
            label = (row.get("label") or "").strip()
            value = (row.get("value") or "").strip()
            label_en = (row.get("label_en") or "").strip()
            value_en = (row.get("value_en") or "").strip()
            group = (row.get("group") or "").strip()
            if not (label and value and label_en and value_en and group):
                continue
            cleaned.append({
                "label": label, "value": value,
                "label_en": label_en, "value_en": value_en,
                "group": group,
            })

        if len(cleaned) < 3:
            st.error(f"Mindestens 3 vollständig ausgefüllte Specs nötig — aktuell: {len(cleaned)}.")
        else:
            data["specs"] = cleaned
            data["updatedAt"] = date.today().isoformat()
            with st.spinner("Rendere PDFs neu …"):
                results[key]["data"] = data
                results[key]["pdf_de"] = render_pdf_bytes(data, lang="de")
                results[key]["pdf_en"] = render_pdf_bytes(data, lang="en")
            st.success(f"Übernommen ({len(cleaned)} Specs) — PDF-Tabs sind aktualisiert.")
            st.rerun()


with tab_json:
    st.json(data, expanded=False)
    st.caption("Diese Datei landet im Astro-Content der WTS-Site (`src/content/komponenten/<slug>.json`).")


# ---------- Downloads ----------

st.divider()
slug = _slugify(data["titel"])
json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

zip_buf = io.BytesIO()
with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr(f"{slug}.json", json_bytes)
    zf.writestr(f"{slug}.de.pdf", pdf_de)
    zf.writestr(f"{slug}.en.pdf", pdf_en)
zip_bytes = zip_buf.getvalue()

st.markdown(f'<div class="wts-eyebrow">Downloads</div>', unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
with c1:
    st.download_button(
        "📦 Alles als ZIP",
        data=zip_bytes,
        file_name=f"{slug}.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )
with c2:
    st.download_button(
        "JSON",
        data=json_bytes,
        file_name=f"{slug}.json",
        mime="application/json",
        use_container_width=True,
    )
with c3:
    st.download_button(
        "PDF DE",
        data=pdf_de,
        file_name=f"{slug}.de.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
with c4:
    st.download_button(
        "PDF EN",
        data=pdf_en,
        file_name=f"{slug}.en.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

st.caption("**Workflow:** ZIP herunterladen → JSON in das WTS-Site-Repo (`src/content/komponenten/`) committen → PDFs an Kunden mailen.")


# ---------- Reset + Footer ----------

st.divider()
left, right = st.columns([3, 1])
with right:
    if st.button("🗑️ Neue Komponente", use_container_width=True):
        results.pop(key, None)
        if mode == MODE_WIZARD:
            st.session_state["wizard"] = {
                "query": "", "lib_done": False, "lib_match": None,
                "web_done": False, "web_results": [], "selected_key": None,
            }
        st.rerun()

st.markdown(
    f"""
    <div class="wts-footer">
      <div><strong style="color: {PRIMARY};">Weber Trading & Service</strong> · Kaiserstraße 35</div>
      <div>WTS-internes Tool · {date.today().year}</div>
    </div>
    """,
    unsafe_allow_html=True,
)
