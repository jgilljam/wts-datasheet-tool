-- ============================================================
-- 0008_quotations.sql — Angebote (Quotations)
-- ============================================================
-- Eigene Tabelle für Angebote — vor dem Auftrag im Verkaufsprozess.
-- Eigener Nummernkreis "AN-YYYY-NNNN" via belegnummer_counter.
-- Wenn Kunde annimmt → convert_to_order erzeugt eine `orders`-Zeile
-- und setzt status='converted' + converted_to_order_id.
-- KEIN GoBD-Lock, da Angebote nicht buchhalterisch relevant sind.
-- ============================================================

create table if not exists quotations (
  id                    uuid primary key default gen_random_uuid(),
  quotation_number      text unique not null,          -- "AN-2026-0001"
  customer_id           uuid not null references parties(id) on delete restrict,
  status                text not null default 'draft'
    check (status in ('draft','sent','accepted','rejected','expired','converted','cancelled')),
  quoted_at             date,                          -- Angebotsdatum
  valid_until           date,                          -- Gültig bis (typ. +30 Tage)
  customer_reference    text,                          -- Kunden-Anfrage-Nr
  currency              text default 'EUR',
  total_net_cents       bigint,
  tax_total_cents       bigint,
  discount_total_cents  bigint,
  incoterms             text,
  incoterms_place       text,
  payment_terms_days    int,
  shipping_address_id   uuid references addresses(id) on delete set null,
  billing_address_id    uuid references addresses(id) on delete set null,
  notes                 text,
  internal_notes        text,
  -- Conversion-Path: Angebot → Auftrag
  converted_to_order_id uuid references orders(id) on delete set null,
  converted_at          timestamptz,
  -- Ablehnung
  rejected_reason       text,
  rejected_at           timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists quotations_customer_idx on quotations(customer_id);
create index if not exists quotations_status_idx   on quotations(status);
create index if not exists quotations_quoted_at_idx on quotations(quoted_at desc);

create table if not exists quotation_items (
  id                   uuid primary key default gen_random_uuid(),
  quotation_id         uuid not null references quotations(id) on delete cascade,
  pos_nr               int not null,
  article_id           uuid references articles(id) on delete restrict,
  description_override text,
  qty                  numeric not null,
  unit                 text not null,
  unit_price_cents     bigint,
  line_total_cents     bigint,
  tax_rate             numeric default 19,
  tax_amount_cents     bigint,
  discount_pct         numeric default 0,
  unique (quotation_id, pos_nr)
);

create index if not exists quotation_items_quotation_idx on quotation_items(quotation_id);
create index if not exists quotation_items_article_idx   on quotation_items(article_id);

-- Audit-Events
create table if not exists quotation_events (
  id            bigserial primary key,
  quotation_id  uuid not null references quotations(id) on delete cascade,
  at            timestamptz not null default now(),
  actor_label   text,
  event_type    text not null,
  payload       jsonb not null default '{}'::jsonb
);
create index if not exists quotation_events_quotation_idx on quotation_events(quotation_id);
create index if not exists quotation_events_at_idx        on quotation_events(at desc);

-- ============================================================
-- RPC: replace_quotation_items — atomarer Delete+Insert
-- ============================================================
create or replace function replace_quotation_items(
  p_quotation_id uuid,
  p_items jsonb
) returns void
language plpgsql
security definer
as $$
declare
  it jsonb;
begin
  delete from quotation_items where quotation_id = p_quotation_id;
  for it in select * from jsonb_array_elements(p_items)
  loop
    insert into quotation_items (
      quotation_id, pos_nr, article_id, description_override,
      qty, unit, unit_price_cents, line_total_cents,
      tax_rate, tax_amount_cents, discount_pct
    ) values (
      p_quotation_id,
      (it->>'pos_nr')::int,
      nullif(it->>'article_id','')::uuid,
      nullif(it->>'description_override',''),
      (it->>'qty')::numeric,
      coalesce(it->>'unit', 'Stk'),
      nullif(it->>'unit_price_cents','')::bigint,
      nullif(it->>'line_total_cents','')::bigint,
      coalesce((it->>'tax_rate')::numeric, 19),
      nullif(it->>'tax_amount_cents','')::bigint,
      coalesce((it->>'discount_pct')::numeric, 0)
    );
  end loop;
end $$;

revoke all on function replace_quotation_items(uuid, jsonb) from public;
grant execute on function replace_quotation_items(uuid, jsonb) to anon, authenticated, service_role;

-- ============================================================
-- updated_at-Trigger
-- ============================================================
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;

drop trigger if exists quotations_updated_at on quotations;
create trigger quotations_updated_at before update on quotations
  for each row execute function set_updated_at();
