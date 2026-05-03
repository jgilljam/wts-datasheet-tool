-- ============================================================
-- 0016_atomic_issue.sql — GoBD P2: Atomare Issue-Funktion
-- ============================================================
-- Schließt die Race, dass `next_belegnummer()` und das Status-Update
-- in zwei separaten DB-Calls passieren. Falls der Update zwischen
-- Counter-Bump und Status-Wechsel scheitert, ist der Counter
-- hochgezählt → Lücke beim nächsten Issue.
--
-- Lösung: PL/pgSQL-Funktion, die Counter-Bump + Update + Snapshot
-- in einer Transaktion macht. Bei Fehler im Update wird der
-- Counter-Bump zurückgerollt.
--
-- Pendants für orders/po/quotations/deliveries: bei diesen wird
-- die Belegnummer NICHT beim Lock vergeben, sondern beim Anlegen
-- (`create_*` ruft `next_belegnummer` direkt). Dort gibt es die
-- Race nicht — wenn Insert scheitert, ist die Nummer auch verbraucht,
-- aber das ist eine "geplante Lücke" mit dokumentiertem Grund
-- (= keine Beleg im System), die im Belegnummern-Audit erklärbar ist.
--
-- Idempotent.
-- ============================================================

create or replace function issue_invoice_atomic(
  p_invoice_id                 uuid,
  p_year                       int,
  p_customer_snapshot          jsonb,
  p_billing_address_snapshot   jsonb,
  p_shipping_address_snapshot  jsonb,
  p_company_snapshot           jsonb
) returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_status text;
  v_service_date date;
  v_item_count int;
  v_new_number text;
  v_now timestamptz := now();
begin
  -- 1. Status + Pflichtfelder prüfen
  select status, service_date
    into v_status, v_service_date
    from invoices
   where id = p_invoice_id
     for update;

  if v_status is null then
    raise exception 'Rechnung % nicht gefunden', p_invoice_id;
  end if;

  if v_status != 'draft' then
    raise exception 'Festschreiben nur aus Status draft möglich (aktuell: %)', v_status
      using errcode = '42501';
  end if;

  if v_service_date is null then
    raise exception 'Leistungsdatum (service_date) ist Pflicht nach UStG §14';
  end if;

  -- 2. Items zählen — keine leeren Rechnungen
  select count(*) into v_item_count from invoice_items where invoice_id = p_invoice_id;
  if v_item_count = 0 then
    raise exception 'Rechnung hat keine Positionen';
  end if;

  -- 3. Counter-Bump + Update in DERSELBEN Transaktion
  v_new_number := next_belegnummer('invoice', p_year);

  update invoices set
    invoice_number             = v_new_number,
    status                     = 'issued',
    locked_at                  = v_now,
    customer_snapshot          = p_customer_snapshot,
    billing_address_snapshot   = p_billing_address_snapshot,
    shipping_address_snapshot  = p_shipping_address_snapshot,
    company_snapshot           = p_company_snapshot,
    updated_at                 = v_now
   where id = p_invoice_id;

  if not found then
    raise exception 'Update auf invoices % schlug fehl — Counter wird gerollback', p_invoice_id;
  end if;

  return v_new_number;
end $$;


-- Permissions
revoke all on function issue_invoice_atomic(uuid, int, jsonb, jsonb, jsonb, jsonb) from public;
grant execute on function issue_invoice_atomic(uuid, int, jsonb, jsonb, jsonb, jsonb)
  to service_role, authenticated;
