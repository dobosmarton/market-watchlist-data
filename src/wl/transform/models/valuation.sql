MODEL (
    name marts.valuation,
    kind VIEW
);

-- Valuation on the latest annual figures. Market cap uses diluted weighted shares from the
-- last annual report, converted to the reporting currency with ECB rates (EUR cross).
-- Companies without borrowings usually tag no debt at all, so missing debt counts as zero once
-- cash is reported; debt_reported keeps that visible. Without cash there is no net debt or EV.
WITH last_year AS (
    SELECT * FROM marts.fin_annual
    QUALIFY row_number() OVER (PARTITION BY slug ORDER BY period_end DESC) = 1
),
priced AS (
    SELECT
        c.slug, c.name, c.grp, c.quote_ccy, c.reporting_ccy,
        s.price, s.price_date, s.wk52_low, s.wk52_high,
        -- price converted from quote to reporting currency: price * EUR->rep / EUR->quote
        s.price * fr.eur_rate / fq.eur_rate AS price_rep,
        y.period_end AS fin_period_end, y.revenue, y.op_income, y.net_income, y.fcf, y.ebitda,
        y.cash, y.debt, y.shares_diluted, y.op_margin_pct,
        y.debt IS NOT NULL AS debt_reported,
        coalesce(y.debt, 0) - y.cash AS net_debt
    FROM main.companies AS c
    LEFT JOIN marts.price_stats AS s USING (slug)
    LEFT JOIN last_year AS y USING (slug)
    LEFT JOIN marts.fx_latest AS fq ON fq.quote = c.quote_ccy
    LEFT JOIN marts.fx_latest AS fr ON fr.quote = c.reporting_ccy
)
SELECT
    *,
    price_rep * shares_diluted AS market_cap,
    price * shares_diluted AS market_cap_quote,
    price_rep * shares_diluted + net_debt AS enterprise_value,
    (price_rep * shares_diluted) / nullif(net_income, 0) AS pe_ttm,
    (price_rep * shares_diluted + net_debt) / nullif(ebitda, 0) AS ev_ebitda,
    fcf / nullif(price_rep * shares_diluted, 0) * 100 AS fcf_yield_pct,
    net_debt / nullif(ebitda, 0) AS net_debt_ebitda,
    (price - wk52_low) / nullif(wk52_high - wk52_low, 0) * 100 AS wk52_pos
FROM priced
