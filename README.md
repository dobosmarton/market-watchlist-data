# watchlist-data

The data pipeline behind the market-analysis watchlist dashboard. For the companies on your
watchlist it collects daily prices, ECB exchange rates and annual reported financials from free public
sources, stores them in DuckDB, computes valuation ratios in SQL, and writes the two JSON files
the dashboard reads.

**Status: milestone 2.** It runs end to end from a CLI against the live sources. See
[Data caveats](#data-caveats) for what the figures can and cannot tell you.

## How it works

```
watchlist.yaml                 companies, identifiers, and the data source for each one
      │
      ▼
src/wl/sources/                Frankfurter (ECB FX), Twelve Data, Yahoo, Euronext, SEC EDGAR,
                               filings.xbrl.org, hand-entered financials/<slug>.yaml
      │  typed records: FxRate, PriceBar, Fact
      ▼
raw_fx, raw_prices,            DuckDB file, or DuckLake (SQLite catalog + Parquet)
raw_facts, companies
      │  concept_map: US-GAAP, IFRS and manual tags → one concept set
      ▼
marts.fin_annual,              SQLMesh view models in src/wl/transform/, built after
marts.price_stats,             every refresh
marts.valuation
      │
      ▼
exports/valuation.json         read by the dashboard
exports/financials.json
```

1. **Refresh** (`wl refresh`) fetches FX, prices and financials company by company. If one
   company fails, it is reported and the others still load. FX and prices are upserted;
   financials are replaced in full for each company, since the sources return full history.
2. **Marts** (`wl build`, also run by `refresh` and `export`). SQLMesh builds the views in the
   `marts` schema. `fin_annual` picks one value per concept and fiscal year (year-long periods only,
   in the reporting currency) and derives debt, operating margin, free cash flow and EBITDA.
   `fin_ttm` turns 10-Q filers' figures into trailing twelve months and keeps the latest fiscal
   year for everyone else. `price_stats` holds the latest close and the 52-week range.
   `valuation` joins `fin_ttm` to the latest price, converting between quote and reporting
   currency with ECB rates.
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

Companies whose filings no XBRL source covers (German issuers, for example) use
`financials: manual`. Type their annual figures into `financials/<slug>.yaml`, also gitignored,
starting from the example; `wl refresh` validates the file against the watchlist entry:

```sh
mkdir -p financials && cp financials.example/sap.yaml financials/<slug>.yaml
```

## Outputs

**`exports/valuation.json`**, one row per company: latest price and date, 52-week low, high and
position in the range, market cap (bn, quote currency), P/E, EV/EBITDA, FCF yield, net
debt/EBITDA, operating margin, whether any debt was tagged (`debt_reported`), and the basis of
the ratios: `ttm` or `annual`, with the end of the period they cover (`fin_period_end`).

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
| `wl gaps` | concepts missing from each company's latest fiscal year |
| `wl companies` | list companies with their identifiers and sources |
| `wl resolve-ids` | look up missing LEIs on GLEIF for ESEF companies |
| `wl unmapped <slug>` | the company's most frequent tags that `concepts.py` does not map yet |

## Configuration

Settings come from environment variables or `.env`, all prefixed `WL_`:

| Variable | Default | Notes |
|---|---|---|
| `WL_SEC_USER_AGENT` | placeholder | SEC asks for `"<name> <email>"` |
| `WL_TWELVEDATA_API_KEY` | unset | needed for US prices; without it those companies are skipped |
| `WL_STORAGE` | `duckdb` | `ducklake` (SQLite catalog with Parquet files) does not work yet, see below |
| `WL_DATA_DIR`, `WL_EXPORT_DIR` | `data`, `exports` | |
| `WL_WATCHLIST_PATH` | `watchlist.yaml` | |
| `WL_MANUAL_DIR` | `financials` | hand-entered figures for `financials: manual` companies |
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
| Euronext prices | live.euronext.com price history download | Amsterdam, Brussels, Dublin, Lisbon, Milan, Oslo, Paris (needs the ISIN) | none |
| Manual financials | `financials/<slug>.yaml`, typed in from the annual report | any company | none |
| LEI lookup | GLEIF | all | none |

Some countries' filings are not on filings.xbrl.org (Germany, for example). Mark those companies
`financials: manual` and enter their figures by hand (see [Your watchlist](#your-watchlist)).
An XBRL figure always wins over a manual one for the same year.

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
  transform/            SQLMesh project: models/ (the marts) and external_models.yaml
  marts.py              builds the marts with SQLMesh
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
  conversion happens only in `transform/models/valuation.sql`, using ECB rates.
- A company's data source is chosen in `watchlist.yaml`, never by branching on slugs in code.
- Tests never touch the network: HTTP is mocked with respx and payloads live in `tests/fixtures/`.

Adding a source means writing a class in `src/wl/sources/` with a `fetch(company)` method that
returns `PriceBar` or `Fact` records, and registering it under its `watchlist.yaml` name in
`build_jobs` (`pipeline.py`). If the company lacks the identifier the source needs, `fetch`
raises `MissingIdentifierError` and the company is skipped.

When a company reports a tag the mart misses, `uv run wl unmapped <slug>` lists the candidates;
add the tag to `concepts.py`.

## Roadmap

The target stack is dlt → DuckLake → SQLMesh → Dagster, adopted piece by piece without
changing the data:

- **Milestone 1:** typed loaders (plain httpx with Pydantic validation instead of dlt), SQL
  views, and a CLI to run from cron or launchd.
- **Milestone 2 (done):** hand-entered figures for `manual` companies, a Euronext price
  provider, trailing twelve months from 10-Qs, and SQLMesh models in place of the SQL views.
- **Next:** make DuckLake storage work (the raw tables' primary keys and upserts need
  replacing), then milestones 3–4: Dagster webserver and daemon for scheduling, and an MCP
  server.

## Data caveats

The first live run confirmed the SEC and ESEF loaders, the LEI-based filings.xbrl.org filter
and the price providers. What remains worth knowing:

- **LEIs**: ESEF companies without an LEI are skipped until you paste in the output of
  `just resolve-ids`. When GLEIF has no ISIN mapping, search GLEIF by the legal name.
- **Gaps**: after a refresh, `just gaps` lists the concepts missing from each company's
  latest year. `wl unmapped <slug>` shows candidate tags to add to `concepts.py`; tags under
  a company's own prefix that many filers share can be mapped with `ANY_TAXONOMY`.
- **Share counts**: most ESEF filers tag diluted EPS but not the share count, so
  `fin_annual` derives it as net income / diluted EPS. Market caps use the diluted weighted
  average of the latest quarter for 10-Q filers and of the last annual report for everyone
  else, so they are approximate after buybacks or issuance.
- **Debt**: companies without borrowings usually tag no debt at all. The valuation counts
  missing debt as zero once cash is reported and exports `debt_reported` so the dashboard
  can mark it; `just gaps` still lists it. Lease liabilities are not counted as debt.
- **filings.xbrl.org lags**: it can take months after publication for an annual report to
  appear there, so some companies' latest year trails the others.
- **Ratio basis**: `pe_ttm` and the other ratios are trailing twelve months only for
  companies that file 10-Qs (`basis = ttm`). Everyone else, including 20-F/40-F, ESEF and
  manual filers, is valued on the last annual report (`basis = annual`). Within a TTM row, a
  figure the 10-Qs do not tag keeps its annual value.
- **DuckLake** storage does not work yet: DuckLake has no primary keys, which the raw tables
  and their upserts rely on. Keep the default `WL_STORAGE=duckdb` for now.
- **DuckDB 1.x**: SQLMesh supports DuckDB 1.x only, so it is pinned below 2. A database file
  written by DuckDB 2 cannot be opened; move it aside and run `just refresh`.
