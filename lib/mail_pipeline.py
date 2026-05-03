"""Pipeline-Orchestrierung für eingehende Mails.

Stufen:
    1. classify_and_extract(mail_id)  → Gemini klassifiziert + extrahiert,
       persistiert Ergebnis in incoming_mails.ai_extracted_payload.
    2. auto_convert_if_eligible(mail_id) → wenn Auto-Convert an + Konfidenz hoch
       + Customer/Supplier per Domain matchbar → erstellt Beleg-Draft.
    3. process_new_mail(mail_id) → Stufe 1 + Stufe 2 hintereinander.

Wird sowohl vom imap_inbox.pull (synchron pro neuer Mail) als auch von Inbox-UI
(„🤖 KI analysieren" / „→ Auftrag anlegen") aufgerufen.

GoBD-Sicherung: Auto-Convert legt NUR Drafts an — niemals locked, niemals direkt
versendet. Mail-Versand bleibt strikt manuell (siehe feedback_no_auto_mail_send).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from core.db import supabase

from . import imap_inbox, mail_ai, mail_to_beleg


# ============================================================
# Settings (aus st.secrets, default-sicher)
# ============================================================

def settings() -> dict[str, Any]:
    """Liest Pipeline-Settings: app_settings (DB) zuerst, dann Streamlit-Secrets, dann Default."""
    from core import app_settings as cfg
    return {
        "auto_classify": cfg.get_bool("mail.auto_classify", default=True, secret_fallback="MAIL_AUTO_CLASSIFY"),
        "auto_convert": cfg.get_bool("mail.auto_convert", default=False, secret_fallback="MAIL_AUTO_CONVERT"),
        "auto_convert_min_confidence": cfg.get_str(
            "mail.auto_convert_min_confidence",
            default="high",
            secret_fallback="MAIL_AUTO_CONVERT_MIN_CONFIDENCE",
        ),
        "gemini_model": str(st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash-lite")),
    }


def _gemini_creds() -> tuple[str, str] | None:
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        return None
    model = settings()["gemini_model"]
    return api_key, model


# ============================================================
# Helper: PDFs aus Anhängen laden
# ============================================================

def _load_pdf_attachments(atts: list[dict[str, Any]]) -> list[bytes]:
    out = []
    for att in atts or []:
        if (att.get("content_type") or "").lower() != "application/pdf":
            continue
        path = att.get("storage_path")
        if not path:
            continue
        try:
            data = supabase().storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(path)
            out.append(data)
        except Exception:
            continue
    return out


# ============================================================
# Stufe 1: Klassifikation + Extraktion
# ============================================================

def classify_and_extract(mail_id: str) -> dict[str, Any]:
    """Lässt Gemini eine Mail klassifizieren + ggf. extrahieren.

    Returns: aktualisiertes incoming_mails-Row.
    Bei Fehlern: status='failed' + ai_error, kein Re-Raise.
    """
    creds = _gemini_creds()
    if creds is None:
        return _set_failure(mail_id, "GEMINI_API_KEY nicht gesetzt")

    api_key, model = creds
    sb = supabase()

    mail = sb.table("incoming_mails").select("*").eq("id", mail_id).maybe_single().execute().data
    if not mail:
        raise ValueError(f"Mail {mail_id} nicht gefunden.")

    sb.table("incoming_mails").update({"status": "ai_processing"}).eq("id", mail_id).execute()

    try:
        atts = mail.get("attachments_meta") or []
        pdf_bytes_list = _load_pdf_attachments(atts)
        attachment_filenames = [a.get("filename") for a in atts if a.get("filename")]

        cls = mail_ai.classify_mail(
            api_key=api_key,
            model=model,
            to_email=mail.get("to_email") or "",
            from_email=mail.get("from_email") or "",
            subject=mail.get("subject") or "",
            body_text=mail.get("body_text") or "",
            attachment_filenames=attachment_filenames,
            pdf_bytes_list=pdf_bytes_list,
        )

        # Primärer Anhang (bei mehreren PDFs nur den echten Beleg verwenden,
        # nicht AGB/Spec/Datenblatt)
        primary_idx = cls.primary_attachment_index
        primary_pdf: bytes | None = None
        if pdf_bytes_list:
            if 0 <= primary_idx < len(pdf_bytes_list):
                primary_pdf = pdf_bytes_list[primary_idx]
            else:
                primary_pdf = pdf_bytes_list[0]

        update: dict[str, Any] = {
            "ai_category": cls.category,
            "ai_confidence": cls.confidence,
            "ai_model": model,
            "ai_processed_at": datetime.now(timezone.utc).isoformat(),
            "status": "ai_classified",
            "ai_extracted_payload": {
                "classification": {
                    "category": cls.category,
                    "confidence": cls.confidence,
                    "reason": cls.reason,
                    "primary_attachment_index": primary_idx,
                },
            },
        }

        if cls.category == "sales_order":
            try:
                so = mail_ai.extract_sales_order(
                    api_key=api_key, model=model,
                    from_email=mail.get("from_email") or "",
                    subject=mail.get("subject") or "",
                    body_text=mail.get("body_text") or "",
                    pdf_bytes_list=[primary_pdf] if primary_pdf else [],
                )
                so_dict = so.model_dump()
                update["ai_extracted_payload"]["sales_order"] = so_dict
                # Item-Validation
                try:
                    warnings = validate_sales_order_items(so_dict)
                    if warnings:
                        update["ai_extracted_payload"]["validation_warnings"] = warnings
                        # Bei kritischen Warnings Confidence dämpfen
                        if any(w["type"] == "price_mismatch" for w in warnings):
                            if cls.confidence == "high":
                                update["ai_confidence"] = "medium"
                except Exception:
                    pass
            except Exception as e:
                update["ai_error"] = f"Sales-Order-Extract: {e}"[:500]

        elif cls.category == "incoming_invoice":
            if primary_pdf:
                try:
                    from .incoming_invoice_ocr import parse_invoice_pdf
                    parsed = parse_invoice_pdf(
                        primary_pdf, api_key=api_key, model=model,
                    )
                    update["ai_extracted_payload"]["incoming_invoice"] = parsed.model_dump()
                except Exception as e:
                    update["ai_error"] = f"OCR: {e}"[:500]
            else:
                update["ai_error"] = "Keine PDF-Anhänge zum OCRen."

        sb.table("incoming_mails").update(update).eq("id", mail_id).execute()
        return sb.table("incoming_mails").select("*").eq("id", mail_id).maybe_single().execute().data
    except Exception as e:
        return _set_failure(mail_id, str(e))


def _set_failure(mail_id: str, msg: str) -> dict[str, Any]:
    supabase().table("incoming_mails").update({
        "status": "failed",
        "ai_error": msg[:500],
    }).eq("id", mail_id).execute()
    return supabase().table("incoming_mails").select("*").eq("id", mail_id).maybe_single().execute().data


# ============================================================
# Stufe 2: Auto-Convert
# ============================================================

# ============================================================
# Validation — Item-Plausibilität gegen Artikelstamm
# ============================================================

PRICE_DEVIATION_WARN_PCT = 20.0  # ab dieser Abweichung in % wird gewarnt


def validate_sales_order_items(so_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Prüft jedes Item: SKU im Artikelstamm? Preis nahe default_price_cents?

    Returns: liste mit warnings, jede {pos_nr, type, msg, ...}
    """
    warnings: list[dict[str, Any]] = []
    sb = supabase()
    for item in so_dict.get("items") or []:
        sku = (item.get("sku") or "").strip()
        pos_nr = item.get("pos_nr")
        if not sku:
            continue
        article = (
            sb.table("articles")
            .select("id, sku, title_de, default_price_cents")
            .ilike("sku", sku).limit(1).execute().data
        )
        if not article:
            warnings.append({
                "pos_nr": pos_nr,
                "type": "no_article_match",
                "msg": f"SKU {sku!r} ist nicht im Artikelstamm — wird als freie Position angelegt.",
            })
            continue
        a = article[0]
        target_eur = float(item.get("target_price_eur") or 0)
        default_eur = (a.get("default_price_cents") or 0) / 100
        if target_eur > 0 and default_eur > 0:
            diff_pct = abs(target_eur - default_eur) / default_eur * 100
            if diff_pct > PRICE_DEVIATION_WARN_PCT:
                warnings.append({
                    "pos_nr": pos_nr,
                    "type": "price_mismatch",
                    "msg": (
                        f"Pos {pos_nr}: Preis {target_eur:.2f} € weicht {diff_pct:.0f}% "
                        f"vom Listenpreis {default_eur:.2f} € ab."
                    ),
                    "target_eur": target_eur,
                    "default_eur": default_eur,
                    "diff_pct": round(diff_pct, 1),
                })
    return warnings


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def auto_convert_if_eligible(mail_id: str, *, actor_email: str | None = None) -> dict[str, Any]:
    """Convertet die Mail in einen Beleg-Draft, wenn die Bedingungen passen.

    Returns: {converted: bool, reason: str, beleg_id?: str, beleg_type?: str}
    """
    cfg = settings()
    if not cfg["auto_convert"]:
        return {"converted": False, "reason": "auto_convert disabled"}

    sb = supabase()
    mail = sb.table("incoming_mails").select("*").eq("id", mail_id).maybe_single().execute().data
    if not mail:
        return {"converted": False, "reason": "mail not found"}
    if mail.get("status") == "linked":
        return {"converted": False, "reason": "already linked"}

    cat = mail.get("ai_category")
    conf = mail.get("ai_confidence") or "low"
    payload = mail.get("ai_extracted_payload") or {}

    min_rank = _CONFIDENCE_RANK.get(cfg["auto_convert_min_confidence"], 2)
    if _CONFIDENCE_RANK.get(conf, 0) < min_rank:
        return {"converted": False, "reason": f"confidence {conf} < min {cfg['auto_convert_min_confidence']}"}

    if cat == "sales_order":
        so = payload.get("sales_order")
        if not so:
            return {"converted": False, "reason": "no sales_order payload"}
        # Auto-Convert nur wenn wir den Kunden domain-sicher matchen können
        from_email = mail.get("from_email") or ""
        if not _can_domain_match_customer(so, from_email):
            return {"converted": False, "reason": "no domain match — manual review"}
        try:
            order_id = mail_to_beleg.convert_mail_to_order(
                mail_id=mail_id,
                sales_order_payload=so,
                mail_from_email=from_email,
                actor_email=actor_email or "auto-pipeline",
            )
            return {"converted": True, "beleg_type": "order", "beleg_id": order_id}
        except Exception as e:
            return {"converted": False, "reason": f"convert error: {e}"[:200]}

    elif cat == "po_acknowledgment":
        # Auto-Verlinkung mit eigener PO via BE-Nr-Erkennung
        try:
            res = mail_to_beleg.link_po_acknowledgment(
                mail_id=mail_id, actor_email=actor_email or "auto-pipeline",
            )
            if res.get("linked"):
                return {"converted": True, "beleg_type": "purchase_order",
                        "beleg_id": res["po_id"], "po_number": res.get("po_number")}
            return {"converted": False, "reason": res.get("reason", "no PO match")}
        except Exception as e:
            return {"converted": False, "reason": f"po-ack error: {e}"[:200]}

    elif cat == "incoming_invoice":
        ii = payload.get("incoming_invoice")
        if not ii:
            return {"converted": False, "reason": "no invoice payload"}
        # Eingangsrechnung: VAT-ID-Match oder Domain → ok für Auto-Convert
        if not (ii.get("supplier_vat_id") or _can_domain_match_supplier(mail.get("from_email") or "")):
            return {"converted": False, "reason": "supplier not safely matchable"}
        atts = mail.get("attachments_meta") or []
        pdf_bytes = None
        pdf_filename = None
        for att in atts:
            if (att.get("content_type") or "").lower() == "application/pdf":
                try:
                    pdf_bytes = sb.storage.from_(imap_inbox.ATTACHMENTS_BUCKET).download(att["storage_path"])
                    pdf_filename = att.get("filename")
                    break
                except Exception:
                    continue
        try:
            inv_id = mail_to_beleg.convert_mail_to_incoming_invoice(
                mail_id=mail_id,
                parsed_invoice=ii,
                pdf_bytes=pdf_bytes,
                pdf_filename=pdf_filename,
                actor_email=actor_email or "auto-pipeline",
            )
            return {"converted": True, "beleg_type": "incoming_invoice", "beleg_id": inv_id}
        except Exception as e:
            return {"converted": False, "reason": f"convert error: {e}"[:200]}

    return {"converted": False, "reason": f"category {cat!r} not auto-convertible"}


def _can_domain_match_customer(so: dict[str, Any], from_email: str) -> bool:
    """Prüft ob wir den Kunden über die Email-Domain eindeutig zuordnen oder neu anlegen können."""
    email = (so.get("customer_email") or from_email or "").lower()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    # Freemail = unsicher; auto-convert nur bei eindeutiger Firmen-Domain
    return domain not in mail_to_beleg._FREEMAIL_DOMAINS


def _can_domain_match_supplier(from_email: str) -> bool:
    if "@" not in (from_email or ""):
        return False
    return from_email.rsplit("@", 1)[1].lower() not in mail_to_beleg._FREEMAIL_DOMAINS


# ============================================================
# Stufe 3: Komplett-Pipeline
# ============================================================

def process_new_mail(mail_id: str, *, actor_email: str | None = None) -> dict[str, Any]:
    """Klassifizieren + (optional) Auto-Convert. Wird von pull_mailbox pro neuer Mail gerufen."""
    cfg = settings()
    result: dict[str, Any] = {"mail_id": mail_id}

    if not cfg["auto_classify"]:
        result["classified"] = False
        result["reason"] = "auto_classify disabled"
        return result

    classified = classify_and_extract(mail_id)
    result["classified"] = classified.get("status") == "ai_classified"
    result["category"] = classified.get("ai_category")
    result["confidence"] = classified.get("ai_confidence")

    if result["classified"] and cfg["auto_convert"]:
        result["auto_convert"] = auto_convert_if_eligible(mail_id, actor_email=actor_email)
    else:
        result["auto_convert"] = {"converted": False, "reason": "skipped"}

    return result
