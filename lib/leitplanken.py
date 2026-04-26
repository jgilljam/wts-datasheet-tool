"""WTS-Leitplanken: Begriffe, die NICHT auf der Website auftauchen dürfen.

Hintergrund: siehe project_wts_website.md (Mentor German + Positionierung).
"""

import re
from dataclasses import dataclass
from typing import List, Optional


HARD_BLOCK_TERMS = [
    "wilspec",
    "calorflex",
    "druckschalter",
    "pressostat",
    "pressure switch",
    "pressure-switch",
]

HERSTELLER_AUTO_HIDE = [
    "evco",
]


@dataclass
class Violation:
    term: str
    location: str
    snippet: str


def scan_text(text: str, location: str = "input") -> List[Violation]:
    """Sucht im Text nach hart geblockten Begriffen.

    Liefert Liste von Violations zurück; leer = ok.
    """
    found: List[Violation] = []
    lowered = text.lower()
    for term in HARD_BLOCK_TERMS:
        for m in re.finditer(re.escape(term), lowered):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            found.append(
                Violation(
                    term=term,
                    location=location,
                    snippet="…" + text[start:end].strip() + "…",
                )
            )
    return found


def normalize_hersteller_visibility(data: dict) -> dict:
    """Setzt herstellerSichtbar auf False für Hersteller, die nur dezent erscheinen dürfen."""
    h = data.get("hersteller")
    if not h:
        return data
    if h.lower() in HERSTELLER_AUTO_HIDE:
        data["herstellerSichtbar"] = False
    return data


def format_violations(violations: List[Violation]) -> str:
    if not violations:
        return ""
    lines = ["LEITPLANKEN-VERSTOSS — Verarbeitung abgebrochen.\n"]
    for v in violations:
        lines.append(f"  • '{v.term}' in {v.location}")
        lines.append(f"    Kontext: {v.snippet}")
    lines.append(
        "\nDiese Begriffe dürfen nicht auf die Website. Wenn der Inhalt trotzdem"
        "\ngebraucht wird, manuell prüfen und die betroffenen Stellen entfernen,"
        "\nbevor das Datenblatt erneut eingespeist wird."
    )
    return "\n".join(lines)
