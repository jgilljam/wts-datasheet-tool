-- ============================================================
--  WTS-Tool — Migration 0002: Lagerbestand (Single-Lager-Modell)
-- ============================================================
--
--  Deploy:
--    1. Supabase-Dashboard → SQL Editor → New query
--    2. Inhalt dieser Datei einfügen → Run
--
--  Modell:
--    - stock_movements: append-only Bewegungen mit Delta (+/- qty)
--    - stock_balances: View, summiert Bewegungen pro Artikel
--    - Single-Lager (kein location-Feld) — Multi-Lager kommt
--      bei Bedarf in einer Folge-Migration (article × location)
--
--  Status-Wechsel an Lieferungen verbuchen NICHT automatisch in
--  diese Tabelle — das ist Phase C (separater service-Hook).
--  Bis dahin werden alle Bewegungen manuell gebucht.
-- ============================================================


create table stock_movements (
  id              uuid primary key default gen_random_uuid(),
  article_id      uuid not null references articles(id) on delete restrict,

  -- + Eingang (Wareneingang, Korrektur nach oben, Stornierung Versand)
  -- - Ausgang (Versand, Schwund, Korrektur nach unten)
  qty_delta       numeric not null check (qty_delta <> 0),

  -- Werte:
  --   'inbound'    Wareneingang (zukünftig: aus Lieferung verlinkt)
  --   'outbound'   Warenausgang (zukünftig: aus Lieferung verlinkt)
  --   'adjustment' manuelle Korrektur / Inventur / Schwund
  --   'transfer'   Reserviert für späteres Multi-Lager
  movement_type   text not null check (movement_type in (
    'inbound', 'outbound', 'adjustment', 'transfer'
  )),

  -- Soft-Verknüpfung zur Lieferung (NULL bei manuellen Buchungen)
  delivery_id     uuid references deliveries(id) on delete set null,

  -- Charge / MHD optional als Audit-Info (kein eigener Pro-Charge-Bestand)
  batch_lot       text,
  mhd             date,

  -- Audit
  at              timestamptz not null default now(),
  actor           uuid,                              -- später → auth.users.id
  actor_label     text,                              -- für jetzt: User-Name als Text
  note            text
);

create index stock_movements_article_idx  on stock_movements(article_id);
create index stock_movements_at_idx       on stock_movements(at);
create index stock_movements_delivery_idx on stock_movements(delivery_id);
create index stock_movements_type_idx     on stock_movements(movement_type);


-- ============================================================
-- View: aktueller Saldo pro Artikel
-- ============================================================
-- qty_on_hand     Summe aller Bewegungen
-- below_min       true, wenn min_stock_qty gesetzt UND Bestand <= min
-- last_movement_at  Datum der letzten Bewegung (NULL wenn nie bewegt)
-- ============================================================

create or replace view stock_balances as
  select
    a.id                                  as article_id,
    a.sku,
    a.manufacturer_sku,
    a.title_de,
    a.unit,
    a.min_stock_qty,
    a.is_active,
    a.is_pfand,
    a.adr_un_nr,
    a.adr_class,
    coalesce(sum(m.qty_delta), 0)         as qty_on_hand,
    case
      when a.min_stock_qty is not null
       and coalesce(sum(m.qty_delta), 0) <= a.min_stock_qty
        then true
      else false
    end                                   as below_min,
    max(m.at)                             as last_movement_at
  from articles a
  left join stock_movements m on m.article_id = a.id
  group by
    a.id, a.sku, a.manufacturer_sku, a.title_de, a.unit,
    a.min_stock_qty, a.is_active, a.is_pfand, a.adr_un_nr, a.adr_class;


-- ============================================================
-- RLS — vorbereiten, noch nicht aktivieren (siehe schema.sql)
-- ============================================================
-- alter table stock_movements enable row level security;
-- create policy "internal_users_all"
--   on stock_movements for all to authenticated using (true);
