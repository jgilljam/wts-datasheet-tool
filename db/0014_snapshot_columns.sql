-- ============================================================
-- 0014_snapshot_columns.sql — GoBD P3: Stammdaten-Snapshots
-- ============================================================
-- Schließt die GoBD-Lücke, dass historische Belege ihre Adressen
-- und Kundennamen live aus parties/addresses ziehen. Nach
-- Festschreibung MÜSSEN Belege ihre Stammdaten eingefroren tragen,
-- sonst verstößt eine spätere Adress-/Namens-Änderung gegen die
-- Unveränderbarkeitspflicht.
--
-- Strategie: JSONB-Snapshots auf invoices/quotations/orders/
-- purchase_orders/deliveries. Befüllt im selben UPDATE wie
-- locked_at, daher passieren sie den GoBD-Lock-Trigger
-- (OLD.locked_at IS NULL beim Setzen).
--
-- Items-Snapshot: article_title_snapshot + article_sku_snapshot
-- als TEXT-Spalten direkt auf *_items. description_override wird
-- bei issue() automatisch befüllt, falls leer.
--
-- Backfill: bestehende gelockte Belege bekommen ihre Snapshots
-- aus den heutigen Live-Daten — best-effort-Approximation für
-- Altbelege. Prod-Belege ab heute werden korrekt gefroren.
--
-- Idempotent.
-- ============================================================

-- ============================================================
-- 1. Snapshot-Spalten auf Beleg-Tabellen
-- ============================================================

-- invoices
alter table invoices
  add column if not exists customer_snapshot          jsonb,
  add column if not exists billing_address_snapshot   jsonb,
  add column if not exists shipping_address_snapshot  jsonb,
  add column if not exists company_snapshot           jsonb;

-- quotations
alter table quotations
  add column if not exists customer_snapshot          jsonb,
  add column if not exists billing_address_snapshot   jsonb,
  add column if not exists shipping_address_snapshot  jsonb,
  add column if not exists company_snapshot           jsonb;

-- orders
alter table orders
  add column if not exists customer_snapshot          jsonb,
  add column if not exists billing_address_snapshot   jsonb,
  add column if not exists shipping_address_snapshot  jsonb,
  add column if not exists company_snapshot           jsonb;

-- purchase_orders (Kunde = Lieferant in diesem Kontext)
alter table purchase_orders
  add column if not exists supplier_snapshot          jsonb,
  add column if not exists billing_address_snapshot   jsonb,
  add column if not exists shipping_address_snapshot  jsonb,
  add column if not exists company_snapshot           jsonb;

-- deliveries (party = Empfänger oder Versender, je nach Richtung)
alter table deliveries
  add column if not exists party_snapshot             jsonb,
  add column if not exists source_party_snapshot      jsonb,
  add column if not exists shipping_address_snapshot  jsonb,
  add column if not exists company_snapshot           jsonb;


-- ============================================================
-- 2. Item-Snapshots (Bezeichnung + SKU eingefroren)
-- ============================================================

alter table invoice_items
  add column if not exists article_title_snapshot text,
  add column if not exists article_sku_snapshot   text;

alter table order_items
  add column if not exists article_title_snapshot text,
  add column if not exists article_sku_snapshot   text;

alter table quotation_items
  add column if not exists article_title_snapshot text,
  add column if not exists article_sku_snapshot   text;

alter table po_items
  add column if not exists article_title_snapshot text,
  add column if not exists article_sku_snapshot   text;

alter table delivery_items
  add column if not exists article_title_snapshot text,
  add column if not exists article_sku_snapshot   text;


-- ============================================================
-- 3. Helper-Funktionen: Snapshot bauen
-- ============================================================

create or replace function build_party_snapshot(p_party_id uuid)
returns jsonb
language sql
stable
as $$
  select jsonb_build_object(
    'id',          id,
    'legal_name',  legal_name,
    'short_name',  short_name,
    'type',        type,
    'vat_id',      vat_id,
    'tax_number',  tax_number,
    'eori',        eori,
    'is_reverse_charge_eligible',
      coalesce(is_reverse_charge_eligible, false)
  )
  from parties
  where id = p_party_id;
$$;

create or replace function build_address_snapshot(p_address_id uuid)
returns jsonb
language sql
stable
as $$
  select jsonb_build_object(
    'id',           id,
    'kind',         kind,
    'label',        label,
    'street',       street,
    'street_2',     street_2,
    'zip',          zip,
    'city',         city,
    'country_code', country_code,
    'contact_name', contact_name,
    'contact_phone', contact_phone
  )
  from addresses
  where id = p_address_id;
$$;

create or replace function build_company_snapshot()
returns jsonb
language sql
stable
as $$
  select jsonb_build_object(
    'legal_name',        legal_name,
    'street',            street,
    'zip',               zip,
    'city',              city,
    'country_code',      country_code,
    'email',             email,
    'phone',             phone,
    'tax_number',        tax_number,
    'vat_id',            vat_id,
    'tax_office',        tax_office,
    'eori',              eori,
    'bank_name',         bank_name,
    'iban',              iban,
    'bic',               bic,
    'managing_director', managing_director,
    'hr_register',       hr_register
  )
  from company_settings
  limit 1;
$$;


-- ============================================================
-- 4. Backfill für bestehende gelockte Belege
--    Best-effort: nutzt heutige Live-Daten als Approximation.
--    Nur Belege ohne Snapshot werden befüllt.
-- ============================================================

-- invoices
update invoices i
   set customer_snapshot         = build_party_snapshot(i.customer_id),
       billing_address_snapshot  = build_address_snapshot(i.billing_address_id),
       shipping_address_snapshot = build_address_snapshot(i.shipping_address_id),
       company_snapshot          = build_company_snapshot()
 where i.locked_at is not null
   and i.customer_snapshot is null;

-- quotations (kein locked_at — sind keine GoBD-Belege im strengen Sinn).
-- Snapshot wird beim PDF-Persist nach status='sent' befüllt (in service.py),
-- nicht via Lock-Trigger. Backfill: alle gesendeten Angebote aus heutigen Daten.
update quotations q
   set customer_snapshot         = build_party_snapshot(q.customer_id),
       billing_address_snapshot  = build_address_snapshot(q.billing_address_id),
       shipping_address_snapshot = build_address_snapshot(q.shipping_address_id),
       company_snapshot          = build_company_snapshot()
 where q.status in ('sent','accepted','rejected','expired','converted')
   and q.customer_snapshot is null;

-- orders
update orders o
   set customer_snapshot         = build_party_snapshot(o.customer_id),
       billing_address_snapshot  = build_address_snapshot(o.billing_address_id),
       shipping_address_snapshot = build_address_snapshot(o.shipping_address_id),
       company_snapshot          = build_company_snapshot()
 where o.locked_at is not null
   and o.customer_snapshot is null;

-- purchase_orders
update purchase_orders po
   set supplier_snapshot         = build_party_snapshot(po.supplier_id),
       billing_address_snapshot  = build_address_snapshot(po.billing_address_id),
       shipping_address_snapshot = build_address_snapshot(po.shipping_address_id),
       company_snapshot          = build_company_snapshot()
 where po.locked_at is not null
   and po.supplier_snapshot is null;

-- deliveries
update deliveries d
   set party_snapshot            = build_party_snapshot(d.party_id),
       source_party_snapshot     = case
                                     when d.source_party_id is not null
                                       then build_party_snapshot(d.source_party_id)
                                     else null
                                   end,
       shipping_address_snapshot = build_address_snapshot(d.shipping_address_id),
       company_snapshot          = build_company_snapshot()
 where d.locked_at is not null
   and d.party_snapshot is null;


-- Items-Backfill: article_title_snapshot + article_sku_snapshot
-- aus articles.title_de + articles.sku, wenn leer und Beleg gelockt.
update invoice_items it
   set article_title_snapshot = a.title_de,
       article_sku_snapshot   = a.sku
  from articles a, invoices i
 where it.article_id = a.id
   and it.invoice_id = i.id
   and i.locked_at is not null
   and it.article_title_snapshot is null;

update order_items it
   set article_title_snapshot = a.title_de,
       article_sku_snapshot   = a.sku
  from articles a, orders o
 where it.article_id = a.id
   and it.order_id = o.id
   and o.locked_at is not null
   and it.article_title_snapshot is null;

update quotation_items it
   set article_title_snapshot = a.title_de,
       article_sku_snapshot   = a.sku
  from articles a, quotations q
 where it.article_id = a.id
   and it.quotation_id = q.id
   and q.status in ('sent','accepted','rejected','expired','converted')
   and it.article_title_snapshot is null;

update po_items it
   set article_title_snapshot = a.title_de,
       article_sku_snapshot   = a.sku
  from articles a, purchase_orders po
 where it.article_id = a.id
   and it.po_id = po.id
   and po.locked_at is not null
   and it.article_title_snapshot is null;

update delivery_items it
   set article_title_snapshot = a.title_de,
       article_sku_snapshot   = a.sku
  from articles a, deliveries d
 where it.article_id = a.id
   and it.delivery_id = d.id
   and d.locked_at is not null
   and it.article_title_snapshot is null;


-- ============================================================
-- 5. GoBD-Lock-Trigger: Snapshot-Felder NICHT in Whitelist.
--    Sie werden gleichzeitig mit locked_at gesetzt — der Trigger
--    sieht OLD.locked_at IS NULL und lässt das Initial-Befüllen
--    durch. Spätere UPDATE-Versuche an Snapshot-Feldern blockt
--    der Trigger korrekt (sie sind nicht in der Whitelist).
--
--    Daher: KEINE Änderung an _gobd_lock_*() nötig.
-- ============================================================

-- ============================================================
-- 6. replace_*_items()-RPCs erweitern um Snapshot-Spalten
--    (sonst ignorieren sie die neuen Felder im JSONB-Input)
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
         elem->>'article_title_snapshot',
         elem->>'article_sku_snapshot'
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


-- replace_quotation_items existiert in Migration 0008 — analog erweitern.
-- Die Funktion liest pos_nr/article_id/description_override/qty/unit/
-- unit_price_cents/tax_rate/discount_pct/tax_amount_cents/line_total_cents.
-- Zusätzliche Felder sind in Migration 0012 (per_item_delivery + quote_options).
-- Wir erweitern hier nur um Snapshot-Felder, ohne die bestehende Signatur zu kennen.

do $$
begin
  if exists (
    select 1 from pg_proc where proname = 'replace_quotation_items'
  ) then
    -- bestehende RPC bleibt; Snapshot-Befüllung für quotation_items übernehmen
    -- wir im Application-Layer (service.py replace_items() macht UPDATE nach RPC).
    null;
  end if;
end $$;


-- ============================================================
-- 7. Permissions
-- ============================================================
revoke all on function build_party_snapshot(uuid)   from public;
revoke all on function build_address_snapshot(uuid) from public;
revoke all on function build_company_snapshot()     from public;
grant execute on function build_party_snapshot(uuid)   to service_role, authenticated;
grant execute on function build_address_snapshot(uuid) to service_role, authenticated;
grant execute on function build_company_snapshot()     to service_role, authenticated;

revoke all on function replace_invoice_items(uuid,jsonb)  from public;
revoke all on function replace_order_items(uuid,jsonb)    from public;
revoke all on function replace_po_items(uuid,jsonb)       from public;
revoke all on function replace_delivery_items(uuid,jsonb) from public;
grant execute on function replace_invoice_items(uuid,jsonb)  to service_role, authenticated;
grant execute on function replace_order_items(uuid,jsonb)    to service_role, authenticated;
grant execute on function replace_po_items(uuid,jsonb)       to service_role, authenticated;
grant execute on function replace_delivery_items(uuid,jsonb) to service_role, authenticated;
