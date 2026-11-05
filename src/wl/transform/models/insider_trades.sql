MODEL (
    name marts.insider_trades,
    kind VIEW
);

-- One row per insider transaction, with what it was worth: value is shares x price in the
-- trade's currency, value_eur the same at the latest ECB rate. An amended filing (4/A)
-- restates the original's trades, so it replaces them: the original's lines by the same
-- person on the same trade date are left out.
WITH amended AS (
    SELECT DISTINCT slug, source, person, trade_date
    FROM main.raw_insider_trades
    WHERE amendment
)
SELECT
    t.slug,
    t.filed,
    t.trade_date,
    t.person,
    t.role,
    t.code,
    CASE t.code
        WHEN 'P' THEN 'buy'
        WHEN 'S' THEN 'sale'
        WHEN 'A' THEN 'award'
        WHEN 'M' THEN 'exercise'
        WHEN 'C' THEN 'conversion'
        WHEN 'F' THEN 'tax withholding'
        WHEN 'G' THEN 'gift'
        WHEN 'D' THEN 'sold back to the company'
        ELSE 'other'
    END AS trade_type,
    t.acquired,
    t.shares,
    t.price,
    t.ccy,
    t.shares * t.price AS value,
    t.shares * t.price / fx.eur_rate AS value_eur,
    t.owned_after,
    t.planned,
    t.derivative,
    t.amendment,
    t.security,
    t.id,
    t.url,
    t.source
FROM main.raw_insider_trades AS t
LEFT JOIN marts.fx_latest AS fx ON fx.quote = t.ccy
LEFT JOIN amended AS a
    ON a.slug = t.slug AND a.source = t.source AND a.person = t.person
    AND a.trade_date = t.trade_date
WHERE t.amendment OR a.person IS NULL
