"""Gemini-basierte Klassifikation + Extraktion für eingehende Mails.

Pipeline:
    classify_mail(mail)  → 'sales_order' / 'po_acknowledgment' / 'incoming_invoice' / 'reply' / 'other'
    extract_sales_order(mail) → SalesOrderParsed
    extract_incoming_invoice(mail, pdf_bytes) → IncomingInvoiceParsed (delegiert)

Routing-Hint: to_email (sales@ → sales_order, invoice@ → incoming_invoice) ist
ein starker Vor-Klassifikator; Gemini bestätigt oder widerspricht nur.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


# ============================================================
# Klassifikations-Schema
# ============================================================

class MailClassification(BaseModel):
    category: str = Field(
        description=(
            "Eine von: 'sales_order' (Kunde will bei uns bestellen / Anfrage), "
            "'po_acknowledgment' (Lieferant bestätigt unsere Bestellung), "
            "'incoming_invoice' (Lieferanten-Rechnung), "
            "'reply' (Antwort auf eine unserer Mails — Rückfrage, Bestätigung, etc.), "
            "'other' (Newsletter, Spam, internes, Unklassifizierbar)"
        )
    )
    confidence: str = Field(
        default="medium",
        description="'high' / 'medium' / 'low' — wie sicher ist die Einordnung",
    )
    reason: str = Field(
        default="",
        description="Kurze Begründung (1-2 Sätze) was auf die Kategorie hinweist",
    )
    primary_attachment_index: int = Field(
        default=-1,
        description=(
            "Index (0-basiert) des wichtigsten Anhangs, der die eigentliche Bestellung/"
            "Rechnung/AB enthält. -1 wenn unklar oder nicht in Anhängen. "
            "Beispiel: bei 3 PDFs (Bestellung, AGB, technische Spec) → Index 0 für die Bestellung."
        ),
    )


# ============================================================
# Sales-Order-Schema (Kunde bestellt bei uns)
# ============================================================

class SalesOrderItemParsed(BaseModel):
    pos_nr: int = Field(description="Position in der Bestellung (1, 2, 3, ...)")
    sku: str = Field(default="", description="Artikelnummer falls vom Kunden angegeben")
    description: str = Field(description="Bezeichnung des gewünschten Artikels (möglichst wörtlich)")
    qty: float = Field(description="Gewünschte Menge")
    unit: str = Field(default="Stk", description="Einheit")
    target_price_eur: float = Field(default=0.0, description="Ziel-/Wunschpreis falls vom Kunden genannt, sonst 0")


class DeliveryAddressParsed(BaseModel):
    """Lieferanschrift wenn explizit im Ship-To-Block des PDFs/Bodys angegeben."""
    company: str = Field(default="", description="Empfänger-Firma (z.B. abweichende Tochterfirma oder Lager)")
    contact_name: str = Field(default="", description="Ansprechpartner an der Lieferadresse")
    street: str = Field(default="", description="Straße + Hausnummer (z.B. 'Musterweg 5')")
    street_2: str = Field(default="", description="Zusatz: Halle, Stockwerk, c/o, etc.")
    zip: str = Field(default="", description="Postleitzahl")
    city: str = Field(default="", description="Stadt")
    country_code: str = Field(default="DE", description="ISO-2-Country-Code (DE, AT, CH, FR, ...)")


class SalesOrderParsed(BaseModel):
    """Vom Kunden gewünschte Bestellung — extrahiert aus Mail-Body + PDF-Anhang."""
    customer_name: str = Field(description="Firmenname des Kunden")
    customer_email: str = Field(default="", description="Allgemeine Kontakt-Email des Kunden (z.B. der Absender)")
    customer_vat_id: str = Field(default="", description="USt-IdNr falls erwähnt")
    customer_reference: str = Field(default="", description="Bestell-Nr / Anfrage-Nr des Kunden, falls genannt")

    confirmation_email: str = Field(
        default="",
        description=(
            "Email-Adresse, an die der Kunde explizit die Auftragsbestätigung wünscht "
            "(z.B. 'Please send confirmation to ...'). Leer wenn nicht genannt."
        ),
    )
    invoice_email: str = Field(
        default="",
        description=(
            "Email-Adresse, an die der Kunde explizit die Rechnung wünscht "
            "(z.B. 'Please send invoice to ...'). Leer wenn nicht genannt."
        ),
    )

    delivery_address: DeliveryAddressParsed | None = Field(
        default=None,
        description=(
            "Strukturierte Lieferanschrift aus dem Ship-To-Block des PDFs. "
            "Nur wenn explizit angegeben — sonst null/leer."
        ),
    )
    requested_delivery_date: str = Field(default="", description="Wunsch-Liefertermin im Format YYYY-MM-DD oder leer")

    items: list[SalesOrderItemParsed] = Field(description="Alle gewünschten Positionen")

    notes: str = Field(default="", description="Sonstige relevante Hinweise des Kunden")
    confidence: str = Field(
        default="medium",
        description="'high' / 'medium' / 'low' — Selbsteinschätzung",
    )


# ============================================================
# Klassifikator
# ============================================================

CLASSIFY_SYSTEM = """\
Du klassifizierst eingehende Geschäfts-Emails für ein deutsches Handelsunternehmen
(WTS Trading & Service, Kältetechnik-Komponenten).

Mögliche Kategorien:
- sales_order: Kunde will etwas bei UNS bestellen (Anfrage, Bestellung, RFQ, Angebotsanfrage)
- po_acknowledgment: Lieferant bestätigt eine BESTELLUNG die wir bei ihm aufgegeben haben
- incoming_invoice: Lieferanten-RECHNUNG für Ware/Leistung an uns
- reply: Antwort/Rückfrage zu einer unserer ausgehenden Mails (Mahnung-Antwort, Klärung, Statusfrage)
- other: Newsletter, Werbung, Spam, intern, sonstiges

Routing-Hinweis: An sales@wts-trading.de gesendete Mails sind meistens sales_order.
An invoice@wts-trading.de sind meistens incoming_invoice. Verifiziere aber anhand
des Inhalts — manchmal landen Mails in der falschen Inbox.

WENN MEHRERE PDF-ANHÄNGE: bestimme welcher der „primäre" Anhang ist —
also der mit der echten Bestellung/Rechnung/AB. Andere PDFs sind oft
AGB, technische Spezifikationen, Datenblätter, Anhänge. Setze
primary_attachment_index entsprechend (0-basiert, -1 wenn unklar).

Beispiel: 3 Anhänge in Reihenfolge ['AGB.pdf', 'PO_4500.pdf', 'Spec_WS200.pdf']
→ primary_attachment_index = 1.

Liefere strukturiertes JSON mit category, confidence, reason, primary_attachment_index.
"""


def classify_mail(
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    to_email: str,
    from_email: str,
    subject: str,
    body_text: str,
    attachment_filenames: list[str] | None = None,
    pdf_bytes_list: list[bytes] | None = None,
) -> MailClassification:
    """Klassifiziert eine Mail in eine der 5 Kategorien.

    PDFs werden an Gemini mit-übergeben — bei Bestellungen ist oft der PDF-Anhang
    die eigentliche Bestellung, der Body nur „anbei…".
    """
    client = genai.Client(api_key=api_key)
    parts: list[Any] = []
    for pdf in (pdf_bytes_list or [])[:3]:  # max 3 PDFs für Klassifikation
        parts.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
    parts.append(
        f"An: {to_email}\n"
        f"Von: {from_email}\n"
        f"Betreff: {subject or '(kein Betreff)'}\n"
        f"Anhänge: {', '.join(attachment_filenames or []) or '(keine)'}\n"
        f"---\n"
        f"{(body_text or '')[:6000]}"
    )
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=CLASSIFY_SYSTEM,
            response_mime_type="application/json",
            response_schema=MailClassification,
            temperature=0.0,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        return MailClassification(category="other", confidence="low", reason="Kein gültiges JSON.")
    return parsed


# ============================================================
# Sales-Order-Extraktion (aus Mail + Anhängen)
# ============================================================

EXTRACT_SO_SYSTEM = """\
Du extrahierst Bestell-Informationen aus einer Kunden-Anfrage / Bestell-Mail an WTS.

KONTEXT — sehr wichtig für customer_name:
- WIR sind WTS Trading & Service (früher auch „Weber Trading", „Weber Trading & Service"
  oder einfach „WEBER" als interner Lieferanten-Code beim Kunden). Wir sind NIEMALS der
  Kunde der bestellt.
- Der Kunde ist die Firma die UNS schreibt — typischerweise erkennbar an:
  1. Absender-Email-Domain (von: aminata.diao@EBERSPAECHER.com → Kunde = Eberspächer)
  2. Briefkopf/Logo im PDF (Header oben, oft mit Firmen-Logo)
  3. „Bill-To"-Block oder „Ship-From" im PDF
- WENN im PDF/Subject steht „PO ... WEBER" oder „... Weber Trading", dann ist WEBER
  der Lieferanten-Code beim Kunden — also UNSER Code, NICHT der Kunden-Name.
- customer_name muss IMMER der Käufer sein, der UNS bestellt. Im Zweifel: Domain
  aus from_email beachten und ein passendes Firmenlabel daraus ableiten.

WICHTIGE REGELN:
- Datums-Felder IMMER im Format YYYY-MM-DD
- Beträge IMMER als Float ohne Tausendertrenner (z.B. 1234.56)
- Wenn ein Feld nicht im Text steht, lass es leer ("") oder 0 — NIEMALS halluzinieren
- customer_reference: explizit nach "Unsere Bestellnummer", "Bestell-Nr", "PO Number",
  "PO No.", "Ref", "P.O. ###" suchen — das ist die Bestell-Nr DES KUNDEN
- Mengen-Erkennung: "5 Stück Wilspec WS-200" → qty=5, sku="WS-200", description="Wilspec WS-200"
- Wenn nur Anfrage ohne konkrete Mengen: items-Liste ggf. leer mit description="ANFRAGE: ..." in notes
- Wenn PDF im Anhang ist (z.B. PO als PDF): primär aus PDF extrahieren, Body-Text ergänzend
- delivery_address: nur wenn explizit im PDF/Body genannt (Ship-To-Block)
- confirmation_email: Suche explizit nach Phrasen wie „send confirmation to…",
  „Auftragsbestätigung an…", „order confirmation…" — die Email die in unmittelbarer
  Nähe steht ist die Confirmation-Email. Leer wenn nicht eindeutig.
- invoice_email: Suche explizit nach Phrasen wie „send invoice to…",
  „Rechnung an…", „accounting…", „invoicing…", „accounts payable" — die Email die
  in unmittelbarer Nähe steht ist die Invoice-Email. Leer wenn nicht eindeutig.
- WICHTIG: confirmation_email + invoice_email sind oft Funktions-Postfächer
  (orderconfirmation@, accounting@, ap@) und unterscheiden sich vom normalen
  Absender (z.B. einkauf@). Trenne sauber.

KONFIDENZ:
- "high": klare Bestellung mit eindeutigen Items + Mengen + korrekt erkanntem Kunden
- "medium": einige Unklarheiten in Mengen oder Artikelbezeichnungen
- "low": vage Anfrage, Items unsicher oder Kunde mehrdeutig
"""


def extract_sales_order(
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    from_email: str,
    subject: str,
    body_text: str,
    pdf_bytes_list: list[bytes] | None = None,
) -> SalesOrderParsed:
    """Extrahiert eine Sales-Order aus Mail + ggf. PDF-Anhang."""
    client = genai.Client(api_key=api_key)
    parts: list[Any] = []
    for pdf in pdf_bytes_list or []:
        parts.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
    parts.append(
        f"Von: {from_email}\n"
        f"Betreff: {subject or ''}\n"
        f"---\n"
        f"{(body_text or '')[:10000]}"
    )
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACT_SO_SYSTEM,
            response_mime_type="application/json",
            response_schema=SalesOrderParsed,
            temperature=0.1,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        raise RuntimeError(
            f"Gemini hat kein gültiges Sales-Order-JSON geliefert. Raw: {(response.text or '')[:500]}"
        )
    return parsed
