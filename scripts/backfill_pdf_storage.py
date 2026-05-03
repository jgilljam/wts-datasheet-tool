"""Backfill: Bestehende issued/locked Belege ins PDF-Archiv persistieren.

Geht durch alle Beleg-Tabellen, sucht Datensätze, die einen "festgeschriebenen"
Status haben aber keinen pdf_storage_path — rendert das PDF und persistiert
es in den belege-Bucket.

Idempotent: bereits persistierte Belege werden übersprungen.

Usage:
    .venv/bin/python scripts/backfill_pdf_storage.py
    .venv/bin/python scripts/backfill_pdf_storage.py --dry-run
    .venv/bin/python scripts/backfill_pdf_storage.py --only invoices
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import supabase
from lib.pdf_storage import persist_after_lock


# ----- Per-Beleg-Typ Konfiguration -----

def _backfill_invoices(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.invoices import repo as inv_repo
    from lib.beleg_generator import render_rechnung_pdf

    rows = (
        supabase()
        .table("invoices")
        .select("id, invoice_number, status, locked_at, pdf_storage_path")
        .not_.is_("locked_at", "null")
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[invoices] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        nr = r.get("invoice_number") or r["id"]
        if dry_run:
            print(f"  [dry] {nr}")
            ok += 1
            continue
        try:
            inv = inv_repo.get_invoice(r["id"])
            items = inv_repo.list_invoice_items(r["id"])
            if not items:
                print(f"  ⚠ {nr}: keine Positionen — übersprungen")
                continue
            pdf = render_rechnung_pdf(inv, items)
            path = persist_after_lock(
                table="invoices",
                doc_id=r["id"],
                beleg_type="invoice",
                beleg_number=nr,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {nr} → {path}")
                ok += 1
            else:
                print(f"  ✗ {nr} — persist returned None")
                fail += 1
                errs.append(nr)
        except Exception as exc:
            print(f"  ✗ {nr}: {exc}")
            fail += 1
            errs.append(f"{nr}: {exc}")
    return ok, fail, errs


def _backfill_orders(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.orders import repo as ord_repo
    from lib.beleg_generator import render_auftragsbestaetigung_pdf

    rows = (
        supabase()
        .table("orders")
        .select("id, order_number, status, locked_at, pdf_storage_path")
        .in_("status", ["confirmed", "in_production", "shipped", "done"])
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[orders] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        nr = r.get("order_number") or r["id"]
        if dry_run:
            print(f"  [dry] {nr}")
            ok += 1
            continue
        try:
            order = ord_repo.get_order(r["id"])
            items = ord_repo.list_order_items(r["id"])
            if not items:
                print(f"  ⚠ {nr}: keine Positionen — übersprungen")
                continue
            pdf = render_auftragsbestaetigung_pdf(order, items)
            path = persist_after_lock(
                table="orders",
                doc_id=r["id"],
                beleg_type="order",
                beleg_number=nr,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {nr} → {path}")
                ok += 1
            else:
                print(f"  ✗ {nr}")
                fail += 1
                errs.append(nr)
        except Exception as exc:
            print(f"  ✗ {nr}: {exc}")
            fail += 1
            errs.append(f"{nr}: {exc}")
    return ok, fail, errs


def _backfill_deliveries(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.deliveries import repo as dlv_repo
    from lib.lieferschein_generator import render_lieferschein_pdf

    rows = (
        supabase()
        .table("deliveries")
        .select("id, delivery_number, status, direction, locked_at, pdf_storage_path")
        .in_("status", ["shipped", "received", "cancelled"])
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[deliveries] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        nr = r.get("delivery_number") or r["id"]
        if dry_run:
            print(f"  [dry] {nr}")
            ok += 1
            continue
        try:
            dlv = dlv_repo.get_delivery(r["id"])
            items = dlv_repo.list_delivery_items(r["id"])
            if not items:
                print(f"  ⚠ {nr}: keine Positionen — übersprungen")
                continue
            pdf = render_lieferschein_pdf(dlv, items)
            path = persist_after_lock(
                table="deliveries",
                doc_id=r["id"],
                beleg_type="delivery",
                beleg_number=nr,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {nr} → {path}")
                ok += 1
            else:
                fail += 1
                errs.append(nr)
        except Exception as exc:
            print(f"  ✗ {nr}: {exc}")
            fail += 1
            errs.append(f"{nr}: {exc}")
    return ok, fail, errs


def _backfill_quotations(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.quotations import repo as q_repo
    from lib.beleg_generator import render_angebot_pdf

    rows = (
        supabase()
        .table("quotations")
        .select("id, quotation_number, status, hide_totals_in_pdf, pdf_storage_path")
        .in_("status", ["sent", "accepted", "rejected", "converted", "expired"])
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[quotations] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        nr = r.get("quotation_number") or r["id"]
        if dry_run:
            print(f"  [dry] {nr}")
            ok += 1
            continue
        try:
            q = q_repo.get_quotation(r["id"])
            items = q_repo.list_quotation_items(r["id"])
            if not items:
                print(f"  ⚠ {nr}: keine Positionen — übersprungen")
                continue
            pdf = render_angebot_pdf(
                q, items, hide_totals=bool(q.get("hide_totals_in_pdf"))
            )
            path = persist_after_lock(
                table="quotations",
                doc_id=r["id"],
                beleg_type="quotation",
                beleg_number=nr,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {nr} → {path}")
                ok += 1
            else:
                fail += 1
                errs.append(nr)
        except Exception as exc:
            print(f"  ✗ {nr}: {exc}")
            fail += 1
            errs.append(f"{nr}: {exc}")
    return ok, fail, errs


def _backfill_purchase_orders(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.purchase_orders import repo as po_repo
    from lib.beleg_generator import render_bestellung_pdf

    rows = (
        supabase()
        .table("purchase_orders")
        .select("id, po_number, status, locked_at, pdf_storage_path")
        .in_("status", ["sent", "confirmed", "in_production", "shipped", "received", "done"])
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[purchase_orders] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        nr = r.get("po_number") or r["id"]
        if dry_run:
            print(f"  [dry] {nr}")
            ok += 1
            continue
        try:
            po = po_repo.get_po(r["id"])
            items = po_repo.list_po_items(r["id"])
            if not items:
                print(f"  ⚠ {nr}: keine Positionen — übersprungen")
                continue
            pdf = render_bestellung_pdf(po, items)
            path = persist_after_lock(
                table="purchase_orders",
                doc_id=r["id"],
                beleg_type="purchase_order",
                beleg_number=nr,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {nr} → {path}")
                ok += 1
            else:
                fail += 1
                errs.append(nr)
        except Exception as exc:
            print(f"  ✗ {nr}: {exc}")
            fail += 1
            errs.append(f"{nr}: {exc}")
    return ok, fail, errs


def _backfill_dunnings(dry_run: bool) -> tuple[int, int, list[str]]:
    from features.dunning import repo as d_repo
    from features.invoices import repo as inv_repo
    from lib.beleg_generator import render_mahnung_pdf

    rows = (
        supabase()
        .table("invoice_dunnings")
        .select("id, invoice_id, level, pdf_storage_path")
        .is_("pdf_storage_path", "null")
        .execute()
        .data
    )
    print(f"\n[invoice_dunnings] {len(rows)} kandidat(en)")
    ok, fail, errs = 0, 0, []
    for r in rows:
        ident = f"M{r.get('level')}-{r['id'][:8]}"
        if dry_run:
            print(f"  [dry] {ident}")
            ok += 1
            continue
        try:
            dunning = d_repo.get_dunning(r["id"])
            inv = inv_repo.get_invoice(r["invoice_id"])
            items = inv_repo.list_invoice_items(r["invoice_id"])
            if not (dunning and inv and items):
                print(f"  ⚠ {ident}: fehlende Daten — übersprungen")
                continue
            pdf = render_mahnung_pdf(inv, items, dunning)
            beleg_number = f"M{dunning['level']}-{inv['invoice_number']}"
            path = persist_after_lock(
                table="invoice_dunnings",
                doc_id=r["id"],
                beleg_type="dunning",
                beleg_number=beleg_number,
                pdf_bytes=pdf,
            )
            if path:
                print(f"  ✓ {beleg_number} → {path}")
                ok += 1
            else:
                fail += 1
                errs.append(beleg_number)
        except Exception as exc:
            print(f"  ✗ {ident}: {exc}")
            fail += 1
            errs.append(f"{ident}: {exc}")
    return ok, fail, errs


HANDLERS = {
    "invoices": _backfill_invoices,
    "orders": _backfill_orders,
    "deliveries": _backfill_deliveries,
    "quotations": _backfill_quotations,
    "purchase_orders": _backfill_purchase_orders,
    "dunnings": _backfill_dunnings,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        choices=list(HANDLERS.keys()),
        help="Nur einen Beleg-Typ backfillen",
    )
    args = parser.parse_args()

    targets = [args.only] if args.only else list(HANDLERS.keys())

    total_ok, total_fail, total_errs = 0, 0, []
    for t in targets:
        ok, fail, errs = HANDLERS[t](args.dry_run)
        total_ok += ok
        total_fail += fail
        total_errs.extend(errs)

    print(f"\n{'=' * 50}")
    print(f"Backfill {'(DRY-RUN) ' if args.dry_run else ''}fertig:")
    print(f"  ✓ {total_ok} persistiert")
    print(f"  ✗ {total_fail} fehlgeschlagen")
    if total_errs:
        for e in total_errs:
            print(f"    · {e}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
