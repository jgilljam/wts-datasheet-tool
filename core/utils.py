"""Allgemein wiederverwendbare Helpers (Datum, Serialisierung, Format)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def parse_date(v: Any) -> date | None:
    """Akzeptiert date / datetime / ISO-String / None und liefert date | None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def format_date(v: Any) -> str:
    d = parse_date(v)
    return d.isoformat() if d else ""


def ser_value(v: Any) -> Any:
    """Serialisiert date/datetime → ISO-String, lässt Rest durch."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def cents_to_eur(cents: int | None) -> str:
    """1234 → '12,34 €' (DE-Format mit Komma)."""
    if cents is None:
        return ""
    return f"{cents / 100:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def eur_to_cents(eur: float | None) -> int | None:
    if eur is None:
        return None
    return int(round(float(eur) * 100))


def sanitize_search(s: str | None) -> str:
    """Macht User-Input für PostgREST-or()/ilike-Queries sicher.

    PostgREST nutzt `,` `(` `)` `:` als Filter-Syntax-Trenner. Ein User-Input
    mit z.B. „CO., LTD." führt sonst zum Parse-Crash. Wir ersetzen die
    Sonderzeichen durch Leerzeichen — fürs ILIKE-Matching ist das ok, weil
    `%` davor und danach jeden String matcht.

    Außerdem entschärfen wir das `%`-Wildcard, damit User keine Wildcard-
    Suche durchführen können (würde die DB überlasten).
    """
    if not s:
        return ""
    out = s
    for ch in ("%", ",", "(", ")", ":", "*"):
        out = out.replace(ch, " ")
    return " ".join(out.split())  # Mehrfach-Leerzeichen zusammenführen
