-- Erweitert next_belegnummer um den Quotation-Präfix "AN"
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
  insert into belegnummer_counter(belegart, jahr, last_n)
  values (p_belegart, p_jahr, 0)
  on conflict (belegart, jahr) do nothing;

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
    when 'quotation'         then 'AN'
    else upper(p_belegart)
  end;

  return format('%s-%s-%s', v_prefix, p_jahr, lpad(v_n::text, 4, '0'));
end $$;
