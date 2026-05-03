"""Convert KI-extrahierte Mails in Beleg-Drafts (orders / incoming_invoices).

Verwendung:
    from lib.mail_to_beleg import convert_mail_to_order, convert_mail_to_incoming_invoice

Wichtig: Erstellt nur Drafts (status='draft' bzw. 'received'), niemals direkt
festgeschrieben. User muss manuell prüfen + locken.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from core.db import supabase
from core.utils import ser_value


# ============================================================
# Helper: Datum, Cents
# ============================================================

def _parse_date_iso(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _eur_to_cents(eur: float | None) -> int | None:
    if eur is None:
        return None
    try:
        return int(round(float(eur) * 100))
    except (TypeError, ValueError):
        return None


# ============================================================
# Party-Matching
# ============================================================

_FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch",
    "web.de", "yahoo.com", "yahoo.de", "hotmail.com", "hotmail.de",
    "outlook.com", "outlook.de", "t-online.de", "icloud.com", "me.com",
    "live.com", "live.de", "aol.com",
}


def _match_party(
    *,
    party_type: str,                  # 'customer' / 'supplier'
    name: str | None,
    email: str | None,
    vat_id: str | None,
) -> tuple[str | None, str]:
    """Versucht eine Partei zu matchen — VAT > Email-Domain > exakter Name.

    Returns (party_id, match_reason). match_reason ∈ {'vat', 'domain', 'name', 'none'}
    """
    sb = supabase()

    # 1. VAT-ID exakt
    if vat_id:
        v = vat_id.strip().replace(" ", "").upper()
        if v:
            res = sb.table("parties").select("id").eq("vat_id", v).eq("type", party_type).limit(1).execute().data
            if res:
                return res[0]["id"], "vat"

    # 2. Email-Domain via contacts
    if email and "@" in email:
        domain = email.rsplit("@", 1)[1].lower().strip()
        if domain and domain not in _FREEMAIL_DOMAINS:
            contacts = (
                sb.table("contacts")
                .select("party_id, email")
                .ilike("email", f"%@{domain}")
                .execute()
                .data
            ) or []
            for c in contacts:
                if not c.get("party_id"):
                    continue
                p = sb.table("parties").select("id, type").eq("id", c["party_id"]).maybe_single().execute().data
                if p and p.get("type") == party_type:
                    return p["id"], "domain"

    # 3. Exakter Name (case-insensitive)
    if name:
        n = name.strip()
        if n:
            res = (
                sb.table("parties")
                .select("id")
                .ilike("legal_name", n)
                .eq("type", party_type)
                .limit(1)
                .execute()
                .data
            )
            if res:
                return res[0]["id"], "name"

    return None, "none"


# WTS-Eigennamen — falls Gemini diese fälschlicherweise als customer_name liefert,
# fallen wir auf Email-Domain als Customer-Hinweis zurück.
_WTS_SELF_NAMES = {
    "wts trading & service",
    "wts trading and service",
    "wts trading",
    "wts",
    "weber trading & service",
    "weber trading and service",
    "weber trading",
    "weber",
}


def _is_wts_selfname(name: str | None) -> bool:
    if not name:
        return False
    return name.strip().lower() in _WTS_SELF_NAMES


def _create_party(
    *,
    party_type: str,
    name: str,
    vat_id: str | None = None,
    notes: str | None = None,
    email: str | None = None,
) -> str:
    """Legt eine neue Partei an. Optional Kontakt mit Email."""
    sb = supabase()
    payload = {
        "legal_name": name or "Unbekannt",
        "type": party_type,
    }
    if vat_id:
        payload["vat_id"] = vat_id.strip().replace(" ", "").upper()
    if notes:
        payload["notes"] = notes

    res = sb.table("parties").insert(payload).execute()
    party_id = res.data[0]["id"]

    if email and "@" in email:
        _upsert_contact(
            party_id=party_id,
            email=email,
            role="Vertrieb" if party_type == "customer" else "Buchhaltung",
            is_primary=True,
        )

    return party_id


def _normalize_addr_part(s: str | None) -> str:
    """Normalisiert Adress-Teile für Duplikatscheck (Schreibweisen-Toleranz)."""
    if not s:
        return ""
    out = s.lower().strip().replace("ß", "ss")
    for ch in (".", ","):
        out = out.replace(ch, "")
    return " ".join(out.split())


def _upsert_address(
    *,
    party_id: str,
    kind: str,
    street: str,
    city: str,
    zip_code: str | None = None,
    street_2: str | None = None,
    country_code: str = "DE",
    contact_name: str | None = None,
    label: str | None = None,
) -> str | None:
    """Legt eine Adresse an, falls Street+City+Zip+Kind noch nicht existiert.

    Match-Logik mit Normalisierung (case-insensitive, ß→ss, Punkte/Whitespace
    ignoriert) — verhindert Duplikate wie „Musterstr. 5" vs „Musterstraße 5".
    """
    if not street or not city:
        return None
    sb = supabase()
    target_street = _normalize_addr_part(street)
    target_city = _normalize_addr_part(city)
    target_zip = _normalize_addr_part(zip_code)

    existing_addrs = (
        sb.table("addresses")
        .select("id, street, city, zip")
        .eq("party_id", party_id)
        .eq("kind", kind)
        .execute().data
    ) or []
    for a in existing_addrs:
        if (
            _normalize_addr_part(a.get("street")) == target_street
            and _normalize_addr_part(a.get("city")) == target_city
            and (not target_zip or _normalize_addr_part(a.get("zip")) == target_zip)
        ):
            return a["id"]
    payload: dict[str, Any] = {
        "party_id": party_id,
        "kind": kind,
        "street": street,
        "city": city,
        "country_code": country_code,
    }
    if zip_code:
        payload["zip"] = zip_code
    if street_2:
        payload["street_2"] = street_2
    if contact_name:
        payload["contact_name"] = contact_name
    if label:
        payload["label"] = label
    try:
        res = sb.table("addresses").insert(payload).execute()
        return res.data[0]["id"]
    except Exception:
        return None


def _upsert_contact(
    *,
    party_id: str,
    email: str,
    role: str,
    is_primary: bool = False,
    name: str | None = None,
) -> None:
    """Legt einen Kontakt an, falls die Email-Adresse für diese Partei noch nicht existiert."""
    if not email or "@" not in email:
        return
    e = email.strip().lower()
    sb = supabase()
    existing = (
        sb.table("contacts")
        .select("id")
        .eq("party_id", party_id)
        .eq("email", e)
        .limit(1)
        .execute()
        .data
    )
    if existing:
        return
    payload: dict[str, Any] = {
        "party_id": party_id,
        "email": e,
        "role": role,
        "is_primary": is_primary,
    }
    if name:
        payload["name"] = name
    try:
        sb.table("contacts").insert(payload).execute()
    except Exception:
        pass


# ============================================================
# Article-Matching
# ============================================================

def _match_article_by_sku(
    sku: str | None,
    *,
    party_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Versucht Artikel zu matchen.

    Reihenfolge:
      1. party_article_skus[party_id, sku]  → 'party_sku'
      2. articles.sku                        → 'exact_sku'

    Returns (article_id, match_confidence).
    """
    if not sku:
        return None, None
    s = sku.strip()
    if not s:
        return None, None
    sb = supabase()

    # 1. Party-Mapping (Kunden-/Lieferanten-eigener Code)
    if party_id:
        mapped = (
            sb.table("party_article_skus")
            .select("article_id")
            .eq("party_id", party_id)
            .ilike("external_sku", s)
            .limit(1).execute().data
        )
        if mapped:
            # last_seen_at aktualisieren
            try:
                sb.table("party_article_skus").update({
                    "last_seen_at": datetime.now(timezone.utc).isoformat()
                }).eq("party_id", party_id).ilike("external_sku", s).execute()
            except Exception:
                pass
            return mapped[0]["article_id"], "party_sku"

    # 2. Eigene SKU
    res = sb.table("articles").select("id").ilike("sku", s).limit(1).execute().data
    if res:
        return res[0]["id"], "exact_sku"
    return None, None


def remember_sku_mapping(
    *,
    party_id: str,
    external_sku: str,
    article_id: str,
    external_description: str | None = None,
) -> None:
    """Persistiert ein neues SKU-Mapping (z.B. nach manuellem Match durch User)."""
    if not (party_id and external_sku and article_id):
        return
    payload: dict[str, Any] = {
        "party_id": party_id,
        "external_sku": external_sku.strip(),
        "article_id": article_id,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if external_description:
        payload["external_description"] = external_description
    try:
        # Upsert
        existing = (
            supabase().table("party_article_skus")
            .select("id")
            .eq("party_id", party_id)
            .ilike("external_sku", external_sku.strip())
            .limit(1).execute().data
        )
        if existing:
            supabase().table("party_article_skus").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            supabase().table("party_article_skus").insert(payload).execute()
    except Exception:
        pass


# ============================================================
# Mail → Order (Sales)
# ============================================================

def convert_mail_to_order(
    *,
    mail_id: str,
    sales_order_payload: dict[str, Any],
    mail_from_email: str,
    actor_email: str | None = None,
) -> str:
    """Erzeugt einen Auftrag-Draft aus extrahierten Sales-Order-Daten.

    Returns: order_id der neu erstellten Order.
    """
    so = sales_order_payload or {}

    # KI-Schutz: wenn Gemini fälschlich WTS als customer_name extrahiert hat,
    # ignorieren wir den Namen und verlassen uns auf die Mail-Domain.
    raw_name = so.get("customer_name")
    if _is_wts_selfname(raw_name):
        raw_name = None

    contact_email = so.get("customer_email") or mail_from_email

    # 1. Customer-Match oder anlegen
    customer_id, match_reason = _match_party(
        party_type="customer",
        name=raw_name,
        email=contact_email,
        vat_id=so.get("customer_vat_id"),
    )
    if not customer_id:
        # Fallback-Name: bei Selbstnamen-Verwechslung Domain als Hinweis
        fallback_name = raw_name
        if not fallback_name and contact_email and "@" in contact_email:
            domain = contact_email.rsplit("@", 1)[1].lower()
            if domain not in _FREEMAIL_DOMAINS:
                fallback_name = domain.split(".")[0].capitalize()
        customer_id = _create_party(
            party_type="customer",
            name=fallback_name or "Unbekannter Kunde",
            vat_id=so.get("customer_vat_id"),
            email=contact_email,
            notes=(
                "🤖 Auto-erstellt aus Posteingang (Sales-Mail). "
                "Bitte legal_name, Adressen und Zahlungskonditionen prüfen."
            ),
        )

    # 1b. Spezial-Kontakte aus der KI-Extraktion ergänzen (auch bei bestehendem Kunden)
    if conf_email := (so.get("confirmation_email") or "").strip():
        _upsert_contact(party_id=customer_id, email=conf_email, role="Auftragsbestätigung")
    if inv_email := (so.get("invoice_email") or "").strip():
        _upsert_contact(party_id=customer_id, email=inv_email, role="Buchhaltung")
    # Absender als Vertriebs-Kontakt (falls neu)
    if mail_from_email and mail_from_email != conf_email and mail_from_email != inv_email:
        _upsert_contact(party_id=customer_id, email=mail_from_email, role="Vertrieb")

    # 1c. Strukturierte Lieferanschrift (falls von KI extrahiert)
    shipping_address_id: str | None = None
    da = so.get("delivery_address") or {}
    if isinstance(da, dict) and (da.get("street") and da.get("city")):
        shipping_address_id = _upsert_address(
            party_id=customer_id,
            kind="shipping",
            street=da.get("street") or "",
            street_2=da.get("street_2") or None,
            zip_code=da.get("zip") or None,
            city=da.get("city") or "",
            country_code=(da.get("country_code") or "DE").upper()[:2],
            contact_name=da.get("contact_name") or None,
            label=da.get("company") or None,
        )

    # 2. Order-Header
    from features.orders import service as order_service
    order_payload: dict[str, Any] = {
        "customer_id": customer_id,
        "ordered_at": date.today(),
        "status": "draft",
        "customer_reference": so.get("customer_reference") or None,
        "notes": (so.get("notes") or "").strip() + "\n\n[Auto-Import aus Posteingang]",
    }
    if shipping_address_id:
        order_payload["shipping_address_id"] = shipping_address_id
    order_payload = {k: ser_value(v) for k, v in order_payload.items() if v is not None and v != ""}
    order_id = order_service.create_order(order_payload)

    # 3. Items — Schema-Felder gemäß replace_order_items RPC
    requested_date = _parse_date_iso(so.get("requested_delivery_date"))
    items_input: list[dict[str, Any]] = []
    for it in so.get("items") or []:
        article_id, _ = _match_article_by_sku(it.get("sku"), party_id=customer_id)
        unit_price_cents = _eur_to_cents(it.get("target_price_eur")) or 0
        qty = float(it.get("qty") or 0)
        line_net = int(round(unit_price_cents * qty))
        item_row: dict[str, Any] = {
            "pos_nr": int(it.get("pos_nr") or len(items_input) + 1),
            "article_id": article_id,
            "description_override": it.get("description") or "",
            "article_title_snapshot": it.get("description") or "(ohne Bezeichnung)",
            "article_sku_snapshot": it.get("sku") or None,
            "qty": qty,
            "unit": it.get("unit") or "Stk",
            "unit_price_cents": unit_price_cents,
            "tax_rate": 19,
            "discount_pct": 0,
            "tax_amount_cents": int(round(line_net * 19 / 100.0)),
            "line_total_cents": line_net,
        }
        if requested_date:
            item_row["expected_delivery_date"] = requested_date
        items_input.append(item_row)
    if items_input:
        try:
            order_service.replace_items(order_id, items_input)
        except Exception:
            # bei Items-Fehler: Order trotzdem behalten, Items werden manuell eingefügt
            pass

    # 4. Mail verlinken
    supabase().table("incoming_mails").update({
        "linked_beleg_type": "order",
        "linked_beleg_id": order_id,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "linked_by": actor_email,
        "status": "linked",
    }).eq("id", mail_id).execute()

    return order_id


# ============================================================
# Mail → Incoming Invoice
# ============================================================

# ============================================================
# Mail → PO-Acknowledgment-Matching
# ============================================================

_PO_NUMBER_RE = re.compile(r"\bBE[\s\-]?(\d{4})[\s\-]?(\d{4})\b", re.IGNORECASE)


def find_po_in_text(text: str) -> str | None:
    """Sucht eine BE-Nr (BE-YYYY-NNNN) in Text. Returns normalisierte Nr oder None."""
    if not text:
        return None
    m = _PO_NUMBER_RE.search(text)
    if not m:
        return None
    return f"BE-{m.group(1)}-{m.group(2)}"


def link_po_acknowledgment(
    *,
    mail_id: str,
    pdf_text: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Sucht in Mail-Subject + Body + ggf. PDF-Text nach BE-Nr und verlinkt mit unserer PO.

    Returns: {linked: bool, po_id?: str, po_number?: str, reason?: str}
    """
    sb = supabase()
    mail = sb.table("incoming_mails").select(
        "id, subject, body_text, body_html, attachments_meta"
    ).eq("id", mail_id).maybe_single().execute().data
    if not mail:
        return {"linked": False, "reason": "mail not found"}

    haystack = " ".join([
        mail.get("subject") or "",
        mail.get("body_text") or "",
        pdf_text or "",
    ])
    po_number = find_po_in_text(haystack)
    if not po_number:
        return {"linked": False, "reason": "no BE-number found"}

    po = (
        sb.table("purchase_orders").select("id, po_number, status")
        .eq("po_number", po_number).limit(1).execute().data
    )
    if not po:
        return {"linked": False, "reason": f"PO {po_number} not in system"}

    po_id = po[0]["id"]
    sb.table("incoming_mails").update({
        "linked_beleg_type": "purchase_order",
        "linked_beleg_id": po_id,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "linked_by": actor_email,
        "status": "linked",
    }).eq("id", mail_id).execute()

    return {"linked": True, "po_id": po_id, "po_number": po_number}


def convert_mail_to_incoming_invoice(
    *,
    mail_id: str,
    parsed_invoice: dict[str, Any],
    pdf_bytes: bytes | None,
    pdf_filename: str | None,
    actor_email: str | None = None,
) -> str:
    """Wraps die existierende `incoming_invoices.service.create_from_ocr` Pipeline."""
    from features.incoming_invoices import service as inv_service
    from lib.incoming_invoice_schema import IncomingInvoiceParsed

    # Re-validate gegen Pydantic
    parsed_obj = IncomingInvoiceParsed.model_validate(parsed_invoice)

    # PDF: aus Mail-Anhang oder Fallback leerer Stub
    if not pdf_bytes:
        # service erwartet bytes — wir nehmen leeres bytes als Stub (PDF kann später nachgereicht werden)
        pdf_bytes = b""
    if not pdf_filename:
        pdf_filename = f"mail_{mail_id[:8]}.pdf"

    invoice_id = inv_service.create_from_ocr(
        parsed=parsed_obj,
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename,
        auto_match=True,
    )

    # Mail verlinken
    supabase().table("incoming_mails").update({
        "linked_beleg_type": "incoming_invoice",
        "linked_beleg_id": invoice_id,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "linked_by": actor_email,
        "status": "linked",
    }).eq("id", mail_id).execute()

    return invoice_id
