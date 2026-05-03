"""Re-klassifiziert die Eberspächer-Mail und triggert den Convert-To-Order."""

from __future__ import annotations

import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with open(ROOT / ".streamlit/secrets.toml", "rb") as f:
    cfg = tomllib.load(f)

import streamlit as st
class _FS(dict):
    def get(self, k, d=None): return super().get(k, d)
st.secrets = _FS(cfg)
st.session_state = {"user": {"email": "juligill67@gmail.com"}, "user_email": "juligill67@gmail.com"}

from core.db import supabase
from lib import imap_inbox, mail_ai, mail_to_beleg

mail = (
    supabase().table("incoming_mails")
    .select("*")
    .eq("from_email", "aminata.diao@eberspaecher.com")
    .single().execute().data
)
print(f"=== Mail: {mail['subject']!r} (id={mail['id'][:8]})")

# PDFs laden
atts = mail.get("attachments_meta") or []
pdf_bytes_list = []
for a in atts:
    if (a.get("content_type") or "").lower() != "application/pdf":
        continue
    try:
        data = supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(a["storage_path"])
        pdf_bytes_list.append(data)
    except Exception:
        pass
print(f"  PDFs: {len(pdf_bytes_list)}")

# Re-Klassifikation mit verbessertem Prompt
api_key = cfg["GEMINI_API_KEY"]
model = cfg.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

print("\n=== Klassifikation …")
cls = mail_ai.classify_mail(
    api_key=api_key, model=model,
    to_email=mail["to_email"], from_email=mail["from_email"],
    subject=mail.get("subject") or "", body_text=mail.get("body_text") or "",
    attachment_filenames=[a.get("filename") for a in atts],
    pdf_bytes_list=pdf_bytes_list,
)
print(f"  → {cls.category} ({cls.confidence})")

print("\n=== Sales-Order-Extraktion …")
so = mail_ai.extract_sales_order(
    api_key=api_key, model=model,
    from_email=mail["from_email"], subject=mail.get("subject") or "",
    body_text=mail.get("body_text") or "", pdf_bytes_list=pdf_bytes_list,
)
so_dict = so.model_dump()
print(f"  customer_name: {so_dict['customer_name']!r}")
print(f"  customer_reference: {so_dict['customer_reference']!r}")
print(f"  items: {len(so_dict['items'])}")
for it in so_dict["items"]:
    print(f"    [{it['pos_nr']}] {it['qty']} × {it['sku']} — {it['description']} @ {it['target_price_eur']} €")

# Persist KI-Ergebnis in DB
supabase().table("incoming_mails").update({
    "ai_category": cls.category,
    "ai_confidence": cls.confidence,
    "ai_model": model,
    "ai_processed_at": datetime.utcnow().isoformat() + "Z",
    "status": "ai_classified",
    "ai_extracted_payload": {
        "classification": {"category": cls.category, "confidence": cls.confidence, "reason": cls.reason},
        "sales_order": so_dict,
    },
}).eq("id", mail["id"]).execute()

# Convert
print("\n=== Convert → Order-Draft …")
order_id = mail_to_beleg.convert_mail_to_order(
    mail_id=mail["id"],
    sales_order_payload=so_dict,
    mail_from_email=mail["from_email"],
    actor_email="juligill67@gmail.com",
)
print(f"  ✓ Order angelegt: {order_id}")

# Verifizieren
order = supabase().table("orders").select(
    "id, order_number, customer_id, customer_reference, status, "
    "customer:parties!customer_id(id, legal_name, type)"
).eq("id", order_id).single().execute().data
print("\n=== Order-Eintrag in DB:")
print(f"  Order-Nr:   {order['order_number']}")
print(f"  Status:     {order['status']}")
print(f"  Kd-Ref:     {order['customer_reference']}")
print(f"  Customer:   {order['customer']['legal_name']} (id={order['customer_id'][:8]})")

items = supabase().table("order_items").select(
    "pos_nr, article_sku_snapshot, article_title_snapshot, qty, unit_price_cents, line_total_cents"
).eq("order_id", order_id).order("pos_nr").execute().data or []
print(f"\n=== Order-Items ({len(items)}):")
for it in items:
    eur = (it.get("unit_price_cents") or 0) / 100
    total = (it.get("line_total_cents") or 0) / 100
    print(f"  [{it['pos_nr']}] {it['qty']} × {it['article_sku_snapshot']} — {it['article_title_snapshot']} @ {eur:.2f} € = {total:.2f} €")

# Mail-Status
m = supabase().table("incoming_mails").select(
    "status, linked_beleg_type, linked_beleg_id"
).eq("id", mail["id"]).single().execute().data
print(f"\n=== Mail-Status nach Convert: {m['status']} → {m['linked_beleg_type']}/{m['linked_beleg_id'][:8]}")
