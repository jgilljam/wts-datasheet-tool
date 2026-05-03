"""Seed: typische WTS-Parteien (Kunden + Lieferanten).

Idempotent über (legal_name, type) — bei zweitem Lauf werden bestehende
Parteien anhand legal_name+type erkannt und übersprungen.
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

SECRETS = Path("/Users/juliangilljam/wts-tools/datasheet-webapp/.streamlit/secrets.toml")

# (legal_name, short_name, type, country_iso)
PARTIES: list[tuple[str, str, str, str]] = [
    # Kunden
    ("Stulz GmbH", "Stulz", "customer", "DE"),
    ("Franke Foodservice Systems", "Franke", "customer", "DE"),
    ("Hefa Kühlmöbel GmbH", "Hefa", "customer", "DE"),
    ("Vaust Kühltechnik", "Vaust", "customer", "DE"),
    ("Van der Heijden Labortechnik GmbH", "Van der Heijden", "customer", "DE"),
    ("Helmut Klein GmbH", "Helmut Klein", "customer", "DE"),
    ("G.S. Stolpen GmbH & Co. KG", "G.S. Stolpen", "customer", "DE"),
    ("Delta Technics Engineering B.V.", "Delta Technics", "customer", "NL"),
    ("Follett Europe Polska Sp. z o.o.", "Follett Polska", "customer", "PL"),
    ("QualServ Europe", "QualServ", "customer", "DE"),
    # Lieferanten
    ("EVCO S.p.A.", "EVCO", "supplier", "IT"),
    ("Calorflex S.r.l.", "Calorflex", "supplier", "IT"),
    ("Titec GmbH", "Titec", "supplier", "DE"),
    ("Produal Oy", "Produal", "supplier", "FI"),
    ("BJB GmbH & Co. KG", "BJB", "supplier", "DE"),
    ("Sensit s.r.o.", "Sensit", "supplier", "CZ"),
    ("SACET S.p.A.", "SACET", "supplier", "IT"),
    ("Eycom Technology Co. Ltd.", "Eycom", "supplier", "CN"),
    ("Sysmetric Ltd.", "Sysmetric", "supplier", "IL"),
]


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
    existing = request("GET", "parties?select=id,legal_name,type")
    have = {(p["legal_name"], p["type"]) for p in existing}

    to_insert = [
        {
            "legal_name": legal,
            "short_name": short,
            "type": ptype,
            "is_active": True,
        }
        for legal, short, ptype, _country in PARTIES
        if (legal, ptype) not in have
    ]

    if not to_insert:
        print(f"Nichts zu tun - alle {len(PARTIES)} Parteien schon vorhanden.")
        return

    inserted = request("POST", "parties", body=to_insert, prefer="return=representation")
    print(f"Neu angelegt: {len(inserted)} Parteien")
    for p in inserted:
        print(f"  - [{p['type']}] {p['legal_name']}")

    # Default-Adressen für die neuen Parteien anlegen (nur Stadt+Land, Adresse leer)
    name_to_country = {legal: country for legal, _, _, country in PARTIES}
    addresses = []
    for p in inserted:
        country = name_to_country.get(p["legal_name"], "DE")
        addresses.append({
            "party_id": p["id"],
            "kind": "billing",
            "label": "Hauptadresse",
            "street": "(noch nicht gepflegt)",
            "city": "(noch nicht gepflegt)",
            "country_code": country,
            "is_default": True,
        })
    if addresses:
        request("POST", "addresses", body=addresses)
        print(f"Default-Adressen angelegt: {len(addresses)}")


if __name__ == "__main__":
    main()
