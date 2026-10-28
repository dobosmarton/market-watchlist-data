# watchlist-data

Data pipeline for the market-analysis watchlist. It collects daily prices, ECB FX rates and
annual reported financials from free sources, stores them in DuckDB, computes valuation
ratios in SQL, and exports JSON files in the shape the watchlist dashboard reads.

## Quick start

```sh
# needs uv (https://docs.astral.sh/uv/) and just (brew install just)
cp .env.example .env        # set WL_SEC_USER_AGENT to "<your name> <your email>"
cp watchlist.example.yaml watchlist.yaml   # then edit it to list your companies
just sync                   # Python 3.13 + all dependencies from uv.lock
uv run wl companies         # what is tracked and where each company's data comes from
just resolve-ids            # print missing LEIs (needed for ESEF filings), paste into watchlist.yaml
just refresh                # fx, prices, financials → data/watchlist.duckdb
just export                 # exports/valuation.json, exports/financials.json
```

`just refresh prices` (or `fx`, `financials`) runs one job. Explore the data with
`duckdb data/watchlist.duckdb` and `SELECT * FROM valuation`.

With Docker: `docker compose run --rm refresh`, then `docker compose run --rm export`. The
watchlist is not baked into the image; compose mounts your `watchlist.yaml`.

## Sources

| Data | Source | Coverage | Key |
|---|---|---|---|
| FX | ECB reference rates via Frankfurter | all currencies on the watchlist | none |
| SEC financials | SEC EDGAR company facts (10-K, 20-F, 40-F) | SEC filers, including foreign issuers filing 20-F or 40-F | none, User-Agent required |
| ESEF financials | ESEF filings via filings.xbrl.org | EU/EEA issuers that file in ESEF | none |
| US prices | Twelve Data `time_series` | US listings | free API key |
| European prices | Yahoo via yfinance (`yahoo` extra) | any exchange Yahoo covers | none; unofficial, personal use |
| LEI lookup | GLEIF | all | none |

Some countries' filings are not on filings.xbrl.org (Germany, for example). Mark those companies
`financials: manual`; annual-report extraction for them comes in milestone 2.

## Layout

```
watchlist.example.yaml  example companies; copy to watchlist.yaml (gitignored) and edit
src/wl/
  config.py             typed watchlist model (Pydantic)
  settings.py           WL_* environment settings
  sources/              one module per source; each returns typed records
  store.py              DuckDB / DuckLake storage and raw tables
  concepts.py           US-GAAP and IFRS tags mapped onto one concept set
  sql/                  marts: fin_annual, price_stats, valuation
  pipeline.py           refresh jobs; one company failing never stops the rest
  export.py             dashboard JSON
  cli.py                `wl` command
tests/                  offline tests with recorded fixtures
```

## Development

`just check` runs what CI runs: Ruff format and lint (all rules), basedpyright in strict mode,
Astral ty (informational while in beta), and pytest with coverage ≥ 85%. Install the git hooks
once with `uvx prek install`. Renovate keeps dependencies and the lockfile current.

Adding a company is one entry in `watchlist.yaml`. Adding a source is a module in
`src/wl/sources/` returning `PriceBar`, `FxRate` or `Fact` records, plus a branch in
`pipeline.py`. When a company reports a tag the mart misses, `uv run wl unmapped <slug>` lists
the candidates; add the tag to `concepts.py`.

## Milestone 1 simplifications

The plan doc's target stack is dlt → DuckLake → SQLMesh → Dagster. Milestone 1 keeps the same
shape with fewer moving parts, so each piece can be swapped in later without changing the data:

- plain httpx loaders with Pydantic validation instead of dlt
- SQL views in `src/wl/sql/` instead of SQLMesh models
- a CLI instead of Dagster (run it from cron or launchd)
- DuckDB file by default; `WL_STORAGE=ducklake` switches to DuckLake (SQLite catalog + Parquet)

## Verify on the first live run

The tests run against recorded fixtures because the build sandbox has no access to these
APIs. On the first real run, check:

- **filings.xbrl.org filter**: `list_filings` uses `filter[entity.identifier]=<LEI>`. If it
  returns other companies' filings or none, adjust the filter in `sources/esef.py`.
- **IFRS tag coverage**: European companies tag debt and share counts in several ways. Check
  `SELECT * FROM fin_annual` for gaps and extend `concepts.py`.
- **Share counts** are diluted weighted averages from the last annual report, so market caps
  are approximate after buybacks or issuance.
- **DuckLake** needs its DuckDB extensions downloaded once (`INSTALL ducklake`).
