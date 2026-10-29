# watchlist-data

The data pipeline behind the market-analysis watchlist dashboard. For the companies on your
watchlist it collects daily prices, ECB exchange rates and annual reported financials from free public
sources, stores them in DuckDB, computes valuation ratios in SQL, and writes the two JSON files
the dashboard reads.

**Status: milestone 1.** It runs end to end from a CLI, but so far only against recorded test
fixtures. See [Verify on the first live run](#verify-on-the-first-live-run).

## How it works

```
watchlist.yaml                 companies, identifiers, and the data source for each one
      │
      ▼
src/wl/sources/                Frankfurter (ECB FX), Twelve Data, Yahoo, SEC EDGAR, filings.xbrl.org
      │  typed records: FxRate, PriceBar, Fact
      ▼
raw_fx, raw_prices,            DuckDB file, or DuckLake (SQLite catalog + Parquet)
raw_facts, companies
      │  concept_map: US-GAAP and IFRS tags → 11 concepts
      ▼
fin_annual, price_stats,       SQL views in src/wl/sql/, rebuilt after every refresh
valuation
      │
      ▼
exports/valuation.json         read by the dashboard
exports/financials.json
```

1. **Refresh** (`wl refresh`) fetches FX, prices and financials company by company. If one
   company fails, it is reported and the others still load. FX and prices are upserted;
   financials are replaced in full for each company, since the sources return full history.
2. **Marts.** `fin_annual` picks one value per concept and fiscal year (year-long periods only,
   in the reporting currency) and derives debt, operating margin, free cash flow and EBITDA.
   `price_stats` holds the latest close and the 52-week range. `valuation` joins the latest
   annual figures to the latest price, converting between quote and reporting currency with
   ECB rates.
3. **Export** (`wl export`) writes the marts out under the column names the dashboard expects.

## Your watchlist

The companies live in `watchlist.yaml`, which is gitignored so everyone keeps their own list.
Start from the example, which has a few well-known companies and uses every source once:

```sh
cp watchlist.example.yaml watchlist.yaml
```

Each entry gives the company's identifiers (ISIN, plus a CIK for SEC filers or an LEI for ESEF
filers), its listing and currencies, and which source to use for prices and for financials.
Adding a company is one entry. The comments at the top of the example explain each field.

## Outputs

**`exports/valuation.json`**, one row per company: latest price and date, 52-week low, high and
position in the range, market cap (bn, quote currency), P/E, EV/EBITDA, FCF yield, net
debt/EBITDA, operating margin, and the fiscal period end the ratios are based on.

**`exports/financials.json`**, one row per company and fiscal year: revenue, operating income,
operating margin, net income and free cash flow, in the reporting currency, scaled to bn or m
per company.

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
watchlist is not baked into the image; compose mounts your `watchlist.yaml`. The same image runs
on a Mac and on a VPS.

## Commands

| Command | What it does |
|---|---|
| `wl refresh [all\|fx\|prices\|financials]` | fetch data, then rebuild the marts |
| `wl export` | write the dashboard JSON files |
| `wl companies` | list companies with their identifiers and sources |
| `wl resolve-ids` | look up missing LEIs on GLEIF for ESEF companies |
| `wl unmapped <slug>` | the company's most frequent tags that `concepts.py` does not map yet |

## Configuration

Settings come from environment variables or `.env`, all prefixed `WL_`:

| Variable | Default | Notes |
|---|---|---|
| `WL_SEC_USER_AGENT` | placeholder | SEC asks for `"<name> <email>"` |
| `WL_TWELVEDATA_API_KEY` | unset | needed for US prices; without it those companies are skipped |
| `WL_STORAGE` | `duckdb` | `ducklake` switches to a SQLite catalog with Parquet files |
| `WL_DATA_DIR`, `WL_EXPORT_DIR` | `data`, `exports` | |
| `WL_WATCHLIST_PATH` | `watchlist.yaml` | |
| `WL_PRICE_HISTORY_DAYS` | `400` | days of price and FX history fetched on each refresh |
| `WL_HTTP_TIMEOUT_S` | `30` | |

## Sources

| Data | Source | Coverage | Key |
|---|---|---|---|
| FX | ECB reference rates via Frankfurter | all currencies on the watchlist | none |
| SEC financials | SEC EDGAR company facts (10-K, 20-F, 40-F) | SEC filers, including foreign issuers filing 20-F or 40-F | none, User-Agent required |
| ESEF financials | ESEF filings via filings.xbrl.org | EU/EEA issuers that file in ESEF | none |
| US prices | Twelve Data `time_series` | US listings | free API key (8 req/min, 800/day) |
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
  models.py             records passed from sources to storage: FxRate, PriceBar, Fact
  sources/              one module per source; each returns typed records
    base.py             the Provider protocol every per-company source implements
  http.py               shared httpx client with retries
  store.py              DuckDB / DuckLake storage and raw tables
  concepts.py           US-GAAP and IFRS tags mapped onto one concept set
  sql/                  marts: fin_annual, price_stats, valuation
  pipeline.py           refresh jobs and their wiring; one company failing never stops the rest
  export.py             dashboard JSON
  cli.py                `wl` command
tests/                  offline tests with recorded fixtures
```

## Development

`just check` runs what CI runs: Ruff format and lint (all rules), basedpyright in strict mode,
Astral ty (informational while in beta), and pytest with coverage ≥ 85%. CI also builds the
Docker image. Install the git hooks once with `uvx prek install`. Renovate keeps dependencies
and the lockfile current.

A few rules hold the design together:

- Sources return typed records from `wl.models`; storage and SQL never see raw API payloads.
- Money stays in the company's reporting currency in `raw_facts` and `fin_annual`. Currency
  conversion happens only in `sql/valuation.sql`, using ECB rates.
- A company's data source is chosen in `watchlist.yaml`, never by branching on slugs in code.
- Tests never touch the network: HTTP is mocked with respx and payloads live in `tests/fixtures/`.

Adding a source means writing a class in `src/wl/sources/` with a `fetch(company)` method that
returns `PriceBar` or `Fact` records, and registering it under its `watchlist.yaml` name in
`build_jobs` (`pipeline.py`). If the company lacks the identifier the source needs, `fetch`
raises `MissingIdentifierError` and the company is skipped.

When a company reports a tag the mart misses, `uv run wl unmapped <slug>` lists the candidates;
add the tag to `concepts.py`.

## Roadmap

The target stack is dlt → DuckLake → SQLMesh → Dagster. Milestone 1 keeps the same shape with
fewer moving parts, so each piece can be swapped in later without changing the data:

- plain httpx loaders with Pydantic validation instead of dlt
- SQL views in `src/wl/sql/` instead of SQLMesh models
- a CLI instead of Dagster (run it from cron or launchd)
- DuckDB file by default; `WL_STORAGE=ducklake` switches to DuckLake

Next up:

- **Milestone 2:** annual-report extraction for the `manual` companies, a Euronext price
  provider, quarterly figures, and SQLMesh models in place of the SQL views.
- **Milestones 3–4:** Dagster webserver and daemon for scheduling, and an MCP server.

## Verify on the first live run

The tests run against recorded fixtures because the build sandbox had no access to these
APIs. On the first real run, check:

- **LEIs**: ESEF companies without an LEI are skipped until you paste in the output of
  `just resolve-ids`.
- **filings.xbrl.org filter**: `list_filings` uses `filter[entity.identifier]=<LEI>`. If it
  returns other companies' filings or none, adjust the filter in `sources/esef.py`.
- **IFRS tag coverage**: European companies tag debt and share counts in several ways. Check
  `SELECT * FROM fin_annual` for gaps and extend `concepts.py`.
- **Ratios use the latest annual report**, not trailing twelve months, even though the
  dashboard column is called `pe_ttm`.
- **Share counts** are diluted weighted averages from the last annual report, so market caps
  are approximate after buybacks or issuance.
- **DuckLake** needs its DuckDB extensions downloaded once (`INSTALL ducklake`).
