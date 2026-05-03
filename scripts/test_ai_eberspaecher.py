"""Klassifiziert + extrahiert die Eberspächer-PO als End-to-End-Demo."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with open(ROOT / ".streamlit/secrets.toml", "rb") as f:
    cfg = tomllib.load(f)

import streamlit as st
class _FS(dict):
    def get(self, k, d=None): return super().get(k, d)
st.secrets = _FS(cfg)

from core.db import supabase
from lib import imap_inbox, mail_ai

# Eberspächer-Mail finden
rows = (
    supabase().table("incoming_mails")
    .select("*")
    .eq("from_email", "aminata.diao@eberspaecher.com")
    .limit(1).execute().data
) or []
if not rows:
    print("Eberspächer-Mail nicht gefunden")
    sys.exit(1)

mail = rows[0]
print(f"=== Mail: {mail['subject']!r} ===")
print(f"  Von: {mail['from_email']}")
print(f"  An:  {mail['to_email']}")
print(f"  Body-Länge: {len(mail.get('body_text') or '')} chars")
atts = mail.get("attachments_meta") or []
print(f"  Anhänge: {len(atts)}")
for i, a in enumerate(atts, 1):
    print(f"    {i}. {a.get('filename')} ({a.get('content_type')}, {a.get('size_bytes', 0)//1024} KB)")

# PDFs laden
print("\n=== PDFs laden …")
pdf_bytes_list = []
for a in atts:
    if (a.get("content_type") or "").lower() != "application/pdf":
        continue
    try:
        data = supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(a["storage_path"])
        pdf_bytes_list.append(data)
        print(f"  ✓ {a.get('filename')} → {len(data)//1024} KB")
    except Exception as e:
        print(f"  ✗ {a.get('filename')}: {e}")

# Klassifikation
print("\n=== Gemini-Klassifikation läuft …")
api_key = cfg["GEMINI_API_KEY"]
model = cfg.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

cls = mail_ai.classify_mail(
    api_key=api_key,
    model=model,
    to_email=mail["to_email"],
    from_email=mail["from_email"],
    subject=mail.get("subject") or "",
    body_text=mail.get("body_text") or "",
    attachment_filenames=[a.get("filename") for a in atts],
    pdf_bytes_list=pdf_bytes_list,
)
print(f"  Kategorie: {cls.category}")
print(f"  Konfidenz: {cls.confidence}")
print(f"  Reason:    {cls.reason}")

# Sales-Order Extract
if cls.category == "sales_order":
    print("\n=== Sales-Order-Extraktion läuft …")
    so = mail_ai.extract_sales_order(
        api_key=api_key,
        model=model,
        from_email=mail["from_email"],
        subject=mail.get("subject") or "",
        body_text=mail.get("body_text") or "",
        pdf_bytes_list=pdf_bytes_list,
    )
    print(json.dumps(so.model_dump(), indent=2, ensure_ascii=False))
