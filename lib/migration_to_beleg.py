"""Bulk-Import von Aufträgen aus der sevDesk-Migration.

Anders als `mail_to_beleg.convert_mail_to_order` gibt es hier keine
Mail-Verknüpfung — die Aufträge werden direkt als Drafts angelegt.

Wiederverwendet:
  - Party-Match (`_match_party`)
  - Party-Anlage (`_create_party`)
  - Adress-Upsert (`_upsert_address`)
  - Article-SKU-Match (`_match_article_by_sku`)
  - Kontakt-Upsert (`_upsert_contact`)
  - WTS-Selbstname-Filter (`_is_wts_selfname`)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from core.utils import ser_value
from lib.mail_to_beleg import (
    _FREEMAIL_DOMAINS,
    _create_party,
    _eur_to_cents,
    _is_wts_selfname,
    _match_article_by_sku,
    _match_party,
    _parse_date_iso,
    _upsert_address,
    _upsert_contact,
)


def import_order(
    *,
    parsed: dict[str, Any],
    notes_prefix: str = "[sevDesk-Migration]",
) -> dict[str, Any]:
    """Legt einen Auftrag-Draft aus einer parsed Sales-Order an.

    Returns: {order_id, order_number, customer_id, customer_name, items_count}
    """
    so = parsed or {}

    raw_name = so.get("customer_name")
    if _is_wts_selfname(raw_name):
        raw_name = None
    contact_email = (so.get("customer_email") or "").strip()

    customer_id, _reason = _match_party(
        party_type="customer",
        name=raw_name,
        email=contact_email or None,
        vat_id=so.get("customer_vat_id"),
    )
    if not customer_id:
        fallback_name = raw_name
        if not fallback_name and contact_email and "@" in contact_email:
            domain = contact_email.rsplit("@", 1)[1].lower()
            if domain not in _FREEMAIL_DOMAINS:
                fallback_name = domain.split(".")[0].capitalize()
        customer_id = _create_party(
            party_type="customer",
            name=fallback_name or "Unbekannter Kunde (Migration)",
            vat_id=so.get("customer_vat_id"),
            email=contact_email or None,
            notes=(
                "🔄 Auto-erstellt aus sevDesk-Migration. "
                "Bitte legal_name, Adressen, Zahlungskonditionen prüfen."
            ),
        )

    if conf_email := (so.get("confirmation_email") or "").strip():
        _upsert_contact(party_id=customer_id, email=conf_email, role="Auftragsbestätigung")
    if inv_email := (so.get("invoice_email") or "").strip():
        _upsert_contact(party_id=customer_id, email=inv_email, role="Buchhaltung")

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

    from features.orders import service as order_service
    order_payload: dict[str, Any] = {
        "customer_id": customer_id,
        "ordered_at": date.today(),
        "status": "draft",
        "customer_reference": so.get("customer_reference") or None,
        "notes": ((so.get("notes") or "").strip() + f"\n\n{notes_prefix}").strip(),
    }
    if shipping_address_id:
        order_payload["shipping_address_id"] = shipping_address_id
    order_payload = {k: ser_value(v) for k, v in order_payload.items() if v is not None and v != ""}
    order_id = order_service.create_order(order_payload)

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
            pass

    from core.db import supabase
    order_row = (
        supabase().table("orders").select("order_number").eq("id", order_id)
        .maybe_single().execute().data
    ) or {}

    return {
        "order_id": order_id,
        "order_number": order_row.get("order_number") or "",
        "customer_id": customer_id,
        "customer_name": raw_name or so.get("customer_name") or "(unbekannt)",
        "items_count": len(items_input),
    }


def import_orders_batch(
    parsed_orders: list[dict[str, Any]],
    *,
    notes_prefix: str = "[sevDesk-Migration]",
) -> list[dict[str, Any]]:
    """Importiert mehrere Aufträge sequenziell. Fehler einzelner Zeilen brechen
    nicht die ganze Batch ab — sie werden als `error` in der Ergebnis-Zeile gemeldet.
    """
    results: list[dict[str, Any]] = []
    for i, p in enumerate(parsed_orders):
        try:
            res = import_order(parsed=p, notes_prefix=notes_prefix)
            res["index"] = i
            res["error"] = None
            results.append(res)
        except Exception as e:
            results.append({
                "index": i,
                "error": str(e),
                "customer_name": (p or {}).get("customer_name") or "(unbekannt)",
                "order_id": None,
                "order_number": None,
            })
    return results
