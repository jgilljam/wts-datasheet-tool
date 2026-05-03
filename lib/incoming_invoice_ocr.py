"""OCR für Lieferanten-Rechnungs-PDFs via Gemini Structured Output.

Schickt das PDF direkt als Datei an Gemini — kein Vorab-Text-Extract nötig,
Gemini parst Multi-Modal selbst (Text + Layout-Hinweise).
"""

from __future__ import annotations

from google import genai
from google.genai import types

from .incoming_invoice_schema import IncomingInvoiceParsed


SYSTEM_PROMPT = """\
Du bist ein präziser Rechnungs-Parser für deutsche und europäische Lieferanten-Rechnungen.

Deine Aufgabe: Extrahiere ALLE relevanten Felder aus dem PDF und liefere sie als
strukturiertes JSON nach dem vorgegebenen Schema.

WICHTIGE REGELN:
- Datums-Felder IMMER im Format YYYY-MM-DD (also 2026-04-15, nicht 15.04.2026)
- Beträge IMMER als Float ohne Tausendertrenner (z.B. 1234.56, nicht "1.234,56 €")
- Wenn ein Feld nicht im PDF steht, lass es leer ("") oder 0.0 — NIEMALS halluzinieren
- Bei mehreren USt-Sätzen: pro Position den jeweiligen Satz angeben (0, 7, 19)
- Reverse-Charge / Steuerfreie EU-Lieferungen → tax_rate_pct = 0
- Wenn die Lieferanten-USt-IdNr im PDF steht, IMMER mit aufnehmen
- Position-Nummern zählen ab 1 — wenn das PDF keine Nummern hat, eigene vergeben
- Beim Gesamtbetrag muss gross_total = total_net + tax_total (auf 1 Cent genau)
- customer_reference: SUCHE explizit nach "Ihre Bestellnummer", "Your PO", "Order No.",
  "BE-NNNN-NNNN", "Bestell-Nr", "P.O. Number" — das ist UNSERE WTS-Bestellnummer beim Lieferanten

KONFIDENZ-EINSCHÄTZUNG:
- "high": alle Pflichtfelder klar erkennbar, Items vollständig extrahiert, Beträge stimmen
- "medium": kleine Unsicherheiten (z.B. ein Datum unklar, eine Position unscharf)
- "low": deutliche Lücken — User muss manuell nachprüfen
"""


class OcrError(RuntimeError):
    pass


def parse_invoice_pdf(
    pdf_bytes: bytes,
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
) -> IncomingInvoiceParsed:
    """Schickt PDF an Gemini, erhält strukturiertes IncomingInvoiceParsed."""
    client = genai.Client(api_key=api_key)

    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

    response = client.models.generate_content(
        model=model,
        contents=[pdf_part, "Bitte parse diese Lieferanten-Rechnung."],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=IncomingInvoiceParsed,
            temperature=0.1,
        ),
    )

    parsed: IncomingInvoiceParsed | None = response.parsed
    if parsed is None:
        raise OcrError(
            f"Gemini hat kein gültiges JSON nach Schema geliefert.\n"
            f"Raw: {(response.text or '')[:500]}"
        )
    return parsed
