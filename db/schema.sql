-- ============================================================
--  WTS-Tool — Initial Schema
--  Postgres / Supabase
-- ============================================================
--
--  Deploy:
--    1. Supabase-Dashboard → SQL Editor → New query
--    2. Inhalt dieser Datei einfügen → Run
--    3. Storage-Bucket 'delivery-docs' anlegen (siehe Ende)
--
--  Phase 0 (jetzt aktiv):  parties, addresses, contacts, articles,
--                          deliveries, delivery_items,
--                          delivery_documents, delivery_events
--
--  Phase 1 (vorbereitet):  orders, order_items,
--                          purchase_orders, po_items
--                          — DDL liegt schon hier, damit FKs in
--                          deliveries.related_order_id /
--                          deliveries.related_po_id sofort
--                          referenzierbar sind.
-- ============================================================


create extension if not exists "pgcrypto";


-- ============================================================
-- 1. Stammdaten: Parties (Kunden + Lieferanten in einer Tabelle)
-- ============================================================

create table parties (
  id              uuid primary key default gen_random_uuid(),
  type            text not null check (type in ('customer','supplier','both')),
  legal_name      text not null,
  short_name      text,
  vat_id          text,                      -- USt-IdNr (z.B. DE123456789)
  tax_number      text,                      -- nationale Steuer-Nr
  eori            text,                      -- für Zoll bei Drittland-Geschäften
  payment_terms_days int,                    -- Zahlungsziel in Tagen
  default_currency   text default 'EUR',
  notes           text,
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index parties_type_idx   on parties(type);
create index parties_active_idx on parties(is_active);


create table addresses (
  id              uuid primary key default gen_random_uuid(),
  party_id        uuid not null references parties(id) on delete cascade,
  kind            text not null check (kind in ('billing','shipping','pickup','registered')),
  label           text,                      -- "Hauptlager", "Empfang", "Filiale 2"
  street          text not null,
  street_2        text,
  zip             text,
  city            text not null,
  country_code    text not null default 'DE' check (length(country_code) = 2),
  contact_name    text,
  contact_phone   text,
  is_default      boolean not null default false,
  notes           text,
  created_at      timestamptz not null default now()
);

create index addresses_party_idx on addresses(party_id);


create table contacts (
  id              uuid primary key default gen_random_uuid(),
  party_id        uuid not null references parties(id) on delete cascade,
  name            text not null,
  email           text,
  phone           text,
  mobile          text,
  role            text,                      -- Einkauf, Disposition, Buchhaltung, ...
  is_primary      boolean not null default false,
  notes           text,
  created_at      timestamptz not null default now()
);

create index contacts_party_idx on contacts(party_id);


-- ============================================================
-- 2. Katalog: Articles (Komponenten)
-- ============================================================

create table articles (
  id                 uuid primary key default gen_random_uuid(),
  sku                text unique not null,        -- WTS-interne Artikel-Nr
  manufacturer_sku   text,                        -- Hersteller-Artikel-Nr
  ean                text,
  title_de           text not null,
  title_en           text,
  short_desc_de      text,
  short_desc_en      text,
  category           text,
  unit               text not null default 'Stk',

  -- Soft-FK auf datasheet-library (slug = Dateiname ohne .json)
  datasheet_slug     text,

  -- Default-Lieferant (Auto-Vorschlag bei Bestellung)
  default_supplier_id uuid references parties(id) on delete set null,

  -- Lager (Phase 0): zwei physische Lager am gleichen Standort
  default_location   text check (default_location in ('keller','garage')),
  min_stock_qty      numeric,                     -- Mindestbestand-Alert (Phase 3)

  -- Pfand (Kältemittelflaschen)
  is_pfand           boolean not null default false,
  pfand_per_unit_cents bigint,

  -- Gefahrgut (ADR — Kältemittel sind nahezu alle Klasse 2)
  adr_un_nr          text,                        -- "UN 3252" (R32), "UN 1978" (R290), ...
  adr_class          text,                        -- "2.1" (entzündbares Gas), "2.2" (verdichtet), "2.3" (toxisch)
  adr_packing_group  text,                        -- "I", "II", "III" (oft N/A bei Klasse 2)
  adr_net_kg_per_unit numeric,                    -- Gewicht der Gefahrgut-Substanz pro Einheit (1000-Punkte-Regel)
  adr_proper_name    text,                        -- offizielle ADR-Benennung

  -- Kommerziell
  default_price_cents bigint,                     -- Listen-VK in Cent

  is_active          boolean not null default true,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index articles_sku_idx       on articles(sku);
create index articles_supplier_idx  on articles(default_supplier_id);
create index articles_active_idx    on articles(is_active);
create index articles_category_idx  on articles(category);


-- ============================================================
-- 3. Phase 1 vorbereitet: Aufträge & Bestellungen
-- ============================================================
-- Tabellen werden in Phase 1 aktiv genutzt; jetzt schon angelegt,
-- damit deliveries.related_order_id / .related_po_id sofort
-- referenzierbar sind.

create table orders (
  id               uuid primary key default gen_random_uuid(),
  order_number     text unique not null,           -- "AB-2026-0042"
  customer_id      uuid not null references parties(id) on delete restrict,
  status           text not null default 'draft'
    check (status in ('draft','confirmed','in_production','partial','shipped','done','cancelled')),
  ordered_at       date,
  due_date         date,
  customer_reference text,                          -- Kunden-Bestell-/Projekt-Nr
  total_net_cents  bigint,
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index orders_customer_idx on orders(customer_id);
create index orders_status_idx   on orders(status);


create table order_items (
  id               uuid primary key default gen_random_uuid(),
  order_id         uuid not null references orders(id) on delete cascade,
  pos_nr           int not null,
  article_id       uuid references articles(id) on delete restrict,
  description_override text,
  qty              numeric not null,
  unit             text not null,
  unit_price_cents bigint,
  line_total_cents bigint,
  unique (order_id, pos_nr)
);

create index order_items_order_idx   on order_items(order_id);
create index order_items_article_idx on order_items(article_id);


create table purchase_orders (
  id                 uuid primary key default gen_random_uuid(),
  po_number          text unique not null,         -- "BE-2026-0007"
  supplier_id        uuid not null references parties(id) on delete restrict,
  status             text not null default 'draft'
    check (status in ('draft','sent','confirmed','in_production','shipped','partial','received','cancelled')),
  ordered_at         date,
  expected_at        date,
  supplier_reference text,                          -- AB-Nr beim Lieferanten
  total_net_cents    bigint,
  notes              text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index po_supplier_idx on purchase_orders(supplier_id);
create index po_status_idx   on purchase_orders(status);


create table po_items (
  id               uuid primary key default gen_random_uuid(),
  po_id            uuid not null references purchase_orders(id) on delete cascade,
  pos_nr           int not null,
  article_id       uuid references articles(id) on delete restrict,
  description_override text,
  qty              numeric not null,
  unit             text not null,
  unit_price_cents bigint,
  line_total_cents bigint,
  unique (po_id, pos_nr)
);

create index po_items_po_idx      on po_items(po_id);
create index po_items_article_idx on po_items(article_id);


-- ============================================================
-- 4. Lieferungen (das MVP — kombiniert eingehend + ausgehend)
-- ============================================================

create table deliveries (
  id                  uuid primary key default gen_random_uuid(),

  -- Identifikation
  direction           text not null check (direction in ('inbound','outbound')),
  delivery_number     text unique not null,        -- "L-2026-0123" / "WE-2026-0042"

  -- Parteien
  -- inbound  → party_id = Absender (Lieferant)
  -- outbound → party_id = Empfänger (Kunde)
  party_id            uuid references parties(id) on delete restrict,
  shipping_address_id uuid references addresses(id) on delete set null,
  -- Abweichende Lieferadresse (z.B. Streckengeschäft, Baustelle)
  billing_address_id  uuid references addresses(id) on delete set null,
  -- Abweichende Rechnungsadresse — bei Streckengeschäft sehr häufig

  -- Verknüpfungen (nullable: Sendungen ohne Auftrag möglich, z.B. Muster, Reklamation)
  related_order_id    uuid references orders(id) on delete set null,
  related_po_id       uuid references purchase_orders(id) on delete set null,
  customer_reference  text,                         -- Kommissions-/Projekt-Nr des Kunden

  -- Status
  -- Wertebereich (App-seitig enforced, DB lässt offen für Flexibilität):
  --   inbound:  announced, ordered, confirmed, in_production, shipped, in_transit,
  --             arrived, partial_received, received, inspected, stored, complaint, cancelled
  --   outbound: draft, picking, packed, ready_for_pickup, handed_to_carrier, in_transit,
  --             delivered, returned, cancelled
  status              text not null default 'draft',

  -- Termine
  termin_type         text check (termin_type in ('fix','ca','kw','asap')),
  -- 'fix' = harter Termin · 'ca' = ungefähr · 'kw' = Kalenderwoche · 'asap' = sofort
  is_partial          boolean not null default false,
  ordered_at          date,                         -- Bestelldatum (kopiert vom related_po/order)
  expected_at         timestamptz,                  -- Wunsch-/Erwartungstermin
  shipped_at          timestamptz,
  arrived_at          timestamptz,

  -- Logistik
  shipping_method     text check (shipping_method in (
    'paket','stueckgut','spedition','kurier','abholung','direktlieferung'
  )),
  carrier             text,                         -- "DHL", "DPD", "Spedition Müller", ...
  tracking_number     text,
  tracking_url        text,                         -- vorgeneriert oder manuell
  packages_count      int,
  weight_gross_kg     numeric,
  weight_net_kg       numeric,
  volume_m3           numeric,
  pallet_type         text check (pallet_type in ('euro','einweg','gitterbox','none','other')),
  pallet_count        int,

  -- Kommerziell / Konditionen
  incoterms           text,                         -- "EXW", "FCA", "DAP", "DDP", ...
  incoterms_place     text,                         -- z.B. "Frankfurt"
  frankatur           text,                         -- "frei Haus", "ab Werk", ...

  -- Avisierung (häufig bei Speditionsanlieferung Pflicht)
  avisierung_required boolean not null default false,
  avisierung_phone    text,
  avisierung_done_at  timestamptz,

  -- Zoll (Drittland-Geschäfte: CH, UK, TR, ...)
  customs_required    boolean not null default false,
  customs_data        jsonb,
  -- Format-Vorschlag: {"hs_code": "8418.69.00", "origin": "CH",
  --                    "preference": "EUR.1", "value_eur": 1234.56, "eori": "..."}

  -- Gefahrgut (ADR) — denormalisiert: true wenn mind. ein item ADR ist
  adr_required        boolean not null default false,
  adr_summary         jsonb,
  -- Format-Vorschlag: aggregierte UN-Mengen für Beförderungspapier
  --   [{"un_nr":"UN 3252","class":"2.1","net_kg":12.5,"packages":3}, ...]

  -- Sonstiges
  notes               text,                         -- für Beleg sichtbar
  internal_notes      text,                         -- nur intern

  -- Audit
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  created_by          uuid,                         -- später → auth.users.id
  updated_by          uuid,

  -- GoBD-Sperre: nach Finalisieren nicht mehr änderbar
  locked_at           timestamptz,
  locked_by           uuid
);

create index deliveries_direction_idx on deliveries(direction);
create index deliveries_status_idx    on deliveries(status);
create index deliveries_party_idx     on deliveries(party_id);
create index deliveries_expected_idx  on deliveries(expected_at);
create index deliveries_order_idx     on deliveries(related_order_id);
create index deliveries_po_idx        on deliveries(related_po_id);
create index deliveries_locked_idx    on deliveries(locked_at);


-- ============================================================
-- 5. Lieferpositionen
-- ============================================================

create table delivery_items (
  id                  uuid primary key default gen_random_uuid(),
  delivery_id         uuid not null references deliveries(id) on delete cascade,
  pos_nr              int not null,

  -- Artikel (nullable: Freitext-Position erlaubt — z.B. unbekanntes Ersatzteil)
  article_id          uuid references articles(id) on delete set null,
  description_override text,                        -- bei article_id null oder Abweichung

  -- Mengen
  qty_expected        numeric,                      -- bestellt / avisiert
  qty_actual          numeric,                      -- tatsächlich angekommen / verschickt
  unit                text,                         -- 'Stk', 'kg', 'm', 'l', ...

  -- Preis (nur bei outbound mit Faktura-Bezug)
  unit_price_cents    bigint,
  line_total_cents    bigint,

  -- Charge / Serie / MHD
  batch_lot           text,                         -- Chargen-Nr (Pflicht bei Kältemittel/Öl)
  serials             text[],                       -- Serien-Nrn (Verdichter, Verflüssiger, ...)
  mhd                 date,                         -- Mindesthaltbarkeit (Öle, Dichtmittel)

  -- Lager (Phase 0: keller / garage)
  storage_location    text check (storage_location in ('keller','garage')),

  -- Pfand (Kältemittelflaschen — werden mit/zurückversandt)
  pfand_qty           numeric,
  pfand_unit_cents    bigint,

  -- Gefahrgut-Snapshot (kopiert vom article zum Versandzeitpunkt)
  adr_un_nr           text,
  adr_class           text,
  adr_packing_group   text,
  adr_net_kg          numeric,                      -- = adr_net_kg_per_unit × qty

  notes               text,

  unique (delivery_id, pos_nr)
);

create index delivery_items_delivery_idx on delivery_items(delivery_id);
create index delivery_items_article_idx  on delivery_items(article_id);


-- ============================================================
-- 6. Anhänge (PDFs, Fotos)
-- ============================================================

create table delivery_documents (
  id            uuid primary key default gen_random_uuid(),
  delivery_id   uuid not null references deliveries(id) on delete cascade,
  kind          text not null check (kind in (
    'delivery_note','invoice','order_confirmation','customs_declaration',
    'adr_paper','photo','damage_photo','signature','other'
  )),
  filename      text not null,
  storage_path  text not null,                      -- Supabase Storage key (Bucket: delivery-docs)
  content_type  text,
  size_bytes    bigint,
  uploaded_at   timestamptz not null default now(),
  uploaded_by   uuid,
  notes         text
);

create index delivery_documents_delivery_idx on delivery_documents(delivery_id);


-- ============================================================
-- 7. Audit-Log (append-only, GoBD-Pflicht)
-- ============================================================

create table delivery_events (
  id            bigserial primary key,
  delivery_id   uuid not null references deliveries(id) on delete cascade,
  at            timestamptz not null default now(),
  actor         uuid,                               -- später → auth.users.id
  actor_label   text,                               -- für jetzt: User-Email/Name als Text
  event_type    text not null,
  -- Werte: 'created', 'status_change', 'item_added', 'item_received',
  --        'document_uploaded', 'note_added', 'tracking_updated', 'locked', ...
  payload       jsonb
  -- Beispiele:
  --   {"old_status":"in_transit","new_status":"received","comment":"vollständig, Sicht ok"}
  --   {"document_kind":"delivery_note","filename":"LS-4711.pdf"}
);

create index delivery_events_delivery_idx on delivery_events(delivery_id);
create index delivery_events_at_idx       on delivery_events(at);


-- ============================================================
-- Helper: updated_at-Auto-Trigger
-- ============================================================

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger parties_updated_at
  before update on parties for each row execute function set_updated_at();
create trigger articles_updated_at
  before update on articles for each row execute function set_updated_at();
create trigger orders_updated_at
  before update on orders for each row execute function set_updated_at();
create trigger purchase_orders_updated_at
  before update on purchase_orders for each row execute function set_updated_at();
create trigger deliveries_updated_at
  before update on deliveries for each row execute function set_updated_at();


-- ============================================================
-- RLS — vorbereiten, noch nicht aktivieren
-- ============================================================
-- Aktuell nutzt die App den service_role-Key (umgeht RLS).
-- Sobald st.login() + Google OIDC + Supabase-Auth-Mapping existiert
-- (Task #5), Row-Level-Security pro Tabelle aktivieren:
--
--   alter table deliveries enable row level security;
--   create policy "internal_users_all"
--     on deliveries for all
--     to authenticated
--     using (true);
--
-- Granular pro Rolle (admin / mitarbeiter) folgt in Phase 2.


-- ============================================================
-- Storage Bucket — separat anlegen
-- ============================================================
-- Im Supabase-Dashboard → Storage → New Bucket:
--   Name:            delivery-docs
--   Public:          false
--   File size limit: 25 MB
--
-- Oder per SQL (nach Schema-Deploy):
--   insert into storage.buckets (id, name, public)
--     values ('delivery-docs', 'delivery-docs', false);
