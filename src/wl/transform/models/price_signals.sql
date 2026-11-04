MODEL (
    name marts.price_signals,
    kind VIEW
);

-- Trend, momentum and activity per company as of its latest close, in the quote currency.
-- Windows count trading days (rows), so they follow each listing's calendar: 21 rows is about
-- a month, 252 a year. An average or return without its full window stays null. The *_date
-- columns give the last time something happened, *_bars_ago how many trading days back.
WITH bars AS (
    SELECT
        slug,
        date,
        close,
        volume,
        ccy,
        coalesce(high, close) AS high,
        coalesce(low, close) AS low,
        row_number() OVER (PARTITION BY slug ORDER BY date) AS n,
        lag(close, 21) OVER (PARTITION BY slug ORDER BY date) AS close_1m,
        lag(close, 63) OVER (PARTITION BY slug ORDER BY date) AS close_3m,
        lag(close, 126) OVER (PARTITION BY slug ORDER BY date) AS close_6m,
        lag(close, 252) OVER (PARTITION BY slug ORDER BY date) AS close_12m,
        close / lag(close) OVER (PARTITION BY slug ORDER BY date) - 1 AS daily_return,
        CASE WHEN count(*) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
        ) = 50 THEN avg(close) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
        ) END AS sma50,
        CASE WHEN count(*) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
        ) = 200 THEN avg(close) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
        ) END AS sma200,
        avg(volume) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS volume_5d,
        -- the 50 sessions before those 5, so a spike does not lift its own baseline
        CASE WHEN count(volume) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 54 PRECEDING AND 5 PRECEDING
        ) = 50 THEN avg(volume) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 54 PRECEDING AND 5 PRECEDING
        ) END AS volume_50d,
        max(coalesce(high, close)) OVER (
            PARTITION BY slug ORDER BY date RANGE BETWEEN INTERVAL 365 DAYS PRECEDING AND CURRENT ROW
        ) AS wk52_high,
        min(coalesce(low, close)) OVER (
            PARTITION BY slug ORDER BY date RANGE BETWEEN INTERVAL 365 DAYS PRECEDING AND CURRENT ROW
        ) AS wk52_low,
        -- the year before this bar, to tell a new high or low from a repeat
        max(coalesce(high, close)) OVER (
            PARTITION BY slug ORDER BY date
            RANGE BETWEEN INTERVAL 365 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
        ) AS prior_high,
        min(coalesce(low, close)) OVER (
            PARTITION BY slug ORDER BY date
            RANGE BETWEEN INTERVAL 365 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
        ) AS prior_low,
        count(*) OVER (
            PARTITION BY slug ORDER BY date
            RANGE BETWEEN INTERVAL 365 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
        ) AS prior_bars
    FROM main.raw_prices
),
states AS (
    SELECT
        *,
        close > sma200 AS above_sma200,
        sma50 > sma200 AS golden,
        -- a high or low only counts as new against most of a year of bars
        prior_bars >= 200 AND high > prior_high AS new_high,
        prior_bars >= 200 AND low < prior_low AS new_low,
        stddev_samp(daily_return) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) * sqrt(252) * 100 AS volatility_20d_pct,
        CASE WHEN count(daily_return) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
        ) >= 200 THEN stddev_samp(daily_return) OVER (
            PARTITION BY slug ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
        ) * sqrt(252) * 100 END AS volatility_1y_pct
    FROM bars
),
changes AS (
    SELECT
        *,
        above_sma200 <> lag(above_sma200) OVER (PARTITION BY slug ORDER BY date) AS crossed_sma200,
        golden <> lag(golden) OVER (PARTITION BY slug ORDER BY date) AS crossed_averages
    FROM states
),
events AS (
    SELECT
        slug,
        max(n) AS last_n,
        max(date) FILTER (WHERE crossed_sma200) AS sma200_cross_date,
        max(n) FILTER (WHERE crossed_sma200) AS sma200_cross_n,
        max(date) FILTER (WHERE crossed_averages) AS ma_cross_date,
        max(n) FILTER (WHERE crossed_averages) AS ma_cross_n,
        max(date) FILTER (WHERE new_high) AS new_high_date,
        max(n) FILTER (WHERE new_high) AS new_high_n,
        max(date) FILTER (WHERE new_low) AS new_low_date,
        max(n) FILTER (WHERE new_low) AS new_low_n
    FROM changes
    GROUP BY slug
),
latest AS (
    SELECT c.*, e.* EXCLUDE (slug)
    FROM changes AS c
    JOIN events AS e ON e.slug = c.slug AND e.last_n = c.n
),
measured AS (
    SELECT
        slug,
        date AS price_date,
        close AS price,
        ccy,
        (close / close_1m - 1) * 100 AS return_1m_pct,
        (close / close_3m - 1) * 100 AS return_3m_pct,
        (close / close_6m - 1) * 100 AS return_6m_pct,
        (close / close_12m - 1) * 100 AS return_12m_pct,
        sma50,
        sma200,
        (close / sma50 - 1) * 100 AS vs_sma50_pct,
        (close / sma200 - 1) * 100 AS vs_sma200_pct,
        above_sma200,
        sma200_cross_date,
        last_n - sma200_cross_n AS sma200_cross_bars_ago,
        CASE WHEN golden THEN 'golden' WHEN NOT golden THEN 'death' END AS ma_cross,
        ma_cross_date,
        last_n - ma_cross_n AS ma_cross_bars_ago,
        wk52_high,
        wk52_low,
        (close / wk52_high - 1) * 100 AS drawdown_pct,
        new_high_date,
        last_n - new_high_n AS new_high_bars_ago,
        new_low_date,
        last_n - new_low_n AS new_low_bars_ago,
        volatility_20d_pct,
        volatility_1y_pct,
        volume_5d / nullif(volume_50d, 0) AS volume_ratio
    FROM latest
)
SELECT
    *,
    -- 1 = the best return on the watchlist over that period
    CASE WHEN return_1m_pct IS NOT NULL THEN rank() OVER (
        PARTITION BY return_1m_pct IS NULL ORDER BY return_1m_pct DESC
    ) END AS rank_1m,
    CASE WHEN return_3m_pct IS NOT NULL THEN rank() OVER (
        PARTITION BY return_3m_pct IS NULL ORDER BY return_3m_pct DESC
    ) END AS rank_3m
FROM measured
