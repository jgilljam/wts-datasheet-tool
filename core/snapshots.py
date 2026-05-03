"""Stammdaten-Snapshots für GoBD-konforme Festschreibung.

Hintergrund: Festgeschriebene Belege müssen ihre Stammdaten (Kundenname, Adresse,
Artikelbezeichnung) eingefroren tragen — sonst verändert ein späteres `UPDATE
addresses` rückwirkend die historische Rechnung.

Diese Helper bauen JSONB-Snapshots aus Live-Daten und geben sie zurück, damit
sie atomar im Issue-/Lock-UPDATE mitgesendet werden.
"""

from __future__ import annotations

from typing import Any

from .db import supabase


def build_party_snapshot(party_id: str | None) -> dict[str, Any] | None:
    if not party_id:
        return None
    res = supabase().rpc("build_party_snapshot", {"p_party_id": party_id}).execute()
    return res.data


def build_address_snapshot(address_id: str | None) -> dict[str, Any] | None:
    if not address_id:
        return None
    res = supabase().rpc("build_address_snapshot", {"p_address_id": address_id}).execute()
    return res.data


def build_company_snapshot() -> dict[str, Any] | None:
    res = supabase().rpc("build_company_snapshot", {}).execute()
    return res.data


def build_invoice_snapshot_payload(
    *,
    customer_id: str | None,
    billing_address_id: str | None,
    shipping_address_id: str | None,
) -> dict[str, Any]:
    """Payload-Patch für invoices/orders/quotations — Customer + Adressen + Company."""
    return {
        "customer_snapshot":         build_party_snapshot(customer_id),
        "billing_address_snapshot":  build_address_snapshot(billing_address_id),
        "shipping_address_snapshot": build_address_snapshot(shipping_address_id),
        "company_snapshot":          build_company_snapshot(),
    }


def build_po_snapshot_payload(
    *,
    supplier_id: str | None,
    billing_address_id: str | None,
    shipping_address_id: str | None,
) -> dict[str, Any]:
    """Payload-Patch für purchase_orders — supplier statt customer."""
    return {
        "supplier_snapshot":         build_party_snapshot(supplier_id),
        "billing_address_snapshot":  build_address_snapshot(billing_address_id),
        "shipping_address_snapshot": build_address_snapshot(shipping_address_id),
        "company_snapshot":          build_company_snapshot(),
    }


def build_delivery_snapshot_payload(
    *,
    party_id: str | None,
    source_party_id: str | None,
    shipping_address_id: str | None,
) -> dict[str, Any]:
    """Payload-Patch für deliveries — party + optional source_party (Streckengeschäft)."""
    return {
        "party_snapshot":            build_party_snapshot(party_id),
        "source_party_snapshot":     build_party_snapshot(source_party_id),
        "shipping_address_snapshot": build_address_snapshot(shipping_address_id),
        "company_snapshot":          build_company_snapshot(),
    }


def fetch_article_snapshots(article_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Lädt sku + title_de für eine Liste Artikel-IDs.

    Returns: dict article_id → {"sku": ..., "title": ...}
    """
    ids = [a for a in article_ids if a]
    if not ids:
        return {}
    res = (
        supabase()
        .table("articles")
        .select("id, sku, title_de")
        .in_("id", ids)
        .execute()
        .data
    ) or []
    return {r["id"]: {"sku": r.get("sku"), "title": r.get("title_de")} for r in res}


def enrich_items_with_snapshots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hängt article_title_snapshot + article_sku_snapshot an Item-Dicts.

    Verwendung beim Item-Insert/Replace, um Bezeichnung + SKU einzufrieren.
    """
    article_ids = [it.get("article_id") for it in items if it.get("article_id")]
    snaps = fetch_article_snapshots(article_ids)
    out = []
    for it in items:
        copy = dict(it)
        aid = copy.get("article_id")
        if aid and aid in snaps:
            # nur befüllen wenn nicht schon explizit gesetzt
            copy.setdefault("article_title_snapshot", snaps[aid]["title"])
            copy.setdefault("article_sku_snapshot", snaps[aid]["sku"])
        out.append(copy)
    return out


# ============================================================
# Render-Layer: Snapshot-Daten als Live-Daten exposen, wenn Beleg gelockt
# ============================================================

def apply_snapshot_view(
    doc: dict[str, Any] | None,
    *,
    party_field: str = "customer",
) -> dict[str, Any] | None:
    """Wenn der Beleg gelockt ist (oder bei Quotations: status>=sent) UND
    Snapshots vorhanden, ersetze Live-Joins (customer/billing_address/
    shipping_address) durch die eingefrorenen Snapshot-Daten.

    Damit kann der Renderer unverändert mit doc["customer"] / doc["billing_address"]
    arbeiten — er bekommt entweder Live-FK-Daten (Draft) oder Snapshot-Daten (gelockt).

    Args:
        doc: Beleg-Dict aus repo.get_*(). Wird in-place modifiziert.
        party_field: 'customer' für invoices/orders/quotations,
                     'supplier' für purchase_orders, 'parties' für deliveries.

    Returns: doc (für Method-Chaining) oder None.
    """
    if not doc:
        return doc

    # Lock-Indikator: locked_at oder (für quotations) status nicht draft
    is_frozen = bool(doc.get("locked_at")) or doc.get("status") in (
        "sent", "accepted", "rejected", "expired", "converted",
        "issued", "partially_paid", "paid", "overdue", "cancelled", "reversed",
        "confirmed", "in_production", "partial", "shipped", "done",
        "received", "in_transit", "delivered", "stored",
    )

    if not is_frozen:
        return doc

    # Customer / Supplier
    party_snap = doc.get("customer_snapshot") or doc.get("supplier_snapshot") or doc.get("party_snapshot")
    if party_snap:
        doc[party_field] = party_snap
        # parties-FK heißt in deliveries-repo "parties" (Auto-Embed) — auch setzen
        if party_field == "parties":
            doc["party"] = party_snap

    # Adressen
    if doc.get("billing_address_snapshot"):
        doc["billing_address"] = doc["billing_address_snapshot"]
    if doc.get("shipping_address_snapshot"):
        doc["shipping_address"] = doc["shipping_address_snapshot"]

    # Source-Party (Streckengeschäft, deliveries)
    if doc.get("source_party_snapshot"):
        doc["source_party"] = doc["source_party_snapshot"]

    return doc


def apply_snapshot_to_items(
    items: list[dict[str, Any]],
    *,
    is_frozen: bool,
) -> list[dict[str, Any]]:
    """Wenn Beleg gelockt: ersetzt articles.title_de/sku im Item-Dict durch
    article_title_snapshot/article_sku_snapshot. Renderer liest weiterhin
    aus dem `articles`-Sub-Dict.
    """
    if not is_frozen:
        return items
    out = []
    for it in items:
        copy = dict(it)
        articles_view = dict(copy.get("articles") or {})
        if copy.get("article_title_snapshot"):
            articles_view["title_de"] = copy["article_title_snapshot"]
        if copy.get("article_sku_snapshot"):
            articles_view["sku"] = copy["article_sku_snapshot"]
        copy["articles"] = articles_view
        out.append(copy)
    return out
