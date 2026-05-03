-- ============================================================
-- 0010_incoming_invoices.sql — Eingangsrechnungen (von Lieferanten)
-- ============================================================
-- Separater Belegtyp: Lieferanten-Rechnungen, die WTS bekommt.
-- Optional verknüpft mit eigener PO (BE-...). PDF wird im Storage
-- abgelegt, OCR-Payload als jsonb gespeichert (für Audit).
-- KEIN GoBD-Lock auf eigene Felder — wir verwalten nur was wir bekommen.
-- ============================================================

create table if not exists incoming_invoices (
  id                    uuid primary key default gen_random_uuid(),
  -- Lieferant (FK auf parties)
  supplier_id           uuid not null references parties(id) on delete restrict,
  -- Lieferanten-Rechnungsnummer (vom Lieferanten vergeben)
  supplier_invoice_number text not null,
  invoice_date          date,
  due_date              date,
  service_date          date,
  -- Beträge
  currency              text default 'EUR',
  total_net_cents       bigint,
  tax_total_cents       bigint,
  gross_total_cents     bigint,
  -- Status-Lifecycle
  status                text not null default 'received'
    check (status in ('received','in_review','approved','disputed','paid','cancelled')),
  paid_at               timestamptz,
  paid_amount_cents     bigint default 0,
  -- Verknüpfung zu eigener PO
  related_po_id         uuid references purchase_orders(id) on delete set null,
  -- Original-PDF
  pdf_storage_path      text,
  pdf_filename          text,
  -- OCR-Output für Audit (kompletter Gemini-Response)
  ocr_payload           jsonb default '{}'::jsonb,
  ocr_confidence        text,                    -- 'high' / 'medium' / 'low'
  -- Freitext
  supplier_reference    text,                    -- z.B. unsere PO-Nr beim Lieferanten
  customer_reference    text,                    -- selten — eigene Referenz
  notes                 text,
  internal_notes        text,
  -- Audit
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (supplier_id, supplier_invoice_number)  -- Doppel-Erfassung verhindern
);

create index if not exists incoming_invoices_supplier_idx  on incoming_invoices(supplier_id);
create index if not exists incoming_invoices_status_idx    on incoming_invoices(status);
create index if not exists incoming_invoices_due_date_idx  on incoming_invoices(due_date);
create index if not exists incoming_invoices_po_idx        on incoming_invoices(related_po_id);

-- Items
create table if not exists incoming_invoice_items (
  id                    uuid primary key default gen_random_uuid(),
  incoming_invoice_id   uuid not null references incoming_invoices(id) on delete cascade,
  pos_nr                int not null,
  -- Vom Lieferanten gelieferte Daten (OCR'd)
  sku                   text,
  description           text,
  qty                   numeric,
  unit                  text default 'Stk',
  unit_price_cents      bigint,
  line_total_cents      bigint,
  tax_rate              numeric default 19,
  tax_amount_cents      bigint,
  discount_pct          numeric default 0,
  -- Match auf eigene Artikel (optional)
  matched_article_id    uuid references articles(id) on delete set null,
  match_confidence      text,                    -- 'exact_sku' / 'fuzzy_name' / 'manual' / null
  unique (incoming_invoice_id, pos_nr)
);

create index if not exists incoming_invoice_items_inv_idx     on incoming_invoice_items(incoming_invoice_id);
create index if not exists incoming_invoice_items_article_idx on incoming_invoice_items(matched_article_id);

-- Audit-Events
create table if not exists incoming_invoice_events (
  id                  bigserial primary key,
  incoming_invoice_id uuid not null references incoming_invoices(id) on delete cascade,
  at                  timestamptz not null default now(),
  actor_label         text,
  event_type          text not null,
  payload             jsonb not null default '{}'::jsonb
);
create index if not exists incoming_invoice_events_inv_idx on incoming_invoice_events(incoming_invoice_id);
create index if not exists incoming_invoice_events_at_idx  on incoming_invoice_events(at desc);

-- updated_at-Trigger
drop trigger if exists incoming_invoices_updated_at on incoming_invoices;
create trigger incoming_invoices_updated_at before update on incoming_invoices
  for each row execute function set_updated_at();

-- Atomarer Items-Replace (analog zu replace_invoice_items)
create or replace function replace_incoming_invoice_items(
  p_invoice_id uuid,
  p_items jsonb
) returns void
language plpgsql
security definer
as $$
declare
  it jsonb;
begin
  delete from incoming_invoice_items where incoming_invoice_id = p_invoice_id;
  for it in select * from jsonb_array_elements(p_items)
  loop
    insert into incoming_invoice_items (
      incoming_invoice_id, pos_nr, sku, description,
      qty, unit, unit_price_cents, line_total_cents,
      tax_rate, tax_amount_cents, discount_pct,
      matched_article_id, match_confidence
    ) values (
      p_invoice_id,
      (it->>'pos_nr')::int,
      nullif(it->>'sku',''),
      nullif(it->>'description',''),
      nullif(it->>'qty','')::numeric,
      coalesce(it->>'unit', 'Stk'),
      nullif(it->>'unit_price_cents','')::bigint,
      nullif(it->>'line_total_cents','')::bigint,
      coalesce((it->>'tax_rate')::numeric, 19),
      nullif(it->>'tax_amount_cents','')::bigint,
      coalesce((it->>'discount_pct')::numeric, 0),
      nullif(it->>'matched_article_id','')::uuid,
      nullif(it->>'match_confidence','')
    );
  end loop;
end $$;

revoke all on function replace_incoming_invoice_items(uuid, jsonb) from public;
grant execute on function replace_incoming_invoice_items(uuid, jsonb) to anon, authenticated, service_role;
