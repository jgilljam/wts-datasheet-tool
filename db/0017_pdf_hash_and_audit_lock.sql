-- ============================================================
-- 0017_pdf_hash_and_audit_lock.sql — GoBD P10
-- ============================================================
--   (a) PDF-Hash auf allen Beleg-Tabellen (SHA-256 in hex).
--       Wird beim persist_after_lock berechnet und gespeichert.
--       Dient als Manipulationsindikator: re-Render derselben
--       Logik-/Daten-Kombination produziert byte-stable PDF →
--       gleicher Hash. Abweichung = Manipulation oder Drift.
--
--   (b) Append-Only-Trigger auf allen *_events-Tabellen. GoBD
--       verlangt ein unveränderbares Audit-Log. Ohne diesen
--       Trigger könnte ein DB-Admin Events nachträglich löschen
--       oder verändern.
--
-- Idempotent.
-- ============================================================


-- ============================================================
-- 1. pdf_hash_sha256 — eine Spalte pro Beleg-Tabelle
--    Format: 64 hex chars (lowercase). NULL solange noch kein
--    PDF persistiert wurde.
-- ============================================================

alter table invoices         add column if not exists pdf_hash_sha256 text;
alter table quotations       add column if not exists pdf_hash_sha256 text;
alter table orders           add column if not exists pdf_hash_sha256 text;
alter table purchase_orders  add column if not exists pdf_hash_sha256 text;
alter table deliveries       add column if not exists pdf_hash_sha256 text;
alter table invoice_dunnings add column if not exists pdf_hash_sha256 text;


-- ============================================================
-- 2. GoBD-Lock-Whitelists erweitern: pdf_hash_sha256 darf
--    NACH Lock befüllt werden (wird gleichzeitig mit
--    pdf_storage_path geschrieben, nach erfolgreichem Upload).
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
      'pdf_storage_path', 'pdf_hash_sha256'
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
    array['status', 'updated_at', 'cancellation_reason',
          'pdf_storage_path', 'pdf_hash_sha256']
  );
  return NEW;
end $$;

create or replace function _gobd_lock_po()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'cancellation_reason',
          'pdf_storage_path', 'pdf_hash_sha256']
  );
  return NEW;
end $$;

create or replace function _gobd_lock_deliveries()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'arrived_at', 'shipped_at',
          'pdf_storage_path', 'pdf_hash_sha256']
  );
  return NEW;
end $$;


-- ============================================================
-- 3. Append-Only-Trigger auf *_events
-- ============================================================

create or replace function _gobd_events_append_only()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    return NEW;
  end if;
  raise exception 'GoBD: % auf %.* nicht erlaubt — Audit-Log ist append-only',
    TG_OP, TG_TABLE_NAME using errcode = '42501';
end $$;

-- Auf alle 6 events-Tabellen anwenden
do $$
declare
  t text;
begin
  foreach t in array array[
    'invoice_events', 'order_events', 'po_events',
    'quotation_events', 'delivery_events', 'incoming_invoice_events'
  ] loop
    execute format('drop trigger if exists trg_%I_append_only on %I', t, t);
    execute format(
      'create trigger trg_%I_append_only ' ||
      'before update or delete on %I ' ||
      'for each row execute function _gobd_events_append_only()',
      t, t
    );
  end loop;
end $$;
