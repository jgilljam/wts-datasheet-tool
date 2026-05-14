"""PDF-Bulk-Export: alle Belege eines Vorgangs oder Zeitraums als ZIP.

Verwendung:
    bundle = build_zip_for_vorgang(vorgang_dict)
    bundle = build_zip_for_zeitraum(list_of_vorgaenge, date_from, date_to)

Liefert bytes (für st.download_button) + count_loaded / count_failed
für UI-Feedback.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from typing import Any

from core.db import supabase


# Mapping kind → Ordner im ZIP
_KIND_FOLDER = {
    "purchase_order": "01_bestellungen",
    "incoming_invoice": "03_eingangsrechnung",
    "outgoing_invoice_tool": "05_ausgangsrechnung_kunde",
    "delivery": "04_lieferschein",
    "sent_mail_attachment": "06_mail_anhaenge",
    "order_attachment": "00_auftrag",
}


def _sanitize(name: str) -> str:
    """Ersetzt unsichere Pfad-Zeichen, behält Umlaute."""
    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", name or "")
    cleaned = cleaned.strip().strip(".")
    return cleaned[:120] or "unbenannt"


def _download_one(bucket: str, path: str) -> bytes | None:
    """Lädt eine Datei aus einem Storage-Bucket. None bei Fehler."""
    try:
        data = supabase().storage.from_(bucket).download(path)
        if isinstance(data, bytes):
            return data
        # Manche supabase-Versionen liefern bytes-like
        return bytes(data)
    except Exception:
        return None


def _vorgang_folder_name(vorgang: dict[str, Any]) -> str:
    order_no = _sanitize(vorgang.get("order_number") or "ohne-Nummer")
    customer = _sanitize(vorgang.get("customer_name") or "")
    return f"{order_no}__{customer}" if customer else order_no


def _filename_for_pdf(pdf_meta: dict[str, Any], default_ext: str = ".pdf") -> str:
    """Sinnvoller Dateiname im ZIP."""
    label = pdf_meta.get("label") or "datei"
    # Wenn das Original-Pfad-Ende eine Extension hat, behalte sie
    src_path = pdf_meta.get("path") or ""
    ext_match = re.search(r"\.[A-Za-z0-9]{1,5}$", src_path)
    ext = ext_match.group(0) if ext_match else default_ext
    base = _sanitize(label)
    if not base.lower().endswith(ext.lower()):
        base = f"{base}{ext}"
    return base


def build_zip_for_vorgang(
    vorgang: dict[str, Any],
    pdf_paths: list[dict[str, Any]],
) -> tuple[bytes, dict[str, int]]:
    """Baut ZIP für einen einzelnen Vorgang. Returns (zip_bytes, stats)."""
    loaded = failed = 0
    folder = _vorgang_folder_name(vorgang)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf in pdf_paths:
            content = _download_one(pdf["bucket"], pdf["path"])
            if content is None:
                failed += 1
                continue
            sub = _KIND_FOLDER.get(pdf.get("kind", ""), "99_sonstiges")
            name = _filename_for_pdf(pdf)
            zf.writestr(f"{folder}/{sub}/{name}", content)
            loaded += 1
    return buf.getvalue(), {"loaded": loaded, "failed": failed}


def build_zip_for_zeitraum(
    vorgaenge: list[dict[str, Any]],
    date_from: date | None,
    date_to: date | None,
    collect_paths_fn: Any,
) -> tuple[bytes, dict[str, int]]:
    """Baut ZIP für alle Vorgänge eines Zeitraums + INDEX.csv.

    `collect_paths_fn` ist eine Funktion vorgang→list[pdf_path_dict],
    typisch service.collect_pdf_paths (injiziert um Zirkelimport zu vermeiden).
    """
    loaded = failed = 0
    label_from = date_from.isoformat() if date_from else "alle"
    label_to = date_to.isoformat() if date_to else "alle"
    root = f"WTS_Vorgaenge_{label_from}_bis_{label_to}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # INDEX.csv
        index_buf = io.StringIO()
        writer = csv.writer(index_buf, delimiter=";")
        writer.writerow([
            "Auftragsnummer", "Kunde", "Datum", "Status",
            "Netto EUR",
            "Bestellung?", "AB Lieferant?", "ER Lieferant?", "AR an Kunde?",
            "PDFs",
        ])

        for v in vorgaenge:
            f = v["flags"]
            pdfs = collect_paths_fn(v)
            writer.writerow([
                v.get("order_number") or "",
                v.get("customer_name") or "",
                v.get("ordered_at") or "",
                v.get("status") or "",
                f"{v.get('order_net_eur') or 0:.2f}".replace(".", ","),
                "ja" if f["has_pos"] else "nein",
                "ja" if f["all_pos_confirmed"] else ("teilw" if f["has_pos"] else "nein"),
                "ja" if f["all_pos_invoiced"] else ("teilw" if f["has_pos"] else "nein"),
                "ja" if f["has_outgoing_invoice"] else "nein",
                len(pdfs),
            ])

            folder = _vorgang_folder_name(v)
            for pdf in pdfs:
                content = _download_one(pdf["bucket"], pdf["path"])
                if content is None:
                    failed += 1
                    continue
                sub = _KIND_FOLDER.get(pdf.get("kind", ""), "99_sonstiges")
                name = _filename_for_pdf(pdf)
                zf.writestr(f"{root}/{folder}/{sub}/{name}", content)
                loaded += 1

        zf.writestr(f"{root}/INDEX.csv", index_buf.getvalue().encode("utf-8-sig"))

    return buf.getvalue(), {"loaded": loaded, "failed": failed, "vorgaenge": len(vorgaenge)}
