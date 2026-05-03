-- ============================================================
-- 0007_gobd_hardening.sql
--   Belegnummer-Counter (atomar, lückenlos, GoBD-konform)
--   Atomare replace_items()-RPCs für invoices/orders/po/deliveries
--   Atomarer bump_qty_invoiced() für Race-frei Auftrags-Items
--   GoBD-Lock-Trigger auf invoices/orders/purchase_orders/deliveries
-- Idempotent (CREATE OR REPLACE / IF NOT EXISTS)
-- ============================================================

-- ============================================================
-- 1. Belegnummer-Counter (Counter-Tabelle, KEINE Sequence wegen Rollback-Lücken)
-- ============================================================

create table if not exists belegnummer_counter (
  belegart text not null,                -- 'invoice', 'order', 'po', 'delivery_outbound', 'delivery_inbound'
  jahr     int  not null,
  last_n   int  not null default 0,
  primary key (belegart, jahr)
);

-- Rückwärts-Migration: bestehende Maximal-Nummern pro (Belegart, Jahr) als
-- Startwert übernehmen, damit weiterhin aufsteigend nummeriert wird.
insert into belegnummer_counter (belegart, jahr, last_n)
select 'invoice',
       (regexp_match(invoice_number, '^RE-(\d{4})-(\d{4})$'))[1]::int,
       max((regexp_match(invoice_number, '^RE-(\d{4})-(\d{4})$'))[2]::int)
  from invoices
 where invoice_number ~ '^RE-\d{4}-\d{4}$'
 group by 1, 2
on conflict (belegart, jahr) do update
  set last_n = greatest(belegnummer_counter.last_n, excluded.last_n);

insert into belegnummer_counter (belegart, jahr, last_n)
select 'order',
       (regexp_match(order_number, '^AB-(\d{4})-(\d{4})$'))[1]::int,
       max((regexp_match(order_number, '^AB-(\d{4})-(\d{4})$'))[2]::int)
  from orders
 where order_number ~ '^AB-\d{4}-\d{4}$'
 group by 1, 2
on conflict (belegart, jahr) do update
  set last_n = greatest(belegnummer_counter.last_n, excluded.last_n);

insert into belegnummer_counter (belegart, jahr, last_n)
select 'po',
       (regexp_match(po_number, '^BE-(\d{4})-(\d{4})$'))[1]::int,
       max((regexp_match(po_number, '^BE-(\d{4})-(\d{4})$'))[2]::int)
  from purchase_orders
 where po_number ~ '^BE-\d{4}-\d{4}$'
 group by 1, 2
on conflict (belegart, jahr) do update
  set last_n = greatest(belegnummer_counter.last_n, excluded.last_n);

-- Lieferungen: zwei Belegarten je nach direction
insert into belegnummer_counter (belegart, jahr, last_n)
select 'delivery_outbound',
       (regexp_match(delivery_number, '^L-(\d{4})-(\d{4})$'))[1]::int,
       max((regexp_match(delivery_number, '^L-(\d{4})-(\d{4})$'))[2]::int)
  from deliveries
 where delivery_number ~ '^L-\d{4}-\d{4}$' and direction = 'outbound'
 group by 1, 2
on conflict (belegart, jahr) do update
  set last_n = greatest(belegnummer_counter.last_n, excluded.last_n);

insert into belegnummer_counter (belegart, jahr, last_n)
select 'delivery_inbound',
       (regexp_match(delivery_number, '^WE-(\d{4})-(\d{4})$'))[1]::int,
       max((regexp_match(delivery_number, '^WE-(\d{4})-(\d{4})$'))[2]::int)
  from deliveries
 where delivery_number ~ '^WE-\d{4}-\d{4}$' and direction = 'inbound'
 group by 1, 2
on conflict (belegart, jahr) do update
  set last_n = greatest(belegnummer_counter.last_n, excluded.last_n);


create or replace function next_belegnummer(p_belegart text, p_jahr int)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_n int;
  v_prefix text;
begin
  -- ensure-row anlegen, falls noch keine Combination existiert
  insert into belegnummer_counter(belegart, jahr, last_n)
  values (p_belegart, p_jahr, 0)
  on conflict (belegart, jahr) do nothing;

  -- atomar incrementen + RETURNING; row-lock serialisiert konkurrierende Aufrufer
  update belegnummer_counter
     set last_n = last_n + 1
   where belegart = p_belegart and jahr = p_jahr
   returning last_n into v_n;

  v_prefix := case p_belegart
    when 'invoice'           then 'RE'
    when 'order'             then 'AB'
    when 'po'                then 'BE'
    when 'delivery_outbound' then 'L'
    when 'delivery_inbound'  then 'WE'
    else upper(p_belegart)
  end;

  return format('%s-%s-%s', v_prefix, p_jahr, lpad(v_n::text, 4, '0'));
end $$;


-- ============================================================
-- 2. replace_items() RPCs — atomar (delete + insert in einer Transaction)
-- ============================================================

create or replace function replace_invoice_items(
  p_invoice_id uuid,
  p_items      jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_locked boolean;
begin
  select locked_at is not null into v_locked from invoices where id = p_invoice_id;
  if v_locked then
    raise exception 'GoBD: Rechnung % ist festgeschrieben — keine Item-Mutation', p_invoice_id
      using errcode = '42501';
  end if;

  delete from invoice_items where invoice_id = p_invoice_id;

  insert into invoice_items (
    invoice_id, pos_nr, article_id, description_override,
    qty, unit, unit_price_cents, tax_rate, discount_pct,
    tax_amount_cents, line_total_cents, source_order_item_id
  )
  select p_invoice_id,
         (elem->>'pos_nr')::int,
         nullif(elem->>'article_id','')::uuid,
         elem->>'description_override',
         (elem->>'qty')::numeric,
         coalesce(elem->>'unit','Stk'),
         coalesce((elem->>'unit_price_cents')::int, 0),
         coalesce((elem->>'tax_rate')::numeric, 19),
         coalesce((elem->>'discount_pct')::numeric, 0),
         coalesce((elem->>'tax_amount_cents')::int, 0),
         coalesce((elem->>'line_total_cents')::int, 0),
         nullif(elem->>'source_order_item_id','')::uuid
    from jsonb_array_elements(p_items) as elem;
end $$;


create or replace function replace_order_items(
  p_order_id uuid,
  p_items    jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_locked boolean;
begin
  select locked_at is not null into v_locked from orders where id = p_order_id;
  if v_locked then
    raise exception 'GoBD: Auftrag % ist festgeschrieben — keine Item-Mutation', p_order_id
      using errcode = '42501';
  end if;

  delete from order_items where order_id = p_order_id;

  insert into order_items (
    order_id, pos_nr, article_id, description_override,
    qty, unit, unit_price_cents, tax_rate, discount_pct,
    tax_amount_cents, line_total_cents
  )
  select p_order_id,
         (elem->>'pos_nr')::int,
         nullif(elem->>'article_id','')::uuid,
         elem->>'description_override',
         (elem->>'qty')::numeric,
         coalesce(elem->>'unit','Stk'),
         coalesce((elem->>'unit_price_cents')::int, 0),
         coalesce((elem->>'tax_rate')::numeric, 19),
         coalesce((elem->>'discount_pct')::numeric, 0),
         coalesce((elem->>'tax_amount_cents')::int, 0),
         coalesce((elem->>'line_total_cents')::int, 0)
    from jsonb_array_elements(p_items) as elem;
end $$;


create or replace function replace_po_items(
  p_po_id uuid,
  p_items jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_locked boolean;
begin
  select locked_at is not null into v_locked from purchase_orders where id = p_po_id;
  if v_locked then
    raise exception 'GoBD: Bestellung % ist festgeschrieben — keine Item-Mutation', p_po_id
      using errcode = '42501';
  end if;

  delete from po_items where po_id = p_po_id;

  insert into po_items (
    po_id, pos_nr, article_id, description_override,
    qty, unit, unit_price_cents, tax_rate, discount_pct,
    tax_amount_cents, line_total_cents, is_dropship
  )
  select p_po_id,
         (elem->>'pos_nr')::int,
         nullif(elem->>'article_id','')::uuid,
         elem->>'description_override',
         (elem->>'qty')::numeric,
         coalesce(elem->>'unit','Stk'),
         coalesce((elem->>'unit_price_cents')::int, 0),
         coalesce((elem->>'tax_rate')::numeric, 19),
         coalesce((elem->>'discount_pct')::numeric, 0),
         coalesce((elem->>'tax_amount_cents')::int, 0),
         coalesce((elem->>'line_total_cents')::int, 0),
         coalesce((elem->>'is_dropship')::boolean, false)
    from jsonb_array_elements(p_items) as elem;
end $$;


create or replace function replace_delivery_items(
  p_delivery_id uuid,
  p_items       jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_locked boolean;
begin
  select locked_at is not null into v_locked from deliveries where id = p_delivery_id;
  if v_locked then
    raise exception 'GoBD: Lieferung % ist festgeschrieben — keine Item-Mutation', p_delivery_id
      using errcode = '42501';
  end if;

  delete from delivery_items where delivery_id = p_delivery_id;

  insert into delivery_items (
    delivery_id, pos_nr, article_id, description_override,
    qty_expected, qty_actual, unit, batch_lot, mhd
  )
  select p_delivery_id,
         (elem->>'pos_nr')::int,
         nullif(elem->>'article_id','')::uuid,
         elem->>'description_override',
         coalesce((elem->>'qty_expected')::numeric, 0),
         nullif(elem->>'qty_actual','')::numeric,
         coalesce(elem->>'unit','Stk'),
         elem->>'batch_lot',
         nullif(elem->>'mhd','')::date
    from jsonb_array_elements(p_items) as elem;
end $$;


-- ============================================================
-- 3. bump_qty_invoiced() — atomares Increment ohne Lost-Update
-- ============================================================

create or replace function bump_qty_invoiced(
  p_order_item_id uuid,
  p_delta         numeric
) returns numeric
language plpgsql
security definer
set search_path = public
as $$
declare
  v_new numeric;
begin
  update order_items
     set qty_invoiced = greatest(0, coalesce(qty_invoiced, 0) + p_delta)
   where id = p_order_item_id
   returning qty_invoiced into v_new;
  return v_new;
end $$;


-- ============================================================
-- 4. GoBD-Lock-Trigger
--   Whitelist: nach locked_at sind nur Zahlungs- und Storno-Felder mutierbar.
--   Diff via to_jsonb() — fail-closed, neue Spalten by-default verboten.
-- ============================================================

-- Hilfsfunktion, die je Tabelle eine eigene Whitelist erlaubt
create or replace function _gobd_lock_check(
  p_old jsonb, p_new jsonb, p_allowed text[]
) returns void
language plpgsql
as $$
declare
  changed text[];
  forbidden text[];
begin
  select array_agg(n.key)
    into changed
    from jsonb_each(p_new) n
    join jsonb_each(p_old) o on n.key = o.key
   where n.value is distinct from o.value;

  if changed is null then
    return;
  end if;

  -- Die geänderten Spalten, die NICHT erlaubt sind
  select array(select unnest(changed) except select unnest(p_allowed))
    into forbidden;

  if forbidden is not null and array_length(forbidden, 1) > 0 then
    raise exception 'GoBD: Beleg gesperrt — unzulässige Änderung an %',
      array_to_string(forbidden, ', ')
      using errcode = '42501';
  end if;
end $$;


-- Invoices
create or replace function _gobd_lock_invoices()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array[
      'paid_amount_cents', 'paid_at', 'status', 'updated_at',
      'reversed_by_id', 'reversed_at', 'cancellation_reason',
      'reverses_id'  -- in Storno-Anlage darf gesetzt werden
    ]
  );
  return NEW;
end $$;

drop trigger if exists trg_invoices_gobd_lock on invoices;
create trigger trg_invoices_gobd_lock
  before update on invoices
  for each row execute function _gobd_lock_invoices();

-- Orders
create or replace function _gobd_lock_orders()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'cancellation_reason']
  );
  return NEW;
end $$;

drop trigger if exists trg_orders_gobd_lock on orders;
create trigger trg_orders_gobd_lock
  before update on orders
  for each row execute function _gobd_lock_orders();

-- Purchase Orders
create or replace function _gobd_lock_po()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'cancellation_reason']
  );
  return NEW;
end $$;

drop trigger if exists trg_po_gobd_lock on purchase_orders;
create trigger trg_po_gobd_lock
  before update on purchase_orders
  for each row execute function _gobd_lock_po();

-- Deliveries
create or replace function _gobd_lock_deliveries()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is null then return NEW; end if;
  perform _gobd_lock_check(
    to_jsonb(OLD), to_jsonb(NEW),
    array['status', 'updated_at', 'arrived_at', 'shipped_at']
  );
  return NEW;
end $$;

drop trigger if exists trg_deliveries_gobd_lock on deliveries;
create trigger trg_deliveries_gobd_lock
  before update on deliveries
  for each row execute function _gobd_lock_deliveries();


-- DELETE-Schutz für festgeschriebene Belege auf allen 4 Tabellen
create or replace function _gobd_block_locked_delete()
returns trigger language plpgsql as $$
begin
  if OLD.locked_at is not null then
    raise exception 'GoBD: Beleg gesperrt — DELETE nicht erlaubt'
      using errcode = '42501';
  end if;
  return OLD;
end $$;

drop trigger if exists trg_invoices_gobd_no_delete on invoices;
create trigger trg_invoices_gobd_no_delete
  before delete on invoices
  for each row execute function _gobd_block_locked_delete();

drop trigger if exists trg_orders_gobd_no_delete on orders;
create trigger trg_orders_gobd_no_delete
  before delete on orders
  for each row execute function _gobd_block_locked_delete();

drop trigger if exists trg_po_gobd_no_delete on purchase_orders;
create trigger trg_po_gobd_no_delete
  before delete on purchase_orders
  for each row execute function _gobd_block_locked_delete();

drop trigger if exists trg_deliveries_gobd_no_delete on deliveries;
create trigger trg_deliveries_gobd_no_delete
  before delete on deliveries
  for each row execute function _gobd_block_locked_delete();


-- ============================================================
-- 5. Permissions: RPCs für Service-Role aufrufbar
-- ============================================================

revoke all on function next_belegnummer(text,int)             from public;
revoke all on function replace_invoice_items(uuid,jsonb)      from public;
revoke all on function replace_order_items(uuid,jsonb)        from public;
revoke all on function replace_po_items(uuid,jsonb)           from public;
revoke all on function replace_delivery_items(uuid,jsonb)     from public;
revoke all on function bump_qty_invoiced(uuid,numeric)        from public;

grant execute on function next_belegnummer(text,int)          to service_role, authenticated;
grant execute on function replace_invoice_items(uuid,jsonb)   to service_role, authenticated;
grant execute on function replace_order_items(uuid,jsonb)     to service_role, authenticated;
grant execute on function replace_po_items(uuid,jsonb)        to service_role, authenticated;
grant execute on function replace_delivery_items(uuid,jsonb)  to service_role, authenticated;
grant execute on function bump_qty_invoiced(uuid,numeric)     to service_role, authenticated;
