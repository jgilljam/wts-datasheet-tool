-- ============================================================
-- 0012_per_item_delivery_and_quote_options.sql
-- ============================================================
-- 1) Liefertermin pro Position (AB): order_items + quotation_items
-- 2) Angebots-Render-Optionen: hide_totals
-- ============================================================

alter table order_items
  add column if not exists expected_delivery_date date,
  add column if not exists delivery_lead_time_text text;
  -- Beispiel: '6-8 Wochen', 'sofort lagernd', 'Termin lt. AB Lieferant'

alter table quotation_items
  add column if not exists expected_delivery_date date,
  add column if not exists delivery_lead_time_text text;

-- Quote-Render-Optionen: ob das PDF die Totals zeigt oder nicht.
-- (Nur Quotations — bei AB/Rechnung sind Totals immer Pflicht)
alter table quotations
  add column if not exists hide_totals_in_pdf boolean not null default false;
