-- Erweitert replace_order_items + replace_quotation_items um die neuen
-- Per-Item-Felder (expected_delivery_date, delivery_lead_time_text)

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
    expected_delivery_date, delivery_lead_time_text
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
         nullif(elem->>'delivery_lead_time_text','')
    from jsonb_array_elements(p_items) elem;
end $$;

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
      expected_delivery_date, delivery_lead_time_text
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
      nullif(it->>'delivery_lead_time_text','')
    );
  end loop;
end $$;

revoke all on function replace_order_items(uuid, jsonb)     from public;
revoke all on function replace_quotation_items(uuid, jsonb) from public;
grant execute on function replace_order_items(uuid, jsonb)     to anon, authenticated, service_role;
grant execute on function replace_quotation_items(uuid, jsonb) to anon, authenticated, service_role;
