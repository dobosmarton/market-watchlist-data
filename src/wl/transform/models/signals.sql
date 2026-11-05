MODEL (
    name marts.signals,
    kind VIEW
);

-- What deserves attention, one row per signal, for the agent to start from.
-- standing = true: a condition that holds as of the latest data, dated by that data;
-- standing = false: an event on that date. The thresholds are in `limits`.
-- Sources: prices, financials, and SEC filings with the insider trades from Form 4s.
WITH limits AS (
    SELECT
        10 AS cross_bars_limit,         -- crosses within the last 10 trading days
        5 AS extreme_bars_limit,        -- new 52-week highs or lows within the last 5
        -20.0 AS drawdown_limit,        -- % at least this far below the 52-week high
        15.0 AS move_1m_limit,          -- % a month's return at least this large either way
        2.0 AS volume_limit,            -- the last 5 sessions' volume against the 50 before
        -5.0 AS revenue_falling_limit,  -- % latest fiscal year against the one before
        15.0 AS revenue_growing_limit,  -- %
        2.0 AS margin_change_limit,     -- points of operating margin change, either way
        100.0 AS margin_range_limit,    -- % margins beyond this (tiny revenue) are not compared
        250000.0 AS insider_sale_limit  -- EUR one person sold in the open market in a month
),
p AS (SELECT * FROM marts.price_signals CROSS JOIN limits),
f AS (SELECT * FROM marts.fundamental_signals CROSS JOIN limits),
-- open-market buys, one row per person and filing
buys AS (
    SELECT
        slug, person, role, id, max(trade_date) AS date, sum(shares) AS shares,
        sum(value) / sum(shares) AS avg_price, any_value(ccy) AS ccy,
        sum(value_eur) AS value_eur, any_value(url) AS url, any_value(source) AS source
    FROM marts.insider_trades
    WHERE code = 'P' AND NOT derivative
    GROUP BY slug, person, role, id
),
-- open-market sales, one row per person and calendar month
sales AS (
    SELECT
        slug, person, role, date_trunc('month', trade_date) AS month, max(trade_date) AS date,
        sum(shares) AS shares, sum(value_eur) AS value_eur, bool_and(planned) AS planned,
        arg_max(url, trade_date) AS url, any_value(source) AS source
    FROM marts.insider_trades
    WHERE code = 'S' AND NOT derivative
    GROUP BY slug, person, role, month
),
insiders AS (
    SELECT
        'buy' AS side, slug, person, role, date, shares, value_eur, url, source,
        coalesce(printf(' at %.2f %s', avg_price, ccy), '') AS detail
    FROM buys
    UNION ALL
    SELECT
        'sale', slug, person, role, date, shares, value_eur, url, source,
        printf(
            ' in %s%s', strftime(month, '%B %Y'),
            CASE WHEN planned THEN ', under a 10b5-1 plan' ELSE '' END
        )
    FROM sales CROSS JOIN limits
    WHERE value_eur >= insider_sale_limit
),
found AS (
    SELECT
        slug, sma200_cross_date AS date, 'price' AS category,
        CASE WHEN above_sma200 THEN 'crossed_above_sma200' ELSE 'crossed_below_sma200' END
            AS kind,
        printf(
            'Closed %s its 200-day average (now %+.1f%% from it)',
            CASE WHEN above_sma200 THEN 'above' ELSE 'below' END, vs_sma200_pct
        ) AS title,
        vs_sma200_pct AS value, FALSE AS standing, 'prices' AS source, NULL AS url
    FROM p WHERE sma200_cross_bars_ago < cross_bars_limit

    UNION ALL
    SELECT
        slug, ma_cross_date, 'price', ma_cross || '_cross',
        printf(
            '50-day average crossed %s the 200-day (%s cross)',
            CASE WHEN ma_cross = 'golden' THEN 'above' ELSE 'below' END, ma_cross
        ),
        (sma50 / sma200 - 1) * 100, FALSE, 'prices', NULL
    FROM p WHERE ma_cross_bars_ago < cross_bars_limit

    UNION ALL
    SELECT
        slug, new_high_date, 'price', 'new_52w_high',
        printf('New 52-week high: %.2f %s', wk52_high, ccy), wk52_high, FALSE, 'prices', NULL
    FROM p WHERE new_high_bars_ago < extreme_bars_limit

    UNION ALL
    SELECT
        slug, new_low_date, 'price', 'new_52w_low',
        printf('New 52-week low: %.2f %s', wk52_low, ccy), wk52_low, FALSE, 'prices', NULL
    FROM p WHERE new_low_bars_ago < extreme_bars_limit

    UNION ALL
    SELECT
        slug, price_date, 'price', 'drawdown',
        printf('%.1f%% below its 52-week high of %.2f %s', -drawdown_pct, wk52_high, ccy),
        drawdown_pct, TRUE, 'prices', NULL
    FROM p WHERE drawdown_pct <= drawdown_limit

    UNION ALL
    SELECT
        slug, price_date, 'price', 'big_move_1m',
        printf('%+.1f%% over the last month (rank %d of the watchlist)', return_1m_pct, rank_1m),
        return_1m_pct, TRUE, 'prices', NULL
    FROM p WHERE abs(return_1m_pct) >= move_1m_limit

    UNION ALL
    SELECT
        slug, price_date, 'price', 'volume_spike',
        printf('Volume %.1fx its usual level over the last 5 sessions', volume_ratio),
        volume_ratio, TRUE, 'prices', NULL
    FROM p WHERE volume_ratio >= volume_limit

    UNION ALL
    SELECT
        slug, period_end, 'fundamental',
        CASE WHEN revenue_growth_pct < 0 THEN 'revenue_falling' ELSE 'revenue_growing' END,
        printf('Revenue %+.1f%% in fiscal %d', revenue_growth_pct, fiscal_year),
        revenue_growth_pct, TRUE, 'financials', NULL
    FROM f
    WHERE revenue_growth_pct <= revenue_falling_limit
        OR revenue_growth_pct >= revenue_growing_limit

    UNION ALL
    SELECT
        slug, period_end, 'fundamental',
        CASE WHEN op_margin_change_pp < 0 THEN 'margin_down' ELSE 'margin_up' END,
        printf(
            'Operating margin %+.1f points in fiscal %d, to %.1f%%',
            op_margin_change_pp, fiscal_year, op_margin_pct
        ),
        op_margin_change_pp, TRUE, 'financials', NULL
    FROM f
    WHERE abs(op_margin_change_pp) >= margin_change_limit
        AND abs(op_margin_pct) <= margin_range_limit
        AND abs(op_margin_pct - op_margin_change_pp) <= margin_range_limit

    UNION ALL
    SELECT
        slug, date, 'insider', 'insider_' || side,
        printf(
            '%s%s %s %s shares%s%s', person,
            CASE WHEN role <> '' THEN ' (' || role || ')' ELSE '' END,
            CASE WHEN side = 'buy' THEN 'bought' ELSE 'sold' END,
            format('{:,}', round(shares)::BIGINT),
            CASE
                WHEN value_eur >= 1e6 THEN printf(' (about EUR %.1fm)', value_eur / 1e6)
                WHEN value_eur IS NOT NULL THEN printf(' (about EUR %.0fk)', value_eur / 1e3)
                ELSE ''
            END,
            detail
        ),
        value_eur, FALSE, source, url
    FROM insiders

    UNION ALL
    SELECT
        slug, date, 'filing',
        CASE
            WHEN form LIKE '8-K%' THEN 'current_report'
            WHEN form LIKE '%13D%' OR form LIKE '%13G%' THEN 'major_holder'
            ELSE 'new_report'
        END,
        title, NULL, FALSE, source, url
    FROM main.raw_filings
    WHERE form NOT LIKE '6-K%'  -- a foreign issuer's 6-K can be any press release
)
SELECT
    s.slug,
    c.name,
    s.date,
    s.category,
    s.kind,
    s.title,
    s.value::DOUBLE AS value,
    s.standing,
    s.url,
    s.source
FROM found AS s
JOIN main.companies AS c USING (slug)
