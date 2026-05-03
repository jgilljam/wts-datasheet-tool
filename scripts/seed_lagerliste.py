"""Seed: Artikel + Initial-Bestände aus WTS-Lagerliste 28.02.2026.

Idempotent: Articles werden per `on_conflict=sku` upserted.
Stock-Movements werden NICHT idempotent eingespielt — vor erneutem Lauf
zuerst alle 'Seed'-Movements löschen (siehe Cleanup unten).
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

SECRETS = Path("/Users/juliangilljam/wts-tools/datasheet-webapp/.streamlit/secrets.toml")
SEED_NOTE = "Initial-Seed aus PDF-Lagerliste 28.02.2026"

# (Bezeichnung, SKU, Bestand, EK-String)
ARTICLES: list[tuple[str, str, int, str]] = [
    ("PS300 pressure switch", "PR1101FR", 9, "9,50"),
    ("K50-H1107", "CP1201FR", 0, "7,98"),
    ("GWSAS 110", "SA1308ST", 0, "7,24"),
    ("GWSAS 109", "SA1307ST", 90, "8,72"),
    ("GWSAS 108", "SA1306ST", 100, "0,89"),
    ("GWSAS 105", "SA1305ST", 740, "2,43"),
    ("GWSAS 104", "SA1304ST", 10, "1,92"),
    ("GWSAS 103", "SA1303ST", 80, "2,48"),
    ("GWSAS 102", "SA1302ST", 0, "2,74"),
    ("GWSAS 101", "SA1301ST", 78, "9,42"),
    ("EVHTP523", "EV1421MU", 1, "0,00"),
    ("EVHP500", "EV1422MU", 2, "45,15"),
    ("EVHP503", "EV1423MU", 1, "45,15"),
    ("NSNE1,55/6E15NSE", "SA1309MU", 157, "0,85"),
    ("NSNE1,55/6E50ONSE", "SA1311MU", 95, "1,51"),
    ("NSNE1,55/6E30NSE", "SA1310MU", 23, "1,23"),
    ("NTC Metall", "DI1401MU", 114, "2,80"),
    ("Magnetventile", "SF1701JO", 80, "19,78"),
    ("25.104'er", "BJ1801JO", 0, "1,92"),
    ("25.106'er", "BJ10802JO", 0, "4,44"),
    ("Feinfilter ZEL0-10GPP005V", "HO1601BO", 0, "85,00"),
    ("OBTF/Pt1000/2.0/SIL", "TI2001MU", 200, "4,80"),
    ("Ölsumpf CCA0001", "CA1501HE", 0, "8,05"),
    ("Tropfschutz 0025100010", "EV1420HE", 400, "0,75"),
    ("TFSB000", "EV1424MU", 20, "3,70"),
    ("EV3421M3", "EV1419MU", 3, "0,00"),
    ("EVPS", "EV1418MU", 0, "0,00"),
    ("EV3Key", "EV1417MU", 1, "57,38"),
    ("EVIF22TWX", "EV1416MU", 25, "31,05"),
    ("EVIF25TBX", "EV1415VA", 51, "44,70"),
    ("EV3422M3", "EV1414MU", 2, "0,00"),
    ("EV3411M7PRXXX1", "EV1413FR", 170, "31,90"),
    ("EV3412M3", "EV1412MU", 0, "0,00"),
    ("EV3412M9", "EV1411MU", 2, "50,40"),
    ("EV3402P3", "EV1410MU", 50, "19,65"),
    ("EV3423M3", "EV1409MU", 10, "44,10"),
    ("EV3123N7", "EV1408MU", 1, "0,00"),
    ("EV3294N9", "EV1407CH", 0, "27,43"),
    ("EV3294N3", "EV1406VA", 2, "26,50"),
    ("EV3221N7", "EV1405MU", 219, "18,65"),
    ("EV3203N7", "EV1404MU", 85, "20,90"),
    ("EV3X21N7", "EV1403MU", 23, "0,00"),
    ("EV6412M3VXBS", "EV1402MU", 0, "63,90"),
    ("EV6412M7VXBS", "EV1401QU", 45, "14,10"),
    ("Double Heating Wire 1574001", "CA1502FR", 9, "20,90"),
    ("Double Heating Wire 1580638", "CA1503FR", 75, "7,95"),
    ("Double Heating Wire 1573999", "CA1504FR", 2, "13,70"),
    ("Double Heating Wire 1573997", "CA1505FR", 33, "11,50"),
    ("Rohrheizkörper 1561429", "CA1506FR", 74, "6,90"),
]


def to_cents(s: str) -> int:
    s = s.replace(".", "").replace(",", ".")
    return int(round(float(s) * 100))


def request(method: str, path: str, body=None, prefer: str | None = None) -> list[dict]:
    secrets = tomllib.load(SECRETS.open("rb"))
    headers = {
        "apikey": secrets["SUPABASE_SECRET_KEY"],
        "Authorization": f"Bearer {secrets['SUPABASE_SECRET_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": "wts-seed/1.0",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{secrets['SUPABASE_URL']}/rest/v1/{path}",
        method=method,
        data=data,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text.strip() else []
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {method} {path}: {e.read().decode('utf-8')[:1000]}", file=sys.stderr)
        raise


def main() -> None:
    # 1. Cleanup: alte Seed-Movements entfernen (idempotenter Re-Run)
    cleanup = request("DELETE", f"stock_movements?note=eq.{urllib.request.quote(SEED_NOTE)}", prefer="return=representation")
    print(f"Cleanup: {len(cleanup)} alte Seed-Movements entfernt.")

    # 2. Articles upserten
    payload = [
        {
            "sku": sku,
            "title_de": title,
            "unit": "Stk",
            "default_price_cents": to_cents(price),
            "is_active": True,
        }
        for title, sku, _, price in ARTICLES
    ]
    inserted = request(
        "POST",
        "articles?on_conflict=sku",
        body=payload,
        prefer="return=representation,resolution=merge-duplicates",
    )
    print(f"Articles upserted: {len(inserted)}")
    sku_to_id = {a["sku"]: a["id"] for a in inserted}

    missing = [sku for _, sku, _, _ in ARTICLES if sku not in sku_to_id]
    if missing:
        print(f"WARN: keine ID für SKUs: {missing}", file=sys.stderr)

    # 3. Initial-Bestände als Adjustment-Movements einspielen
    movements = [
        {
            "article_id": sku_to_id[sku],
            "qty_delta": qty,
            "movement_type": "adjustment",
            "actor_label": "Seed",
            "note": SEED_NOTE,
        }
        for _, sku, qty, _ in ARTICLES
        if qty > 0 and sku in sku_to_id
    ]
    if movements:
        moves = request(
            "POST",
            "stock_movements",
            body=movements,
            prefer="return=representation",
        )
        print(f"Movements inserted: {len(moves)}")
        total_qty = sum(m["qty_delta"] for m in moves)
        print(f"Gesamt-Bestand: {total_qty} Stück")
    else:
        print("Keine Bestände > 0 zu seeden.")


if __name__ == "__main__":
    main()
