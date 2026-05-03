-- ============================================================
-- 0015_items_lock_trigger.sql — GoBD P1: Items-Lock-Trigger
-- ============================================================
-- Schließt die Lücke, dass die GoBD-Lock-Trigger (Migration 0007)
-- nur auf invoices/orders/purchase_orders/deliveries sitzen, NICHT
-- aber auf den jeweiligen *_items-Tabellen.
--
-- Aktueller Schutz: Items-Mutation nur via replace_*_items()-RPC,
-- die `locked_at` des Parent-Belegs prüft. Lücke: ein direktes
-- `UPDATE invoice_items SET unit_price_cents = 1 WHERE …` mit
-- Service-Role umgeht die RPC.
--
-- Dieser Patch ergänzt before-update/delete/insert-Trigger auf den
-- Items-Tabellen, die den Parent-Beleg auf `locked_at IS NOT NULL`
-- prüfen und bei Sperre abbrechen.
--
-- Quotation_items hat KEIN Lock-Pendant (Angebote sind nicht GoBD-
-- streng), aber Konsistenz: gleiche Trigger-Logik mit Status-Check.
--
-- Idempotent (drop trigger if exists + create or replace).
-- ============================================================


-- ============================================================
-- 1. Generischer Helper — fail-closed bei locked Parent
-- ============================================================

create or replace function _gobd_check_parent_locked(
  p_table  text,
  p_id_col text,
  p_id     uuid
) returns void
language plpgsql
as $$
declare
  v_locked timestamptz;
begin
  if p_id is null then return; end if;
  execute format(
    'select locked_at from %I where id = $1',
    p_table
  ) using p_id into v_locked;
  if v_locked is not null then
    raise exception 'GoBD: Beleg %.% gesperrt — Items nicht mutierbar', p_table, p_id
      using errcode = '42501';
  end if;
end $$;


-- ============================================================
-- 2. invoice_items
-- ============================================================
create or replace function _gobd_lock_invoice_items()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    perform _gobd_check_parent_locked('invoices', 'invoice_id', NEW.invoice_id);
    return NEW;
  elsif TG_OP = 'UPDATE' then
    -- Beide IDs prüfen, falls jemand die FK selbst umrouten will
    perform _gobd_check_parent_locked('invoices', 'invoice_id', OLD.invoice_id);
    perform _gobd_check_parent_locked('invoices', 'invoice_id', NEW.invoice_id);
    return NEW;
  elsif TG_OP = 'DELETE' then
    perform _gobd_check_parent_locked('invoices', 'invoice_id', OLD.invoice_id);
    return OLD;
  end if;
  return null;
end $$;

drop trigger if exists trg_invoice_items_gobd_lock on invoice_items;
create trigger trg_invoice_items_gobd_lock
  before insert or update or delete on invoice_items
  for each row execute function _gobd_lock_invoice_items();


-- ============================================================
-- 3. order_items
-- ============================================================
create or replace function _gobd_lock_order_items()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    perform _gobd_check_parent_locked('orders', 'order_id', NEW.order_id);
    return NEW;
  elsif TG_OP = 'UPDATE' then
    perform _gobd_check_parent_locked('orders', 'order_id', OLD.order_id);
    perform _gobd_check_parent_locked('orders', 'order_id', NEW.order_id);
    return NEW;
  elsif TG_OP = 'DELETE' then
    perform _gobd_check_parent_locked('orders', 'order_id', OLD.order_id);
    return OLD;
  end if;
  return null;
end $$;

drop trigger if exists trg_order_items_gobd_lock on order_items;
create trigger trg_order_items_gobd_lock
  before insert or update or delete on order_items
  for each row execute function _gobd_lock_order_items();


-- ============================================================
-- 4. po_items
-- ============================================================
create or replace function _gobd_lock_po_items()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    perform _gobd_check_parent_locked('purchase_orders', 'po_id', NEW.po_id);
    return NEW;
  elsif TG_OP = 'UPDATE' then
    perform _gobd_check_parent_locked('purchase_orders', 'po_id', OLD.po_id);
    perform _gobd_check_parent_locked('purchase_orders', 'po_id', NEW.po_id);
    return NEW;
  elsif TG_OP = 'DELETE' then
    perform _gobd_check_parent_locked('purchase_orders', 'po_id', OLD.po_id);
    return OLD;
  end if;
  return null;
end $$;

drop trigger if exists trg_po_items_gobd_lock on po_items;
create trigger trg_po_items_gobd_lock
  before insert or update or delete on po_items
  for each row execute function _gobd_lock_po_items();


-- ============================================================
-- 5. delivery_items
-- ============================================================
create or replace function _gobd_lock_delivery_items()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    perform _gobd_check_parent_locked('deliveries', 'delivery_id', NEW.delivery_id);
    return NEW;
  elsif TG_OP = 'UPDATE' then
    perform _gobd_check_parent_locked('deliveries', 'delivery_id', OLD.delivery_id);
    perform _gobd_check_parent_locked('deliveries', 'delivery_id', NEW.delivery_id);
    return NEW;
  elsif TG_OP = 'DELETE' then
    perform _gobd_check_parent_locked('deliveries', 'delivery_id', OLD.delivery_id);
    return OLD;
  end if;
  return null;
end $$;

drop trigger if exists trg_delivery_items_gobd_lock on delivery_items;
create trigger trg_delivery_items_gobd_lock
  before insert or update or delete on delivery_items
  for each row execute function _gobd_lock_delivery_items();


-- ============================================================
-- 6. quotation_items — Status-basiert (kein locked_at)
--    Sobald Angebot 'sent'/'accepted'/'expired'/'converted'/'rejected':
--    keine Item-Mutation mehr.
-- ============================================================
create or replace function _gobd_lock_quotation_items()
returns trigger language plpgsql as $$
declare
  v_status text;
  v_qid uuid;
begin
  v_qid := case TG_OP when 'DELETE' then OLD.quotation_id else NEW.quotation_id end;
  if v_qid is null then
    if TG_OP = 'DELETE' then return OLD; else return NEW; end if;
  end if;
  select status into v_status from quotations where id = v_qid;
  if v_status in ('sent','accepted','rejected','expired','converted') then
    raise exception 'GoBD: Angebot % im Status % — Items nicht mehr mutierbar', v_qid, v_status
      using errcode = '42501';
  end if;
  if TG_OP = 'DELETE' then return OLD; else return NEW; end if;
end $$;

drop trigger if exists trg_quotation_items_gobd_lock on quotation_items;
create trigger trg_quotation_items_gobd_lock
  before insert or update or delete on quotation_items
  for each row execute function _gobd_lock_quotation_items();


-- ============================================================
-- Note: Die replace_*_items()-RPCs (security definer) sind die
-- offiziellen Mutations-Pfade und ihre Lock-Checks bleiben aktiv
-- (verhindern z.B. Insert auf gelocktem Parent). Dieser Trigger
-- ist die Defense-in-Depth gegen direkte UPDATE/DELETE/INSERT auf
-- den Items-Tabellen, die die RPC-Schicht umgehen.
-- ============================================================
