-- ============================================================
-- 0006_invoices_full.sql — Phase J: Rechnungen + Storno + Company-Settings
-- ============================================================
-- Legt invoices + invoice_items + invoice_events + invoice_documents an.
-- Legt company_settings (single-row für Rechnungskopf-Daten) an.
-- Ergänzt orders + purchase_orders + invoices um Storno-Spalten.
-- Ergänzt order_items um qty_invoiced (Teilrechnungs-Tracking).
-- ============================================================

-- ============================================================
-- 1. company_settings — Single-Row für Rechnungskopf-Daten
-- ============================================================
create table if not exists company_settings (
  id              uuid primary key default gen_random_uuid(),
  -- Single-Row-Constraint via Trigger oder einfach: nur einen Datensatz pflegen
  legal_name      text not null default 'Weber Trading & Service',
  street          text not null default 'Kaiserstraße 35',
  zip             text not null default '41061',
  city            text not null default 'Mönchengladbach',
  country_code    text not null default 'DE',
  email           text default 'info@wts-trading.de',
  phone           text,
  -- Steuerlich
  tax_number      text,                  -- Finanzamt-Steuernummer
  vat_id          text,                  -- USt-IdNr (z.B. DE123456789)
  -- Bank
  bank_name       text,
  iban            text,
  bic             text,
  -- HR/Geschäftsführung (optional, für Rechnungsfooter)
  managing_director text,
  hr_register     text,                  -- z.B. "HRB 12345 Amtsgericht Mönchengladbach"
  -- Audit
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- Default-Datensatz einfügen, falls leer (idempotent)
insert into company_settings (legal_name)
select 'Weber Trading & Service'
where not exists (select 1 from company_settings);

-- ============================================================
-- 2. invoices
-- ============================================================
create table if not exists invoices (
  id                  uuid primary key default gen_random_uuid(),
  invoice_number      text unique,        -- "RE-2026-0001" — vergeben erst bei issued (GoBD!)
  customer_id         uuid not null references parties(id) on delete restrict,

  status              text not null default 'draft'
    check (status in ('draft','issued','partially_paid','paid','overdue','cancelled','reversed')),
  -- 'cancelled' = vor issued storniert (kein Beleg an Kunde gegangen)
  -- 'reversed'  = nach issued storniert; Stornobeleg mit reverses_id existiert

  -- Daten
  issued_at           date,                -- Rechnungsdatum (= Ausstellungsdatum)
  service_date        date,                -- Leistungsdatum (= Lieferdatum) — UStG §14 Pflicht!
  due_date            date,                -- Zahlbar bis
  paid_at             date,                -- Wann tatsächlich beglichen

  -- Beträge
  total_net_cents     bigint,
  tax_total_cents     bigint,
  discount_total_cents bigint,
  paid_amount_cents   bigint default 0,    -- für Teilzahlungen

  -- Konditionen
  currency            text default 'EUR',
  payment_terms_days  int,
  customer_reference  text,                -- Kunden-Bestell-Nr aus dem Auftrag
  purpose_of_payment  text,                -- Verwendungszweck für Banking-App
  incoterms           text,
  incoterms_place     text,

  -- Adressen (Rechnung kann andere Adressen als Lieferung haben!)
  shipping_address_id uuid references addresses(id) on delete set null,
  billing_address_id  uuid references addresses(id) on delete set null,

  -- Verknüpfungen
  related_order_id    uuid references orders(id) on delete set null,

  -- Storno-Verkettung (für reversed/cancellation_invoices)
  reverses_id         uuid references invoices(id) on delete set null,
  reversed_by_id      uuid references invoices(id) on delete set null,
  cancellation_reason text,

  -- Reverse-Charge: auf Beleg-Ebene gecacht (nicht aus Customer ziehen — der könnte sich ändern)
  is_reverse_charge   boolean not null default false,

  -- Texte
  notes               text,                -- sichtbar auf Rechnung
  internal_notes      text,                -- nur intern

  -- Audit
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  locked_at           timestamptz          -- GoBD-Festschreibung
);

create index if not exists invoices_customer_idx     on invoices(customer_id);
create index if not exists invoices_status_idx       on invoices(status);
create index if not exists invoices_issued_at_idx    on invoices(issued_at);
create index if not exists invoices_due_date_idx     on invoices(due_date);
create index if not exists invoices_related_order_idx on invoices(related_order_id);
create index if not exists invoices_reverses_idx     on invoices(reverses_id);

-- ============================================================
-- 3. invoice_items
-- ============================================================
create table if not exists invoice_items (
  id                  uuid primary key default gen_random_uuid(),
  invoice_id          uuid not null references invoices(id) on delete cascade,
  pos_nr              int not null,
  article_id          uuid references articles(id) on delete restrict,
  description_override text,
  qty                 numeric not null,    -- kann negativ sein bei Stornorechnungen
  unit                text not null,
  unit_price_cents    bigint,
  discount_pct        numeric default 0,
  tax_rate            numeric default 19,  -- Prozent
  tax_amount_cents    bigint,
  line_total_cents    bigint,
  -- Verknüpfung zur Quell-Position im Auftrag (für Teilrechnungs-Tracking)
  source_order_item_id uuid references order_items(id) on delete set null,
  unique (invoice_id, pos_nr)
);

create index if not exists invoice_items_invoice_idx  on invoice_items(invoice_id);
create index if not exists invoice_items_article_idx  on invoice_items(article_id);
create index if not exists invoice_items_source_idx   on invoice_items(source_order_item_id);

-- ============================================================
-- 4. invoice_events (Audit)
-- ============================================================
create table if not exists invoice_events (
  id            bigserial primary key,
  invoice_id    uuid not null references invoices(id) on delete cascade,
  at            timestamptz not null default now(),
  actor_label   text,
  event_type    text not null,
  payload       jsonb not null default '{}'::jsonb
);
create index if not exists invoice_events_invoice_idx on invoice_events(invoice_id);
create index if not exists invoice_events_at_idx      on invoice_events(at desc);

-- ============================================================
-- 5. invoice_documents
-- ============================================================
create table if not exists invoice_documents (
  id            uuid primary key default gen_random_uuid(),
  invoice_id    uuid not null references invoices(id) on delete cascade,
  kind          text not null,             -- 'invoice_pdf' | 'cancellation_pdf' | 'reminder' | 'attachment'
  filename      text not null,
  storage_path  text not null,
  content_type  text,
  size_bytes    bigint,
  notes         text,
  uploaded_at   timestamptz not null default now(),
  uploaded_by   text
);
create index if not exists invoice_documents_invoice_idx on invoice_documents(invoice_id);

-- ============================================================
-- 6. order_items: qty_invoiced für Teilrechnungs-Tracking
-- ============================================================
alter table order_items
  add column if not exists qty_invoiced numeric not null default 0;

-- ============================================================
-- 7. orders + purchase_orders: Storno-Spalten
-- ============================================================
-- Aufträge können vor Versand mit `status='cancelled'` einfach storniert werden,
-- aber wenn sie schon abgerechnet sind, brauchen wir Storno-Verkettung
alter table orders
  add column if not exists cancellation_reason text,
  add column if not exists cancelled_at        timestamptz;

alter table purchase_orders
  add column if not exists cancellation_reason text,
  add column if not exists cancelled_at        timestamptz;

-- ============================================================
-- 8. Trigger: invoices updated_at + GoBD-Schutz
-- ============================================================
create trigger invoices_updated_at
  before update on invoices
  for each row execute function set_updated_at();

create trigger company_settings_updated_at
  before update on company_settings
  for each row execute function set_updated_at();
