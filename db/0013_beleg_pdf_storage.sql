-- ============================================================
-- 0013 Beleg-PDF-Persistierung (GoBD: byte-stable Wiederherstellung)
-- ============================================================
--   pdf_storage_path: relativer Pfad im Storage-Bucket "belege",
--   z.B. "invoice/2026/RE-2026-0001.pdf"
--
--   Ohne diese Spalte können generierte PDFs nicht zuverlässig
--   wieder geladen werden — bei Templating- oder CSS-Änderungen
--   wäre die Byte-Identität verloren.
-- ============================================================

alter table invoices
  add column if not exists pdf_storage_path text;

alter table quotations
  add column if not exists pdf_storage_path text;

alter table orders
  add column if not exists pdf_storage_path text;

alter table purchase_orders
  add column if not exists pdf_storage_path text;

alter table deliveries
  add column if not exists pdf_storage_path text;

alter table invoice_dunnings
  add column if not exists pdf_storage_path text;


-- ============================================================
-- GoBD-Whitelists erweitern: pdf_storage_path muss nach Lock
-- gesetzt werden können (persist erfolgt direkt nach status='issued',
-- aber locked_at wird gleichzeitig gesetzt → Trigger feuert).
-- ============================================================

create or replace function _gobd_lock_invoices()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array[
      'paid_amount_cents', 'paid_at', 'status', 'updated_at',
      'reversed_by_id', 'reversed_at', 'cancellation_reason',
      'reverses_id',
      'current_dunning_level', 'last_dunning_at',
      'pdf_storage_path'
    ]
  );
  return NEW;
end $$;

create or replace function _gobd_lock_orders()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'cancellation_reason', 'pdf_storage_path']
  );
  return NEW;
end $$;

create or replace function _gobd_lock_po()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'cancellation_reason', 'pdf_storage_path']
  );
  return NEW;
end $$;

create or replace function _gobd_lock_deliveries()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'arrived_at', 'shipped_at', 'pdf_storage_path']
  );
  return NEW;
end $$;


-- ============================================================
-- Storage-Bucket "belege" anlegen (privat, nur service-role).
-- Idempotent.
-- ============================================================

insert into storage.buckets (id, name, public)
values ('belege', 'belege', false)
on conflict (id) do nothing;
