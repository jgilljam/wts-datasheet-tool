-- ============================================================
-- 0009_dunning.sql — OP-Liste & Mahnwesen
-- ============================================================
-- Mahnstufen pro Rechnung tracken. Levels:
--   0 = noch keine Mahnung
--   1 = freundliche Erinnerung
--   2 = 1. Mahnung (mit Mahngebühr)
--   3 = 2. Mahnung (höhere Gebühr, Inkasso-Androhung)
-- ============================================================

alter table invoices
  add column if not exists current_dunning_level int not null default 0,
  add column if not exists last_dunning_at       timestamptz;

create table if not exists invoice_dunnings (
  id                uuid primary key default gen_random_uuid(),
  invoice_id        uuid not null references invoices(id) on delete cascade,
  level             int not null check (level between 1 and 3),
  sent_at           timestamptz not null default now(),
  due_date          date,                              -- neue Zahlfrist
  amount_due_cents  bigint not null,                   -- Hauptforderung zum Mahnzeitpunkt
  fees_cents        bigint not null default 0,         -- Mahngebühren
  interest_cents    bigint not null default 0,         -- Verzugszinsen
  total_cents       bigint generated always as (amount_due_cents + fees_cents + interest_cents) stored,
  notes             text,
  payload           jsonb not null default '{}'::jsonb,
  unique (invoice_id, level)
);

create index if not exists invoice_dunnings_invoice_idx on invoice_dunnings(invoice_id);
create index if not exists invoice_dunnings_sent_idx on invoice_dunnings(sent_at desc);

-- Standard-Gebühren pro Stufe (kann im company_settings ergänzt werden, sonst Default)
alter table company_settings
  add column if not exists dunning_fee_l1_cents int default 0,
  add column if not exists dunning_fee_l2_cents int default 500,
  add column if not exists dunning_fee_l3_cents int default 1500,
  add column if not exists dunning_grace_days   int default 7;
