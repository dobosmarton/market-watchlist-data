MODEL (
    name marts.fin_ttm,
    kind VIEW
);

-- One row per company: trailing twelve months where 10-Q filings allow it, otherwise the
-- latest fiscal year (basis = 'annual'; ESEF, 20-F/40-F and manual filers have no quarterlies).
-- Flows: last fiscal year + this year to date - the same period a year earlier. A flow
-- missing either year-to-date figure keeps its annual value. The balance sheet and the
-- diluted share count come from the latest quarter end, falling back to the annual report.
WITH latest_fy AS (
    SELECT * FROM marts.fin_annual
    QUALIFY row_number() OVER (PARTITION BY slug ORDER BY period_end DESC) = 1
),
quarterly AS (
    -- mapped 10-Q values, one per concept and period: best tag first, then the latest filing
    SELECT f.slug, m.concept, m.kind, f.period_start, f.period_end, f.value
    FROM main.raw_facts AS f
    JOIN main.concept_map AS m ON m.tag = f.tag AND m.taxonomy IN (f.taxonomy, '*')
    JOIN main.companies AS c ON c.slug = f.slug
    WHERE f.form LIKE '10-Q%'
      AND f.unit IN (c.reporting_ccy, 'shares', c.reporting_ccy || '/shares')
    QUALIFY row_number() OVER (
        PARTITION BY f.slug, m.concept, f.period_start, f.period_end
        ORDER BY m.priority, f.filed DESC NULLS LAST
    ) = 1
),
ytd AS (
    -- this fiscal year to date: flows that start right after the latest fiscal year end
    SELECT q.*, date_diff('day', q.period_start, q.period_end) AS days
    FROM quarterly AS q
    JOIN latest_fy AS y USING (slug)
    WHERE q.kind = 'flow'
      AND q.period_start BETWEEN y.period_end AND y.period_end + INTERVAL 7 DAY
    QUALIFY q.period_end = max(q.period_end) OVER (PARTITION BY q.slug)
),
prior_ytd AS (
    -- the same span a year earlier (filed as a comparative), matched within ten days
    SELECT t.slug, t.concept, p.value
    FROM ytd AS t
    JOIN quarterly AS p
      ON p.slug = t.slug AND p.concept = t.concept AND p.kind = 'flow'
     AND abs(date_diff('day', p.period_end + INTERVAL 1 YEAR, t.period_end)) <= 10
     AND abs(date_diff('day', p.period_start, p.period_end) - t.days) <= 10
    QUALIFY row_number() OVER (
        PARTITION BY t.slug, t.concept
        ORDER BY abs(date_diff('day', p.period_end + INTERVAL 1 YEAR, t.period_end))
    ) = 1
),
ytd_wide AS (
    SELECT
        t.slug,
        max(t.period_end) AS ytd_end,
        max(t.value - p.value) FILTER (WHERE t.concept = 'revenue') AS revenue,
        max(t.value - p.value) FILTER (WHERE t.concept = 'op_income') AS op_income,
        max(t.value - p.value) FILTER (WHERE t.concept = 'net_income') AS net_income,
        max(t.value - p.value) FILTER (WHERE t.concept = 'cfo') AS cfo,
        max(t.value - p.value) FILTER (WHERE t.concept = 'capex') AS capex,
        max(t.value - p.value) FILTER (WHERE t.concept = 'dna') AS dna
    FROM ytd AS t
    JOIN prior_ytd AS p USING (slug, concept)
    GROUP BY t.slug
),
newest_quarter AS (
    SELECT q.slug, max(q.period_end) AS period_end
    FROM quarterly AS q
    JOIN latest_fy AS y USING (slug)
    WHERE q.period_end > y.period_end
    GROUP BY q.slug
),
quarter_end AS (
    -- balance sheet and the quarter's diluted weighted shares at the newest 10-Q period end
    SELECT
        q.slug,
        max(q.value) FILTER (WHERE q.concept = 'cash' AND q.kind = 'instant') AS cash,
        max(q.value) FILTER (WHERE q.concept = 'debt_total' AND q.kind = 'instant') AS debt_total,
        max(q.value) FILTER (
            WHERE q.concept = 'debt_noncurrent' AND q.kind = 'instant'
        ) AS debt_noncurrent,
        max(q.value) FILTER (
            WHERE q.concept = 'debt_current' AND q.kind = 'instant'
        ) AS debt_current,
        max(q.value) FILTER (
            WHERE q.concept = 'shares_diluted'
              AND date_diff('day', q.period_start, q.period_end) BETWEEN 80 AND 100
        ) AS shares_diluted
    FROM quarterly AS q
    JOIN newest_quarter AS n ON n.slug = q.slug AND n.period_end = q.period_end
    GROUP BY q.slug
),
combined AS (
    SELECT
        y.slug,
        y.ccy,
        y.period_end AS fiscal_year_end,
        w.ytd_end,
        coalesce(y.revenue + w.revenue, y.revenue) AS revenue,
        coalesce(y.op_income + w.op_income, y.op_income) AS op_income,
        coalesce(y.net_income + w.net_income, y.net_income) AS net_income,
        coalesce(y.cfo + w.cfo, y.cfo) AS cfo,
        coalesce(y.capex + w.capex, y.capex) AS capex,
        coalesce(y.dna + w.dna, y.dna) AS dna,
        coalesce(b.cash, y.cash) AS cash,
        coalesce(coalesce(b.debt_total, b.debt_noncurrent + coalesce(b.debt_current, 0)), y.debt)
            AS debt,
        coalesce(b.shares_diluted, y.shares_diluted) AS shares_diluted,
        y.revenue + w.revenue IS NOT NULL AS revenue_is_ttm
    FROM latest_fy AS y
    LEFT JOIN ytd_wide AS w USING (slug)
    LEFT JOIN quarter_end AS b USING (slug)
)
SELECT
    slug,
    CASE WHEN revenue_is_ttm THEN 'ttm' ELSE 'annual' END AS basis,
    CASE WHEN revenue_is_ttm THEN ytd_end ELSE fiscal_year_end END AS period_end,
    fiscal_year_end,
    ccy,
    revenue,
    op_income,
    net_income,
    cfo,
    capex,
    dna,
    cash,
    debt,
    shares_diluted,
    op_income / nullif(revenue, 0) * 100 AS op_margin_pct,
    cfo - capex AS fcf,
    op_income + dna AS ebitda
FROM combined
