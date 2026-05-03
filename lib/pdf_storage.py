"""Persistierung und Re-Download von Beleg-PDFs aus Supabase Storage.

GoBD-Hintergrund: Festgeschriebene Belege (Rechnungen, Mahnungen, Lieferscheine,
Aufträge, Bestellungen) müssen byte-stable wiederherstellbar sein. Wir
generieren das PDF einmal beim Issue/Lock und legen es im Bucket "belege"
ab. Spätere Downloads laden aus Storage statt neu zu rendern.

Pfad-Schema im Bucket:
    {beleg_type}/{jahr}/{beleg_number}.pdf
    Beispiel: invoice/2026/RE-2026-0001.pdf
"""

from __future__ import annotations

import hashlib
import re
from typing import Callable

from core.db import supabase

BUCKET = "belege"


def sha256_hex(data: bytes) -> str:
    """SHA-256-Hex-Digest der PDF-Bytes — Manipulations-Indikator (GoBD P10)."""
    return hashlib.sha256(data).hexdigest()


# ---------- Pfad-Helper ----------

_YEAR_RE = re.compile(r"-(\d{4})-")


def _extract_year(beleg_number: str) -> str:
    m = _YEAR_RE.search(beleg_number or "")
    return m.group(1) if m else "unsorted"


def make_path(beleg_type: str, beleg_number: str) -> str:
    """Erzeugt den Storage-Pfad für einen Beleg."""
    safe_number = re.sub(r"[^A-Za-z0-9._-]", "_", beleg_number or "unknown")
    return f"{beleg_type}/{_extract_year(beleg_number)}/{safe_number}.pdf"


# ---------- Storage-Primitives ----------

def upload_pdf(path: str, pdf_bytes: bytes, *, upsert: bool = False) -> str:
    """Lädt PDF in den belege-Bucket. Returns den Pfad."""
    supabase().storage.from_(BUCKET).upload(
        path=path,
        file=pdf_bytes,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true" if upsert else "false",
        },
    )
    return path


def download_pdf(path: str) -> bytes:
    """Lädt PDF aus dem belege-Bucket."""
    return supabase().storage.from_(BUCKET).download(path)


def signed_url(path: str, expires_in: int = 3600) -> str:
    res = supabase().storage.from_(BUCKET).create_signed_url(path, expires_in)
    return res.get("signedURL") or res.get("signed_url") or ""


def remove_pdf(path: str) -> None:
    """Löscht ein PDF aus dem Bucket. Idempotent."""
    try:
        supabase().storage.from_(BUCKET).remove([path])
    except Exception:
        pass


# ---------- High-Level: render-or-fetch ----------

def render_or_fetch(
    *,
    table: str,
    doc: dict,
    beleg_type: str,
    beleg_number: str,
    render_fn: Callable[[], bytes],
    persist: bool,
) -> tuple[bytes, str | None]:
    """Liefert PDF-Bytes — aus Storage wenn vorhanden, sonst frisch rendern.

    Args:
        table: DB-Tabellenname für UPDATE (z.B. 'invoices').
        doc: Beleg-Row aus DB; muss 'id' und 'pdf_storage_path' enthalten.
        beleg_type: Pfad-Prefix im Bucket (z.B. 'invoice', 'quotation').
        beleg_number: Beleg-Nr. für Pfad (z.B. 'RE-2026-0001').
        render_fn: Closure, die die PDF-Bytes erzeugt.
        persist: Wenn True und PDF noch nicht im Storage, wird sie gespeichert
                 und der Pfad in der DB persistiert. Üblicherweise persist=is_locked.

    Returns:
        (pdf_bytes, storage_path_or_None) — storage_path nur wenn persistiert.
    """
    existing_path = (doc or {}).get("pdf_storage_path")
    if existing_path:
        try:
            return download_pdf(existing_path), existing_path
        except Exception:
            # Storage out of sync (z.B. nach manuellem bucket-clear) → re-render
            pass

    pdf_bytes = render_fn()

    if not persist:
        return pdf_bytes, None

    path = make_path(beleg_type, beleg_number)
    try:
        upload_pdf(path, pdf_bytes, upsert=True)
    except Exception as exc:
        # Persist-Fehler nicht durchreichen — User soll trotzdem Download bekommen
        return pdf_bytes, None

    pdf_hash = sha256_hex(pdf_bytes)
    try:
        supabase().table(table).update({
            "pdf_storage_path": path,
            "pdf_hash_sha256": pdf_hash,
        }).eq("id", doc["id"]).execute()
    except Exception:
        # DB-Update fehlgeschlagen, aber Storage-Upload war ok — beim
        # nächsten Aufruf wird re-uploaded (kein dauerhafter Schaden)
        pass

    return pdf_bytes, path


# ---------- Convenience: persist nach erfolgreichem Lock ----------

def persist_after_lock(
    *,
    table: str,
    doc_id: str,
    beleg_type: str,
    beleg_number: str,
    pdf_bytes: bytes,
) -> str | None:
    """Speichert ein bereits gerendertes PDF und schreibt den Pfad in die DB.

    Genutzt z.B. direkt nach einem Status-Übergang zu 'issued' / 'shipped'.
    Returns den Pfad bei Erfolg, None bei Fehler.
    """
    path = make_path(beleg_type, beleg_number)
    try:
        upload_pdf(path, pdf_bytes, upsert=True)
    except Exception:
        return None
    pdf_hash = sha256_hex(pdf_bytes)
    try:
        supabase().table(table).update({
            "pdf_storage_path": path,
            "pdf_hash_sha256": pdf_hash,
        }).eq("id", doc_id).execute()
    except Exception:
        return None
    return path
