MODEL (
    name marts.fx_latest,
    kind VIEW
);

-- Latest ECB reference rate per currency, quoted against EUR (EUR itself is 1).
SELECT quote, arg_max(rate, date) AS eur_rate, max(date) AS date
FROM main.raw_fx
WHERE base = 'EUR'
GROUP BY quote
UNION ALL
SELECT 'EUR', 1.0, current_date
