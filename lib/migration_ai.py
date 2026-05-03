"""Gemini-basierte Bulk-Extraktion offener Aufträge für sevDesk-Migration.

Eingaben:
  - Freier Text (paste): mehrere Aufträge in einem Block
  - PDF(s): alte Auftragsbestätigungen, eine pro Datei
  - CSV-Zeilen: bereits vorstrukturiert, KI füllt nur Lücken / parst Items

Ausgabe: Liste von SalesOrderParsed (gleiches Schema wie Mail-Pipeline).
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from lib.mail_ai import SalesOrderParsed


class BatchSalesOrders(BaseModel):
    """Wrapper für mehrere Aufträge aus einer Quelle (Freitext / Multi-PDF)."""
    orders: list[SalesOrderParsed] = Field(
        default_factory=list,
        description="Alle erkannten Aufträge — leere Liste wenn nichts identifiziert wurde.",
    )
    notes: str = Field(default="", description="Generelle Hinweise zur Extraktion (Mehrdeutigkeiten etc.)")


SYSTEM_BATCH = """\
Du extrahierst OFFENE AUFTRÄGE aus Text/PDFs für die Migration aus einem alten ERP
(sevDesk) in WTS Trading & Service.

WIR sind WTS Trading & Service (früher „Weber Trading"). Der Kunde ist die
Firma, die bei uns bestellt hat — niemals WTS/Weber selbst.

Eine Quelle kann MEHRERE Aufträge enthalten — z.B. eine Liste mit Trennzeilen,
mehrere Auftrags-PDFs, eine sevDesk-Export-Tabelle. Erkenne jede Bestellung
einzeln und liefere sie als separates SalesOrderParsed-Element.

REGELN:
- Datums-Felder IMMER YYYY-MM-DD
- Beträge IMMER Float ohne Tausendertrenner
- Wenn ein Feld nicht im Text steht: leer ("" oder 0). Niemals halluzinieren.
- customer_reference: explizite Bestellnummer DES KUNDEN (PO-Nr, Bestell-Nr)
- pos_nr: bei 1 starten und durchnummerieren wenn nicht angegeben
- Wenn Mengen unklar: qty=0 + notes="ANFRAGE: ..." am Auftrag
- delivery_address: nur wenn explizit Ship-To-Block vorhanden

KONFIDENZ pro Auftrag:
- "high": Kunde + Items + Mengen klar
- "medium": Mengen oder Bezeichnungen unsicher
- "low": Quelle unklar oder unvollständig
"""


def extract_batch_from_text(
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    text: str,
) -> BatchSalesOrders:
    """Extrahiert mehrere Aufträge aus einem Freitext-Block."""
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[text[:50_000]],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_BATCH,
            response_mime_type="application/json",
            response_schema=BatchSalesOrders,
            temperature=0.1,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        return BatchSalesOrders(orders=[], notes="Gemini lieferte kein gültiges JSON.")
    return parsed


def extract_batch_from_pdfs(
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    pdfs: list[tuple[str, bytes]],
) -> BatchSalesOrders:
    """Extrahiert je einen Auftrag pro PDF (Filename als Hinweis im Prompt)."""
    if not pdfs:
        return BatchSalesOrders(orders=[])
    client = genai.Client(api_key=api_key)
    parts: list[Any] = []
    for filename, data in pdfs:
        parts.append(types.Part.from_bytes(data=data, mime_type="application/pdf"))
        parts.append(f"\n--- ENDE Datei: {filename} ---\n")
    parts.append(
        "Extrahiere aus jeder PDF-Datei genau einen Auftrag. "
        "Reihenfolge der Antworten = Reihenfolge der Dateien."
    )
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_BATCH,
            response_mime_type="application/json",
            response_schema=BatchSalesOrders,
            temperature=0.1,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        return BatchSalesOrders(orders=[], notes="Gemini lieferte kein gültiges JSON.")
    return parsed


def extract_batch_from_csv_rows(
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    csv_text: str,
) -> BatchSalesOrders:
    """CSV-Tabelle (sevDesk-Export o.ä.) → Aufträge."""
    text = (
        "Folgende CSV ist ein Export offener Aufträge aus dem alten ERP. "
        "Spalten können beliebig benannt sein — interpretiere die Header. "
        "Jede Zeile (oder Gruppe Zeilen mit gleicher Auftrags-Nr) ist ein Auftrag.\n\n"
        + csv_text[:50_000]
    )
    return extract_batch_from_text(api_key=api_key, model=model, text=text)
