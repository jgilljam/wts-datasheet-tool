-- ============================================================
-- 0005_orders_full.sql — Phase I: Aufträge + Bestellungen vollausbauen
-- ============================================================
-- Erweitert orders + purchase_orders um Steuer/Rabatt/Currency/Adressen/
-- Incoterms/Zahlungsziel/locked_at, ergänzt order_items + po_items
-- um USt + Rabatt pro Position. Legt Audit-Tabellen + Document-Tabellen
-- analog zu deliveries an. Ergänzt parties.is_reverse_charge_eligible.
-- ============================================================

-- 1. parties: Reverse-Charge-Flag (EU-B2B → 0% USt)
alter table parties
  add column if not exists is_reverse_charge_eligible boolean not null default false;

-- 2. orders erweitern
alter table orders
  add column if not exists currency             text default 'EUR',
  add column if not exists tax_total_cents      bigint,
  add column if not exists discount_total_cents bigint,
  add column if not exists incoterms            text,
  add column if not exists incoterms_place      text,
  add column if not exists payment_terms_days   int,
  add column if not exists shipping_address_id  uuid references addresses(id) on delete set null,
  add column if not exists billing_address_id   uuid references addresses(id) on delete set null,
  add column if not exists internal_notes       text,
  add column if not exists locked_at            timestamptz;

-- 3. order_items erweitern
alter table order_items
  add column if not exists tax_rate          numeric default 19,    -- Prozent
  add column if not exists tax_amount_cents  bigint,
  add column if not exists discount_pct      numeric default 0,
  add column if not exists is_dropship       boolean not null default false;

-- 4. purchase_orders erweitern
alter table purchase_orders
  add column if not exists currency             text default 'EUR',
  add column if not exists tax_total_cents      bigint,
  add column if not exists discount_total_cents bigint,
  add column if not exists incoterms            text,
  add column if not exists incoterms_place      text,
  add column if not exists payment_terms_days   int,
  add column if not exists shipping_address_id  uuid references addresses(id) on delete set null,
  add column if not exists billing_address_id   uuid references addresses(id) on delete set null,
  add column if not exists internal_notes       text,
  add column if not exists locked_at            timestamptz,
  -- PO entstand aus SO (z.B. bei Drop-Ship): Smart-Button-Verlinkung
  add column if not exists source_order_id      uuid references orders(id) on delete set null,
  -- Bestätigung durch Lieferant: AB-Datum + bestätigter Termin
  add column if not exists confirmed_at         date,
  add column if not exists confirmed_due_date   date;

-- 5. po_items erweitern
alter table po_items
  add column if not exists tax_rate          numeric default 19,
  add column if not exists tax_amount_cents  bigint,
  add column if not exists discount_pct      numeric default 0,
  add column if not exists is_dropship       boolean not null default false;

-- 6. Audit-Tabellen
create table if not exists order_events (
  id            bigserial primary key,
  order_id      uuid not null references orders(id) on delete cascade,
  at            timestamptz not null default now(),
  actor_label   text,
  event_type    text not null,
  payload       jsonb not null default '{}'::jsonb
);
create index if not exists order_events_order_idx on order_events(order_id);
create index if not exists order_events_at_idx    on order_events(at desc);

create table if not exists po_events (
  id            bigserial primary key,
  po_id         uuid not null references purchase_orders(id) on delete cascade,
  at            timestamptz not null default now(),
  actor_label   text,
  event_type    text not null,
  payload       jsonb not null default '{}'::jsonb
);
create index if not exists po_events_po_idx on po_events(po_id);
create index if not exists po_events_at_idx on po_events(at desc);

-- 7. Document-Tabellen (Storage-Buckets: 'order-docs', 'po-docs' separat anlegen!)
create table if not exists order_documents (
  id            uuid primary key default gen_random_uuid(),
  order_id      uuid not null references orders(id) on delete cascade,
  kind          text not null,
  -- 'order_confirmation' | 'invoice' | 'proforma' | 'customer_po' | 'attachment' | 'other'
  filename      text not null,
  storage_path  text not null,
  content_type  text,
  size_bytes    bigint,
  notes         text,
  uploaded_at   timestamptz not null default now(),
  uploaded_by   text
);
create index if not exists order_documents_order_idx on order_documents(order_id);

create table if not exists po_documents (
  id            uuid primary key default gen_random_uuid(),
  po_id         uuid not null references purchase_orders(id) on delete cascade,
  kind          text not null,
  -- 'po_pdf' | 'supplier_confirmation' | 'invoice' | 'proforma' | 'attachment' | 'other'
  filename      text not null,
  storage_path  text not null,
  content_type  text,
  size_bytes    bigint,
  notes         text,
  uploaded_at   timestamptz not null default now(),
  uploaded_by   text
);
create index if not exists po_documents_po_idx on po_documents(po_id);

-- 8. Done
-- Trigger orders_updated_at + purchase_orders_updated_at sind in schema.sql schon definiert.
