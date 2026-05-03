"""Pydantic-Schema für OCR-Output von Lieferanten-Rechnungs-PDFs.

Wird als response_schema an Gemini übergeben — Gemini liefert direkt
strukturiertes JSON, das wir validieren und in die DB übernehmen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IncomingInvoiceItemParsed(BaseModel):
    pos_nr: int = Field(description="Position in der Rechnung (1, 2, 3, ...)")
    sku: str = Field(default="", description="Artikelnummer/SKU des Lieferanten")
    description: str = Field(description="Bezeichnung der Position")
    qty: float = Field(description="Menge")
    unit: str = Field(default="Stk", description="Einheit (Stk, kg, m, ...)")
    unit_price_eur: float = Field(description="Einzelpreis in EUR (z.B. 12.50)")
    discount_pct: float = Field(default=0.0, description="Rabatt in Prozent")
    line_total_eur: float = Field(description="Zeilensumme NETTO in EUR")
    tax_rate_pct: float = Field(default=19.0, description="USt-Satz in Prozent (0/7/19)")


class IncomingInvoiceParsed(BaseModel):
    """Komplette OCR-Extraktion einer Lieferanten-Rechnung."""

    # Lieferant
    supplier_name: str = Field(description="Firmenname des Lieferanten/Rechnungsstellers")
    supplier_vat_id: str = Field(default="", description="USt-IdNr des Lieferanten (DE..., AT..., etc.)")
    supplier_address: str = Field(default="", description="Adresse des Lieferanten als Freitext")
    supplier_email: str = Field(default="", description="Email-Adresse des Lieferanten falls erkennbar")

    # Rechnungs-Header
    invoice_number: str = Field(description="Rechnungsnummer wie vom Lieferanten vergeben")
    invoice_date: str = Field(description="Rechnungsdatum im Format YYYY-MM-DD")
    due_date: str = Field(default="", description="Fälligkeitsdatum im Format YYYY-MM-DD, leer wenn nicht angegeben")
    service_date: str = Field(default="", description="Leistungsdatum im Format YYYY-MM-DD, leer wenn nicht angegeben")
    currency: str = Field(default="EUR", description="Währung (EUR, USD, ...)")

    # Beträge
    total_net_eur: float = Field(description="Nettobetrag in EUR")
    tax_total_eur: float = Field(description="USt-Gesamtbetrag in EUR")
    gross_total_eur: float = Field(description="Bruttobetrag (Gesamt) in EUR")

    # Referenzen
    customer_reference: str = Field(default="", description="Unsere Bestellnummer beim Lieferanten (z.B. BE-2026-...) wenn auf der Rechnung")
    supplier_reference: str = Field(default="", description="Auftragsbestätigungs-Nr des Lieferanten falls angegeben")

    # Items
    items: list[IncomingInvoiceItemParsed] = Field(description="Alle Rechnungspositionen")

    # OCR-Konfidenz (Selbsteinschätzung von Gemini)
    confidence: str = Field(
        default="medium",
        description="Selbsteinschätzung der Extraktionsqualität: 'high' wenn alle Felder klar erkennbar, 'medium' bei kleineren Unsicherheiten, 'low' bei deutlichen Lücken oder nur Teil-OCR",
    )
