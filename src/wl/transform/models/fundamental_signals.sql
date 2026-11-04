MODEL (
    name marts.fundamental_signals,
    kind VIEW
);

-- Growth and margin trend per company: its latest fiscal year against the year before and
-- three years before, in the reporting currency. A comparison needs that earlier year on
-- file (ending 11-13 or 33-39 months before), so a missing year leaves it null.
WITH latest AS (
    SELECT * FROM marts.fin_annual
    QUALIFY row_number() OVER (PARTITION BY slug ORDER BY period_end DESC) = 1
)
SELECT
    l.slug,
    l.period_end,
    l.fiscal_year,
    l.ccy,
    l.revenue,
    (l.revenue / nullif(p.revenue, 0) - 1) * 100 AS revenue_growth_pct,
    -- growth from a loss means nothing
    CASE WHEN p.net_income > 0 THEN (l.net_income / p.net_income - 1) * 100 END
        AS net_income_growth_pct,
    l.op_margin_pct,
    l.op_margin_pct - p.op_margin_pct AS op_margin_change_pp,
    l.fcf / nullif(l.revenue, 0) * 100 AS fcf_margin_pct,
    CASE WHEN l.revenue > 0 AND o.revenue > 0
        THEN (power(l.revenue / o.revenue, 1.0 / 3) - 1) * 100 END AS revenue_cagr_3y_pct
FROM latest AS l
LEFT JOIN marts.fin_annual AS p
    ON p.slug = l.slug AND date_diff('month', p.period_end, l.period_end) BETWEEN 11 AND 13
LEFT JOIN marts.fin_annual AS o
    ON o.slug = l.slug AND date_diff('month', o.period_end, l.period_end) BETWEEN 33 AND 39
