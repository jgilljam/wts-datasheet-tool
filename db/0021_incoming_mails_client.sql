-- ============================================================
-- 0021_incoming_mails_client.sql — Mail-Client-Erweiterungen
-- ============================================================
--   read_status:    'unread' / 'read' (orthogonal zum Pipeline-Status)
--   in_reply_to:    Message-ID der Original-Mail (für Threading)
--   references_ids: [Message-ID, …] kompletter Thread-Pfad (RFC2822 References)
--   thread_id:      gemeinsame ID für alle Mails desselben Threads
--   starred:        Markierung „wichtig"
--
--   Idempotent.
-- ============================================================

alter table incoming_mails
  add column if not exists read_status text not null default 'unread'
    check (read_status in ('unread', 'read')),
  add column if not exists in_reply_to text,
  add column if not exists references_ids text[],
  add column if not exists thread_id text,
  add column if not exists starred boolean not null default false;

create index if not exists incoming_mails_read_idx     on incoming_mails(read_status);
create index if not exists incoming_mails_thread_idx   on incoming_mails(thread_id);
create index if not exists incoming_mails_in_reply_idx on incoming_mails(in_reply_to);
create index if not exists incoming_mails_starred_idx  on incoming_mails(starred) where starred = true;

-- mailbox-Check erweitern um 'info'
-- (war bisher offen — wir setzen den Check explizit)
do $$
begin
  if not exists (
    select 1 from information_schema.check_constraints
    where constraint_name = 'incoming_mails_mailbox_check'
  ) then
    alter table incoming_mails
      add constraint incoming_mails_mailbox_check
      check (mailbox in ('sales', 'invoice', 'info', 'other'));
  end if;
end $$;
