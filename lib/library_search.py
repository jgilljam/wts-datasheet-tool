"""Sucht in den lokalen library/-JSONs nach der besten Komponente für eine Anfrage.

Strategie: Mini-Gemini-Call mit komprimiertem Index (Titel + Tags + Kurzbeschreibung
aller Komponenten) → Modell entscheidet, ob ein Match passt (Score 0-100 + Slug).
Token-Verbrauch klein (~5k pro Call).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


LIBRARY_DIR = Path(__file__).resolve().parent.parent / "library"


@dataclass
class LibraryEntry:
    slug: str  # Dateiname ohne .json
    data: dict


class LibraryMatch(BaseModel):
    slug: str = Field(description="Slug der besten passenden Komponente, oder Leerstring wenn keine passt.")
    score: int = Field(description="Match-Qualität 0-100. >=80 = sehr gut, >=60 = brauchbar, <60 = kein Match.", ge=0, le=100)
    begruendung: str = Field(description="Ein Satz: warum passt (oder warum nicht).")


def load_library() -> list[LibraryEntry]:
    if not LIBRARY_DIR.exists():
        return []
    entries: list[LibraryEntry] = []
    for p in sorted(LIBRARY_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries.append(LibraryEntry(slug=p.stem, data=data))
    return entries


def _build_index(entries: list[LibraryEntry]) -> str:
    lines = []
    for e in entries:
        d = e.data
        tags = ", ".join(d.get("tags", []))
        line = (
            f"[{e.slug}] {d.get('titel', '')} — "
            f"{d.get('kategorie', '')} — "
            f"{d.get('kurzbeschreibung', '')[:140]} "
            f"(tags: {tags})"
        )
        lines.append(line)
    return "\n".join(lines)


def find_best_match(
    query: str,
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
) -> tuple[Optional[LibraryEntry], LibraryMatch]:
    """Liefert (entry-or-None, LibraryMatch). entry ist None wenn score < 60."""
    entries = load_library()
    if not entries:
        return None, LibraryMatch(slug="", score=0, begruendung="Bibliothek ist leer.")

    index = _build_index(entries)
    system = (
        "Du prüfst, ob in einer WTS-Komponenten-Bibliothek eine zur Kundenanfrage passende "
        "Komponente vorhanden ist. Antworte STRENG als JSON nach Schema. "
        "Score-Skala: 100=identisch, 80=sehr gute Entsprechung, 60=technisch ähnlich aber "
        "abweichende Specs, <60=kein passender Treffer (slug=\"\")."
    )
    prompt = f"BIBLIOTHEK:\n{index}\n\nANFRAGE:\n{query}"

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=LibraryMatch,
            temperature=0.1,
        ),
    )

    match: LibraryMatch | None = response.parsed
    if match is None:
        return None, LibraryMatch(slug="", score=0, begruendung="Modell-Antwort nicht parsbar.")

    if match.score < 60 or not match.slug:
        return None, match

    by_slug = {e.slug: e for e in entries}
    entry = by_slug.get(match.slug)
    if entry is None:
        # Modell hat einen unbekannten Slug halluziniert → behandeln als kein Match
        return None, LibraryMatch(
            slug="", score=match.score,
            begruendung=f"Modell nannte unbekannten Slug \"{match.slug}\" — verworfen.",
        )
    return entry, match
