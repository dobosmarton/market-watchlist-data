MODEL (
    name marts.fin_gaps,
    kind VIEW
);

-- Concepts missing from each company's latest fiscal year, and companies with no annual
-- figures at all (missing lists every concept). `wl gaps` prints this after a refresh.
WITH latest AS (
    SELECT * FROM marts.fin_annual
    QUALIFY row_number() OVER (PARTITION BY slug ORDER BY period_end DESC) = 1
),
checked AS (
    SELECT
        c.slug,
        l.period_end,
        list_filter([
            CASE WHEN l.revenue IS NULL THEN 'revenue' END,
            CASE WHEN l.op_income IS NULL THEN 'op_income' END,
            CASE WHEN l.net_income IS NULL THEN 'net_income' END,
            CASE WHEN l.cfo IS NULL THEN 'cfo' END,
            CASE WHEN l.capex IS NULL THEN 'capex' END,
            CASE WHEN l.dna IS NULL THEN 'dna' END,
            CASE WHEN l.cash IS NULL THEN 'cash' END,
            CASE WHEN l.debt IS NULL THEN 'debt' END,
            CASE WHEN l.shares_diluted IS NULL THEN 'shares_diluted' END
        ], lambda concept: concept IS NOT NULL) AS missing
    FROM main.companies AS c
    LEFT JOIN latest AS l USING (slug)
)
SELECT * FROM checked
WHERE len(missing) > 0
ORDER BY slug
