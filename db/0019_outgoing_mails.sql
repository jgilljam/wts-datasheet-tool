-- ============================================================
-- 0019_outgoing_mails.sql — Mail-Versand-Audit
-- ============================================================
--   Persistiert jede vom Tool gesendete Mail (Rechnung/Angebot/
--   Lieferschein/Mahnung/Auftragsbestätigung/Bestellung).
--
--   Polymorph: beleg_type + beleg_id verweisen auf den Beleg,
--   ohne harte FK (weil 6 verschiedene Tabellen).
--
--   Status-Updates kommen via Resend-Webhook (delivered/bounced/
--   complained). DELETE ist geblockt — Mail-Versand ist endgültig.
--
--   Idempotent.
-- ============================================================

create table if not exists outgoing_mails (
  id                  uuid primary key default gen_random_uuid(),
  beleg_type          text not null
    check (beleg_type in (
      'invoice', 'quotation', 'order', 'po', 'delivery', 'dunning', 'other'
    )),
  beleg_id            uuid,                       -- nullable für 'other'
  beleg_number        text,                       -- z.B. "RE-2026-0001" — Snapshot
  to_email            text not null,
  cc_emails           text[],
  bcc_emails          text[],
  reply_to            text,
  from_email          text not null,              -- z.B. "info@wts-trading.de"
  subject             text not null,
  body_preview        text,                       -- erste 500 chars
  body_html           text,                       -- voller HTML-Body
  attachments_meta    jsonb,                      -- [{filename, storage_path, size_bytes}]
  status              text not null default 'queued'
    check (status in (
      'queued', 'sending', 'sent', 'delivered',
      'bounced', 'complained', 'failed', 'cancelled'
    )),
  resend_message_id   text,                       -- für Webhook-Tracking
  error_message       text,
  sent_at             timestamptz,
  delivered_at        timestamptz,
  sent_by             text,                       -- user_email zum Sendezeitpunkt
  created_at          timestamptz not null default now()
);

create index if not exists outgoing_mails_beleg_idx
  on outgoing_mails(beleg_type, beleg_id);
create index if not exists outgoing_mails_status_idx
  on outgoing_mails(status);
create index if not exists outgoing_mails_created_idx
  on outgoing_mails(created_at desc);
create index if not exists outgoing_mails_resend_id_idx
  on outgoing_mails(resend_message_id);


-- DELETE blocken — Mail-Versand-Audit ist permanent
create or replace function _outgoing_mails_block_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'outgoing_mails: DELETE nicht erlaubt — Mail-Audit ist permanent'
    using errcode = '42501';
end $$;

drop trigger if exists trg_outgoing_mails_block_delete on outgoing_mails;
create trigger trg_outgoing_mails_block_delete
  before delete on outgoing_mails
  for each row execute function _outgoing_mails_block_delete();
