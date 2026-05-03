"""Beliebige SQL-Datei in Supabase ausführen via Management API."""
import sys
import urllib.request
import urllib.error
import json
import tomllib
from pathlib import Path

SECRETS = Path("/Users/juliangilljam/wts-tools/datasheet-webapp/.streamlit/secrets.toml")

if len(sys.argv) < 2:
    print("Usage: run_migration.py <path-to-sql>", file=sys.stderr)
    sys.exit(1)

migration = Path(sys.argv[1])
secrets = tomllib.load(SECRETS.open("rb"))
sql = migration.read_text(encoding="utf-8")

req = urllib.request.Request(
    f"https://api.supabase.com/v1/projects/{secrets['SUPABASE_PROJECT_REF']}/database/query",
    method="POST",
    headers={
        "Authorization": f"Bearer {secrets['SUPABASE_PAT']}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    },
    data=json.dumps({"query": sql}).encode("utf-8"),
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"HTTP {resp.status}")
        print(resp.read().decode("utf-8")[:2000])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    print(e.read().decode("utf-8")[:2000])
    sys.exit(1)
