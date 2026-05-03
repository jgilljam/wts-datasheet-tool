-- ============================================================
--  WTS-Tool — Migration 0004: Streckengeschäft / Direktlieferung
-- ============================================================
--  Bisher hat eine Lieferung genau EINE Partei (`party_id`):
--    - outbound → Empfänger (Kunde)
--    - inbound  → Absender (Lieferant)
--
--  Beim Streckengeschäft (Direktlieferung Lieferant → Endkunde,
--  WTS koordiniert nur) brauchen wir BEIDE Parteien gleichzeitig:
--    - party_id        = Empfänger (Kunde)
--    - source_party_id = Absender  (Lieferant) — neu
--
--  Beim Standardversand bleibt source_party_id NULL.
-- ============================================================

alter table deliveries
  add column if not exists source_party_id uuid
    references parties(id) on delete set null;

create index if not exists deliveries_source_party_idx
  on deliveries(source_party_id);

-- Konsistenz-Check (kein Constraint, nur Doku):
--   Wenn source_party_id gesetzt ist, sollte die Lieferung als
--   shipping_method='direktlieferung' gekennzeichnet sein. Das wird
--   App-seitig im Anlege-Form gesetzt; nicht als DB-Constraint, damit
--   Korrektur-Edits jederzeit möglich bleiben.
