"""Gemini Web-Suche → Hersteller-Vorschläge mit Datenblatt-URLs.

Zwei-Stufen-Call, weil google_search-Tool und response_schema sich gegenseitig
ausschließen:
  Stufe 1: google_search-Call (text out) — Modell sucht und beschreibt Treffer
  Stufe 2: kleiner JSON-Cleanup-Call (response_schema) — formt Treffer in
           strukturierte Liste mit URLs

Pro Anfrage Tokenverbrauch ca. 10–25k.
"""

from __future__ import annotations

from typing import List

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class WebSuggestion(BaseModel):
    hersteller: str
    modell: str
    kurzbeschreibung: str = Field(description="1 Satz: was ist das, welche Hauptspecs.")
    datenblatt_url: str = Field(description="Direkte URL zum PDF-Datenblatt. Leerstring wenn kein PDF gefunden.")
    quelle_url: str = Field(description="Produktseite des Herstellers (Fallback wenn kein PDF).")


class WebSuggestions(BaseModel):
    treffer: List[WebSuggestion]


SEARCH_SYSTEM = (
    "Du recherchierst für WTS Trading & Service (B2B-Komponentenhandel) im Web nach "
    "passenden Industriekomponenten zu einer Anfrage. Nutze Web-Suche, finde 3–5 "
    "konkrete Produkte verschiedener Hersteller. Bevorzuge etablierte EU-Hersteller. "
    "Liefere für jeden Treffer: Hersteller, Modellbezeichnung, kurze Charakterisierung, "
    "und WENN möglich die direkte URL zum PDF-Datenblatt (sonst Produktseiten-URL). "
    "Vermeide chinesische Marktplätze (Alibaba, Aliexpress) und reine Händler ohne "
    "eigene Produkte."
)

EXTRACT_SYSTEM = (
    "Extrahiere aus dem nachfolgenden Recherche-Text eine strukturierte Liste der "
    "Produkt-Treffer. Antwort STRENG als JSON nach Schema. Wenn keine PDF-URL "
    "genannt ist, datenblatt_url=\"\" setzen."
)


class WebSearchError(RuntimeError):
    pass


def search_components(
    query: str,
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
) -> List[WebSuggestion]:
    """Web-Suche nach passenden Komponenten. Liefert Liste mit 0–5 Vorschlägen."""
    client = genai.Client(api_key=api_key)

    # Stufe 1: Web-Suche mit google_search Tool
    search_response = client.models.generate_content(
        model=model,
        contents=f"Anfrage: {query}",
        config=types.GenerateContentConfig(
            system_instruction=SEARCH_SYSTEM,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3,
        ),
    )
    raw = (search_response.text or "").strip()
    if not raw:
        raise WebSearchError("Web-Suche lieferte keine Treffer-Beschreibung.")

    # Stufe 2: Recherche-Text → strukturiertes JSON
    extract_response = client.models.generate_content(
        model=model,
        contents=raw,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACT_SYSTEM,
            response_mime_type="application/json",
            response_schema=WebSuggestions,
            temperature=0.1,
        ),
    )
    parsed: WebSuggestions | None = extract_response.parsed
    if parsed is None:
        raise WebSearchError(
            "Konnte Web-Treffer nicht in Liste umwandeln.\n"
            f"Recherche-Text war: {raw[:300]}"
        )
    return parsed.treffer
