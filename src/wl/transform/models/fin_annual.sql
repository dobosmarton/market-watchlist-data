MODEL (
    name marts.fin_annual,
    kind VIEW
);

-- One row per company and fiscal year with the mapped concepts in the reporting currency.
WITH flows AS (
    -- fiscal years are anchored on the period end of the revenue line
    SELECT DISTINCT slug, period_end
    FROM marts.fin_annual_long
    WHERE concept = 'revenue'
),
wide AS (
    SELECT
        y.slug,
        y.period_end,
        year(y.period_end) AS fiscal_year,
        max(CASE WHEN l.concept = 'revenue' THEN l.value END) AS revenue,
        max(CASE WHEN l.concept = 'op_income' THEN l.value END) AS op_income,
        max(CASE WHEN l.concept = 'net_income' THEN l.value END) AS net_income,
        max(CASE WHEN l.concept = 'cfo' THEN l.value END) AS cfo,
        max(CASE WHEN l.concept = 'capex' THEN l.value END) AS capex,
        max(CASE WHEN l.concept = 'dna' THEN l.value END) AS dna_reported,
        max(CASE WHEN l.concept = 'dep_ppe' THEN l.value END) AS dep_ppe,
        max(CASE WHEN l.concept = 'dep_rou' THEN l.value END) AS dep_rou,
        max(CASE WHEN l.concept = 'amort' THEN l.value END) AS amort,
        max(CASE WHEN l.concept = 'cash' THEN l.value END) AS cash,
        max(CASE WHEN l.concept = 'debt_total' THEN l.value END) AS debt_total,
        max(CASE WHEN l.concept = 'debt_noncurrent' THEN l.value END) AS debt_noncurrent,
        max(CASE WHEN l.concept = 'debt_current' THEN l.value END) AS debt_current,
        max(CASE WHEN l.concept = 'shares_diluted' THEN l.value END) AS shares_reported,
        max(CASE WHEN l.concept = 'eps_diluted' THEN l.value END) AS eps_diluted,
        any_value(l.source) AS source,
        any_value(l.source_url) AS source_url
    FROM flows AS y
    LEFT JOIN marts.fin_annual_long AS l ON l.slug = y.slug AND l.period_end = y.period_end
    GROUP BY ALL
),
summed AS (
    -- some filers tag depreciation and amortisation only as separate lines
    SELECT
        * EXCLUDE (dna_reported, dep_ppe, dep_rou, amort),
        coalesce(dna_reported, dep_ppe + coalesce(dep_rou, 0) + coalesce(amort, 0)) AS dna
    FROM wide
)
SELECT
    w.* EXCLUDE (shares_reported),
    c.reporting_ccy AS ccy,
    -- most ESEF filers tag diluted EPS but not the share count behind it
    coalesce(w.shares_reported, w.net_income / nullif(w.eps_diluted, 0)) AS shares_diluted,
    coalesce(w.debt_total, w.debt_noncurrent + coalesce(w.debt_current, 0)) AS debt,
    w.op_income / nullif(w.revenue, 0) * 100 AS op_margin_pct,
    w.cfo - w.capex AS fcf,
    w.op_income + w.dna AS ebitda
FROM summed AS w
JOIN main.companies AS c USING (slug)
