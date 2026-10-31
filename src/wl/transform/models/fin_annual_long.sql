MODEL (
    name marts.fin_annual_long,
    kind VIEW
);

-- Each company's mapped concepts per period, in the reporting currency: the best tag wins.
-- Annual reports only: 10-Qs repeat the prior year-end balance sheet, which must not outrank it.
-- Flow concepts must cover roughly a year (330-400 days); instants sit on the fiscal year end.
-- A concept_map taxonomy of '*' matches the tag under any prefix (ESEF extension taxonomies).
WITH candidates AS (
    SELECT
        f.slug, m.concept, m.priority, m.kind, f.period_start, f.period_end, f.value, f.unit,
        f.filed, f.source, f.source_url, f.form
    FROM main.raw_facts AS f
    JOIN main.concept_map AS m ON m.tag = f.tag AND m.taxonomy IN (f.taxonomy, '*')
    JOIN main.companies AS c ON c.slug = f.slug
    WHERE (
            (m.kind = 'flow' AND f.period_start IS NOT NULL
             AND date_diff('day', f.period_start, f.period_end) BETWEEN 330 AND 400)
         OR (m.kind = 'instant' AND f.period_start IS NULL)
        )
      AND f.unit IN (c.reporting_ccy, 'shares', c.reporting_ccy || '/shares')
      AND coalesce(f.form, '') NOT LIKE '10-Q%'
),
ranked AS (
    SELECT *,
        row_number() OVER (
            PARTITION BY slug, concept, period_end
            ORDER BY priority, filed DESC NULLS LAST
        ) AS rn
    FROM candidates
)
SELECT slug, concept, period_end, value, unit, source, source_url, form
FROM ranked
WHERE rn = 1
