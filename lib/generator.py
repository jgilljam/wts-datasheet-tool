"""Komponente-JSON → PDF via WeasyPrint, im WTS-Branding."""

import base64
import os
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Pango/Cairo aus Homebrew finden (lokal Mac); auf Linux/Streamlit-Cloud nicht nötig.
if os.uname().sysname == "Darwin":
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

from weasyprint import HTML, CSS  # noqa: E402

from .i18n import I18N  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

Lang = Literal["de", "en"]

_jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

SPEC_GROUP_ORDER = [
    "elektrisch", "thermisch", "abmessungen", "konstruktion", "umgebung",
    "funktion", "bedienung", "konfiguration", "prozess", "kommunikation",
    "qualitaet", "geografie", "kommerziell",
]


def _logo_uri() -> str:
    logo_path = ASSETS_DIR / "logo.png"
    data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def _group_specs(specs: list[dict], lang: Lang) -> "OrderedDict[str, list]":
    grouped: dict[str, list] = {}
    for s in specs:
        g = s.get("group", "konstruktion")
        if lang == "en":
            entry = {"label": s["label_en"], "value": s["value_en"]}
        else:
            entry = {"label": s["label"], "value": s["value"]}
        grouped.setdefault(g, []).append(entry)

    out: OrderedDict[str, list] = OrderedDict()
    for g in SPEC_GROUP_ORDER:
        if g in grouped:
            out[g] = grouped.pop(g)
    for g, items in grouped.items():
        out[g] = items
    return out


def _build_context(data: dict, lang: Lang) -> dict:
    tr = I18N[lang]
    if lang == "en":
        titel = data["titel_en"]
        kurz = data["kurzbeschreibung_en"]
        beschr = data["beschreibung_en"]
        anwendungen = data["anwendungen_en"]
        lieferzeit = data["lieferzeit_en"]
    else:
        titel = data["titel"]
        kurz = data["kurzbeschreibung"]
        beschr = data["beschreibung"]
        anwendungen = data["anwendungen"]
        lieferzeit = data["lieferzeit"]

    branchen = data.get("branchen", [])
    branchen_display = ", ".join(tr["branche"].get(b, b) for b in branchen)

    verfuegbarkeit_display = tr["verfuegbarkeit_value"].get(
        data["verfuegbarkeit"], data["verfuegbarkeit"]
    )
    kategorie_display = tr["kategorie"].get(data["kategorie"], data["kategorie"])

    return {
        "lang": lang,
        "tr": tr,
        "logo_uri": _logo_uri(),
        "titel": titel,
        "kurzbeschreibung": kurz,
        "beschreibung": beschr,
        "anwendungen": anwendungen,
        "lieferzeit": lieferzeit,
        "kategorie_display": kategorie_display,
        "branchen_display": branchen_display,
        "verfuegbarkeit_display": verfuegbarkeit_display,
        "specs_grouped": _group_specs(data["specs"], lang),
        "artikelnummer": data.get("artikelnummer"),
        "temperaturbereich": data.get("temperaturbereich"),
        "updatedAt": data.get("updatedAt") or data.get("publishedAt") or "",
    }


def render_pdf_bytes(data: dict, lang: Lang = "de") -> bytes:
    """Rendert die Komponente direkt nach BytesIO — fürs Streaming an den Browser."""
    context = _build_context(data, lang)
    template = _jinja.get_template("datenblatt.html")
    html_str = template.render(**context)

    css_path = TEMPLATES_DIR / "datenblatt.css"
    css = CSS(filename=str(css_path))

    buf = BytesIO()
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        target=buf,
        stylesheets=[css],
    )
    return buf.getvalue()
