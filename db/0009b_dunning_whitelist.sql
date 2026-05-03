-- Erweitert die GoBD-Lock-Whitelist für invoices um Mahnwesen-Felder
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
      'current_dunning_level', 'last_dunning_at'
    ]
  );
  return NEW;
end $$;
