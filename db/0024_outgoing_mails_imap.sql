-- ============================================================
-- 0024_outgoing_mails_imap.sql — IMAP-Sent-Pull-Support
-- ============================================================
--   Erweitert `outgoing_mails` um Spalten für IMAP-gepullte
--   versendete Mails (Quelle: info@ Sent-Folder).
--
--   Hintergrund: Bisher wurden hier nur tool-versendete Mails
--   (Resend) protokolliert. Mit der info@-Umstellung ziehen wir
--   zusätzlich den Sent-Folder per IMAP, um auch manuell aus
--   dem Mail-Client versendete Rechnungen / Bestellungen zu
--   erfassen — sonst keine vollständige Vorgangs-Übersicht.
--
--   Diskriminator: `source` ('tool' | 'imap_pull').
--   IMAP-gepullte Mails sind initial unklassifiziert
--   (beleg_type nullable), Pipeline klassifiziert + verknüpft
--   später.
--
--   Idempotent.
-- ============================================================

-- ============================================================
-- Neue Spalten
-- ============================================================
alter table outgoing_mails
  add column if not exists source       text not null default 'tool',
  add column if not exists message_id   text,
  add column if not exists imap_uid     integer,
  add column if not exists imap_folder  text,
  add column if not exists mailbox      text,
  add column if not exists body_text    text,
  add column if not exists date_sent_hdr timestamptz,
  add column if not exists in_reply_to  text,
  add column if not exists references_ids text[],
  add column if not exists thread_id    text,
  -- Verknüpfung zu Beleg (polymorph, wie incoming_mails)
  add column if not exists linked_beleg_type text,
  add column if not exists linked_beleg_id   uuid,
  add column if not exists linked_at         timestamptz,
  add column if not exists linked_by         text,
  -- KI-Klassifikation für IMAP-Pulls
  add column if not exists ai_category       text,
  add column if not exists ai_confidence     text,
  add column if not exists ai_model          text,
  add column if not exists ai_processed_at   timestamptz,
  add column if not exists ai_extracted_payload jsonb,
  add column if not exists ai_error          text;

-- ============================================================
-- source-Check
-- ============================================================
alter table outgoing_mails
  drop constraint if exists outgoing_mails_source_check;
alter table outgoing_mails
  add constraint outgoing_mails_source_check
  check (source in ('tool', 'imap_pull'));

-- ============================================================
-- ai_category-Check (outgoing-spezifische Kategorien)
-- ============================================================
alter table outgoing_mails
  drop constraint if exists outgoing_mails_ai_category_check;
alter table outgoing_mails
  add constraint outgoing_mails_ai_category_check
  check (ai_category is null or ai_category in (
    'outgoing_invoice',         -- Rechnung an Kunde
    'outgoing_quotation',       -- Angebot an Kunde
    'outgoing_purchase_order',  -- Bestellung an Lieferant
    'outgoing_delivery',        -- Lieferschein
    'outgoing_dunning',         -- Mahnung
    'outgoing_reply',
    'outgoing_other'
  ));

-- ============================================================
-- Status-Check erweitern um IMAP-Pull-Workflow
-- ============================================================
alter table outgoing_mails
  drop constraint if exists outgoing_mails_status_check;
alter table outgoing_mails
  add constraint outgoing_mails_status_check
  check (status in (
    -- Tool-Versand (Resend)
    'queued', 'sending', 'sent', 'delivered',
    'bounced', 'complained', 'failed', 'cancelled',
    -- IMAP-Pull
    'imap_received', 'imap_ai_processing', 'imap_classified',
    'imap_linked', 'imap_ignored', 'imap_failed'
  ));

-- ============================================================
-- beleg_type / beleg_id nullable für IMAP-Pulls
-- ============================================================
alter table outgoing_mails alter column beleg_type drop not null;

alter table outgoing_mails
  drop constraint if exists outgoing_mails_beleg_type_check;
alter table outgoing_mails
  add constraint outgoing_mails_beleg_type_check
  check (beleg_type is null or beleg_type in (
    'invoice', 'quotation', 'order', 'po', 'delivery', 'dunning', 'other'
  ));

-- ============================================================
-- Idempotenz: message_id unique (nur wenn vorhanden)
-- ============================================================
create unique index if not exists outgoing_mails_message_id_uq
  on outgoing_mails(message_id)
  where message_id is not null;

-- ============================================================
-- Indices für Pull/Übersicht
-- ============================================================
create index if not exists outgoing_mails_source_idx
  on outgoing_mails(source);

create index if not exists outgoing_mails_mailbox_idx
  on outgoing_mails(mailbox)
  where mailbox is not null;

create index if not exists outgoing_mails_thread_idx
  on outgoing_mails(thread_id)
  where thread_id is not null;

create index if not exists outgoing_mails_linked_idx
  on outgoing_mails(linked_beleg_type, linked_beleg_id)
  where linked_beleg_id is not null;

create index if not exists outgoing_mails_date_sent_hdr_idx
  on outgoing_mails(date_sent_hdr desc)
  where date_sent_hdr is not null;

create index if not exists outgoing_mails_ai_category_idx
  on outgoing_mails(ai_category)
  where ai_category is not null;
