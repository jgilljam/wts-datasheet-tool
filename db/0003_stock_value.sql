-- ============================================================
--  WTS-Tool — Migration 0003: Stock-Balances um Wert erweitern
-- ============================================================
--  Fügt der View `stock_balances` zwei Spalten hinzu:
--    - default_price_cents (EK aus articles, durchgereicht)
--    - value_cents         (qty_on_hand × default_price_cents)
--  Damit kann die UI den Lagerwert ausweisen ohne Join.
-- ============================================================

drop view if exists stock_balances;

create view stock_balances as
  select
    a.id                                   as article_id,
    a.sku,
    a.manufacturer_sku,
    a.title_de,
    a.unit,
    a.min_stock_qty,
    a.is_active,
    a.is_pfand,
    a.adr_un_nr,
    a.adr_class,
    a.default_price_cents,
    coalesce(sum(m.qty_delta), 0)          as qty_on_hand,
    (coalesce(sum(m.qty_delta), 0) * coalesce(a.default_price_cents, 0))::bigint
                                            as value_cents,
    case
      when a.min_stock_qty is not null
       and coalesce(sum(m.qty_delta), 0) <= a.min_stock_qty
        then true
      else false
    end                                    as below_min,
    max(m.at)                              as last_movement_at
  from articles a
  left join stock_movements m on m.article_id = a.id
  group by
    a.id, a.sku, a.manufacturer_sku, a.title_de, a.unit,
    a.min_stock_qty, a.is_active, a.is_pfand, a.adr_un_nr,
    a.adr_class, a.default_price_cents;
