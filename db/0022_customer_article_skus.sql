-- ============================================================
-- 0022_customer_article_skus.sql — Mapping fremder SKUs auf eigene Artikel
-- ============================================================
--   Kunden + Lieferanten haben oft EIGENE Artikelcodes für unsere Artikel.
--   Beispiel: Eberspächer bestellt EVIF24TSX → unser Artikel "Wilspec WS-200".
--   Dieses Mapping spart manuelles Matching bei wiederkehrenden Bestellungen.
--
--   Idempotent.
-- ============================================================

create table if not exists party_article_skus (
  id              uuid primary key default gen_random_uuid(),
  party_id        uuid not null references parties(id) on delete cascade,
  external_sku    text not null,                  -- der Code, den die Partei verwendet
  article_id      uuid not null references articles(id) on delete cascade,
  -- Optional: zusätzliche Bezeichnung wie sie der Kunde verwendet
  external_description text,
  -- Wann zuletzt gesehen — hilft beim Aufräumen alter Mappings
  last_seen_at    timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  unique (party_id, external_sku)
);

create index if not exists party_article_skus_party_idx   on party_article_skus(party_id);
create index if not exists party_article_skus_article_idx on party_article_skus(article_id);
create index if not exists party_article_skus_external_idx on party_article_skus(external_sku);
