-- Latest close and trailing 52-week range per company, in the quote currency.
CREATE OR REPLACE VIEW price_stats AS
WITH latest AS (
    SELECT slug, max(date) AS last_date FROM raw_prices GROUP BY slug
)
SELECT
    p.slug,
    l.last_date AS price_date,
    arg_max(p.close, p.date) AS price,
    any_value(p.ccy) AS ccy,
    min(p.low) FILTER (WHERE p.date > l.last_date - INTERVAL 365 DAY) AS wk52_low,
    max(p.high) FILTER (WHERE p.date > l.last_date - INTERVAL 365 DAY) AS wk52_high,
    arg_max(p.source, p.date) AS source
FROM raw_prices AS p
JOIN latest AS l USING (slug)
GROUP BY p.slug, l.last_date;
