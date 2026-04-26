#!/usr/bin/env bash
# Spiegelt die Komponenten aus dem WTS-Astro-Repo in den webapp-Library-Ordner.
# Aufruf:   bash scripts/sync_library.sh
# Läuft nur lokal — Streamlit Cloud zieht den Inhalt von library/ aus dem Git-Repo.

set -euo pipefail

SRC="${WTS_ASTRO_CONTENT_DIR:-$HOME/Documents/wts-website/src/content/komponenten}"
DST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/library"

if [ ! -d "$SRC" ]; then
  echo "Quell-Ordner nicht gefunden: $SRC" >&2
  echo "Setze WTS_ASTRO_CONTENT_DIR, falls die Astro-Site woanders liegt." >&2
  exit 1
fi

mkdir -p "$DST"

# Spiegeln: löschen was nicht mehr in SRC liegt, JSONs aktualisieren.
rsync -av --delete --include='*.json' --exclude='*' "$SRC/" "$DST/"

count=$(find "$DST" -maxdepth 1 -name '*.json' | wc -l | tr -d ' ')
echo ""
echo "→ $count Komponenten in $DST"
echo "→ Vergiss nicht: git add library/ && git commit && git push, damit die Cloud-App es sieht."
