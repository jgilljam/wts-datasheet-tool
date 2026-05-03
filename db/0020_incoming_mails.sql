-- ============================================================
-- 0020_incoming_mails.sql — Mail-Posteingang (sales@ + invoice@)
-- ============================================================
--   Zentrale Inbox für vom Tool gepullte Mails (IMAP).
--
--   Quellen: sales@wts-trading.de, invoice@wts-trading.de
--   info@ wird NICHT gepullt (Newsletter-Noise vermeiden).
--
--   Lifecycle:
--     received → ai_processing → ai_classified → linked → archived
--                                              ↓
--                                            ignored
--
--   Idempotent.
-- ============================================================

create table if not exists incoming_mails (
  id                  uuid primary key default gen_random_uuid(),
  -- IMAP-Identifikation (Duplikate vermeiden)
  message_id          text unique,                -- Mail-Header Message-ID, RFC2822
  imap_uid            bigint,                     -- IMAP UID auf dem Server
  imap_folder         text default 'INBOX',
  mailbox             text not null,              -- 'sales' / 'invoice' / 'info'
  -- Header
  from_email          text not null,
  from_name           text,
  to_email            text not null,              -- volle An-Adresse (z.B. sales@wts-trading.de)
  cc_emails           text[],
  reply_to            text,
  subject             text,
  date_sent           timestamptz,                -- Date-Header der Mail
  -- Body
  body_text           text,
  body_html           text,
  -- Anhänge (Storage-Pfade nach Supabase Storage)
  attachments_meta    jsonb default '[]'::jsonb,  -- [{filename, content_type, size_bytes, storage_path, sha256}]
  -- Roh-Eml (komplette Mail) optional als Backup
  raw_eml_storage_path text,
  -- Status-Lifecycle
  status              text not null default 'received'
    check (status in (
      'received',         -- frisch gepullt
      'ai_processing',    -- Gemini läuft
      'ai_classified',    -- KI hat klassifiziert + extrahiert
      'linked',           -- mit Beleg verknüpft (order/po/incoming_invoice)
      'ignored',          -- vom User als irrelevant markiert
      'failed',           -- AI-Fehler oder anderes Problem
      'archived'          -- abgehakt
    )),
  -- KI-Klassifikation
  ai_category         text                        -- 'sales_order' / 'po_acknowledgment' / 'incoming_invoice' / 'other'
    check (ai_category is null or ai_category in (
      'sales_order', 'po_acknowledgment', 'incoming_invoice', 'reply', 'other'
    )),
  ai_confidence       text,                       -- 'high' / 'medium' / 'low'
  ai_extracted_payload jsonb,                     -- Gemini-Output: strukturierte Bestellung/Rechnung
  ai_model            text,                       -- z.B. 'gemini-2.5-flash-lite'
  ai_error            text,                       -- bei status=failed
  ai_processed_at     timestamptz,
  -- Verknüpfung zu Beleg (polymorph)
  linked_beleg_type   text                        -- 'order' / 'purchase_order' / 'incoming_invoice'
    check (linked_beleg_type is null or linked_beleg_type in (
      'order', 'purchase_order', 'incoming_invoice'
    )),
  linked_beleg_id     uuid,
  linked_at           timestamptz,
  linked_by           text,                       -- user_email
  -- Notizen
  internal_notes      text,
  -- Audit
  received_at         timestamptz not null default now(),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index if not exists incoming_mails_mailbox_idx     on incoming_mails(mailbox);
create index if not exists incoming_mails_status_idx      on incoming_mails(status);
create index if not exists incoming_mails_received_idx    on incoming_mails(received_at desc);
create index if not exists incoming_mails_from_idx        on incoming_mails(from_email);
create index if not exists incoming_mails_category_idx    on incoming_mails(ai_category);
create index if not exists incoming_mails_linked_idx      on incoming_mails(linked_beleg_type, linked_beleg_id);

-- updated_at-Trigger
drop trigger if exists incoming_mails_updated_at on incoming_mails;
create trigger incoming_mails_updated_at before update on incoming_mails
  for each row execute function set_updated_at();

-- DELETE blocken — Posteingang-Audit
create or replace function _incoming_mails_block_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'incoming_mails: DELETE nicht erlaubt — Mail-Audit ist permanent. Status auf ''ignored'' oder ''archived'' setzen.'
    using errcode = '42501';
end $$;

drop trigger if exists trg_incoming_mails_block_delete on incoming_mails;
create trigger trg_incoming_mails_block_delete
  before delete on incoming_mails
  for each row execute function _incoming_mails_block_delete();
