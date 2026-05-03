-- ============================================================
-- 0014b — replace_*_items RPCs konsolidiert
-- ============================================================
-- Hotfix: Migration 0014 hatte replace_order_items überschrieben ohne
-- die in 0012b ergänzten Felder (expected_delivery_date, delivery_lead_time_text).
-- Dieser Patch zieht alle replace_*_items-RPCs auf einen konsistenten Stand mit:
--   - allen produktiv genutzten Item-Feldern
--   - article_title_snapshot + article_sku_snapshot (GoBD P3)
--
-- Idempotent.
-- ============================================================

-- replace_invoice_items
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
    tax_amount_cents, line_total_cents, source_order_item_id,
    article_title_snapshot, article_sku_snapshot
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
         nullif(elem->>'source_order_item_id','')::uuid,
         elem->>'article_title_snapshot',
         elem->>'article_sku_snapshot'
    from jsonb_array_elements(p_items) as elem;
end $$;


-- replace_order_items (mit expected_delivery_date + delivery_lead_time_text aus 0012b)
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
    tax_amount_cents, line_total_cents,
    expected_delivery_date, delivery_lead_time_text,
    article_title_snapshot, article_sku_snapshot
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
         coalesce((elem->>'line_total_cents')::int, 0),
         nullif(elem->>'expected_delivery_date','')::date,
         nullif(elem->>'delivery_lead_time_text',''),
         elem->>'article_title_snapshot',
         elem->>'article_sku_snapshot'
    from jsonb_array_elements(p_items) elem;
end $$;


-- replace_po_items
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
    tax_amount_cents, line_total_cents, is_dropship,
    article_title_snapshot, article_sku_snapshot
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
         coalesce((elem->>'is_dropship')::boolean, false),
         elem->>'article_title_snapshot',
         elem->>'article_sku_snapshot'
    from jsonb_array_elements(p_items) as elem;
end $$;


-- replace_quotation_items (mit expected_delivery_date + delivery_lead_time_text aus 0012b)
create or replace function replace_quotation_items(
  p_quotation_id uuid,
  p_items jsonb
) returns void
language plpgsql
security definer
as $$
declare
  it jsonb;
begin
  delete from quotation_items where quotation_id = p_quotation_id;
  for it in select * from jsonb_array_elements(p_items)
  loop
    insert into quotation_items (
      quotation_id, pos_nr, article_id, description_override,
      qty, unit, unit_price_cents, line_total_cents,
      tax_rate, tax_amount_cents, discount_pct,
      expected_delivery_date, delivery_lead_time_text,
      article_title_snapshot, article_sku_snapshot
    ) values (
      p_quotation_id,
      (it->>'pos_nr')::int,
      nullif(it->>'article_id','')::uuid,
      nullif(it->>'description_override',''),
      (it->>'qty')::numeric,
      coalesce(it->>'unit', 'Stk'),
      nullif(it->>'unit_price_cents','')::bigint,
      nullif(it->>'line_total_cents','')::bigint,
      coalesce((it->>'tax_rate')::numeric, 19),
      nullif(it->>'tax_amount_cents','')::bigint,
      coalesce((it->>'discount_pct')::numeric, 0),
      nullif(it->>'expected_delivery_date','')::date,
      nullif(it->>'delivery_lead_time_text',''),
      it->>'article_title_snapshot',
      it->>'article_sku_snapshot'
    );
  end loop;
end $$;


-- replace_delivery_items (delivery_items hat eigene Spalten — kein qty/tax_rate, dafür qty_expected/qty_actual)
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
    qty_expected, qty_actual, unit, batch_lot, mhd,
    article_title_snapshot, article_sku_snapshot
  )
  select p_delivery_id,
         (elem->>'pos_nr')::int,
         nullif(elem->>'article_id','')::uuid,
         elem->>'description_override',
         coalesce((elem->>'qty_expected')::numeric, 0),
         nullif(elem->>'qty_actual','')::numeric,
         coalesce(elem->>'unit','Stk'),
         elem->>'batch_lot',
         nullif(elem->>'mhd','')::date,
         elem->>'article_title_snapshot',
         elem->>'article_sku_snapshot'
    from jsonb_array_elements(p_items) as elem;
end $$;


-- Permissions
revoke all on function replace_invoice_items(uuid,jsonb)   from public;
revoke all on function replace_order_items(uuid,jsonb)     from public;
revoke all on function replace_po_items(uuid,jsonb)        from public;
revoke all on function replace_quotation_items(uuid,jsonb) from public;
revoke all on function replace_delivery_items(uuid,jsonb)  from public;

grant execute on function replace_invoice_items(uuid,jsonb)   to anon, authenticated, service_role;
grant execute on function replace_order_items(uuid,jsonb)     to anon, authenticated, service_role;
grant execute on function replace_po_items(uuid,jsonb)        to anon, authenticated, service_role;
grant execute on function replace_quotation_items(uuid,jsonb) to anon, authenticated, service_role;
grant execute on function replace_delivery_items(uuid,jsonb)  to anon, authenticated, service_role;
