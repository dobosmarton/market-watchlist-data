"""MCP server: Claude reads the watchlist marts, changes the watchlist, and refreshes on request.

The read tools are plain functions over a ``Store``. ``build_server`` wraps each one so that
every call opens its own read-only connection and closes it again, which leaves the database
file free for ``wl refresh`` in another process. Watchlist changes go through ``wl.live``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import httpx
from mcp.server import MCPServer
from mcp.server.apps import Apps
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from wl import live
from wl.config import Company, load_watchlist
from wl.export import clean, valuation_rows
from wl.http import make_client
from wl.pipeline import Job, run_refresh
from wl.sources import gleif, sec
from wl.store import Store
from wl.watchlist_file import WatchlistEditError

if TYPE_CHECKING:
    from collections.abc import Callable

    from wl.settings import Settings
    from wl.store import Row

_MILLION = 1e6
MAX_QUERY_ROWS = 500
MONEY_UNIT = "millions of ccy (shares in millions; EPS per share; margin in %)"

# money and share counts the financials tool returns in millions; ratios and EPS stay as they are
_IN_MILLIONS = (
    "revenue", "op_income", "net_income", "cfo", "capex", "fcf", "dna", "ebitda", "cash", "debt",
    "shares_diluted",
)  # fmt: skip
_FINANCIAL_COLUMNS = ", ".join((*_IN_MILLIONS, "op_margin_pct"))

TABLES = {
    "companies": "one row per watchlist company (grp is the group)",
    "raw_prices": "daily bars per company in the quote currency",
    "price_stats": "one row per company: latest close and 52-week range, quote currency",
    "fx_latest": "latest ECB rate per currency as units per 1 EUR",
    "fin_annual_long": "one row per company, fiscal year end and concept: the value chosen",
    "fin_annual": "one row per company and fiscal year, reporting currency, full units",
    "fin_ttm": "one row per company: trailing twelve months for 10-Q filers (basis 'ttm'), "
    "otherwise the latest fiscal year (basis 'annual'); reporting currency, full units",
    "fin_gaps": "companies whose latest fiscal year misses concepts (missing is a list)",
    "valuation": "one row per company: ratios on fin_ttm; money in the reporting currency "
    "except price and market_cap_quote (quote currency) and market_cap_eur (EUR); full units",
}

# MCP Apps views: one self-contained page, built from ui/ by `just ui`, drawn in hosts that
# render apps (Claude Desktop); other clients get the same results as text
UI_PAGE = Path(__file__).parent / "ui" / "index.html"
VIEWS = ("prices", "financials", "valuation", "chart")
_HTML_TAG = '<html lang="en">'
_NOT_BUILT = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="color-scheme" content="light dark">
<p style="font: 14px system-ui, sans-serif">The chart views are not built: run
<code>just ui</code> in the watchlist-data folder, then restart the app.</p></html>
"""
_SPEC_ROOTS = ("mark", "layer", "concat", "hconcat", "vconcat", "facet", "repeat")

_INSTRUCTIONS = """\
A personal stock watchlist: prices, ECB FX rates, and figures from annual reports (SEC, ESEF
or hand-entered) for a list of companies, identified by slug.
Start with `companies` or `valuation`. `financials` gives one company's history in millions
of its reporting currency. For anything else, call `describe` and then `query` with DuckDB SQL.
To add a company, find its identifiers with `lookup_company`, then call `add_company`;
`update_company` and `remove_company` change the list. Change the list only when asked.
Data is as fresh as the last refresh: check price_date and fin_period_end before calling it
current. Call `refresh` only when the user asks for fresh data.
Charts: `prices` draws a company's price chart, `financials` its revenue, margin and earnings,
`valuation` a ratio comparison across companies (in hosts that show apps, such as Claude
Desktop). Use them whenever they fit; `chart` is only for charts none of them draws."""

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_REFRESH = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
_LOOKUP = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_EDIT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
_REMOVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


# ---------------------------------------------------------------------- tool functions
def describe_tables(store: Store) -> dict[str, Row]:
    """Each table's grain and units, and its columns with their types."""
    rows = store.query(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema IN ('main', 'marts') AND list_contains(?, table_name) "
        "ORDER BY table_name, ordinal_position",
        [list(TABLES)],
    )
    described: dict[str, Row] = {}
    for name, note in TABLES.items():
        columns = {r["column_name"]: r["data_type"] for r in rows if r["table_name"] == name}
        described[name] = {"note": note, "columns": columns}
    return described


def list_companies(settings: Settings, group: str | None) -> list[Row]:
    """List the watchlist entries as they stand in ``watchlist.yaml``."""
    if not settings.watchlist_path.exists():
        msg = f"{settings.watchlist_path} not found; start from watchlist.example.yaml"
        raise ToolError(msg)
    companies = load_watchlist(settings.watchlist_path).companies
    return [c.model_dump(exclude_none=True) for c in companies if group in (None, c.group)]


def lookup(settings: Settings, query: str) -> Row:
    """Candidates from the SEC ticker list and GLEIF for filling in a watchlist entry."""
    try:
        with make_client(settings) as client:
            sec_matches = sec.search_tickers(client, query)
            lei_matches = gleif.search_entities(client, query)
    except httpx.HTTPError as exc:
        msg = f"lookup failed: {exc}"
        raise ToolError(msg) from exc
    return {
        "sec": [match.model_dump() for match in sec_matches],
        "gleif": [match.model_dump() for match in lei_matches],
    }


def valuation_of(store: Store, slugs: list[str] | None) -> list[Row]:
    for slug in slugs or ():
        _check_slug(store, slug)
    return [row for row in valuation_rows(store) if slugs is None or row["slug"] in slugs]


def financials_of(store: Store, slug: str, years: int) -> Row:
    _check_slug(store, slug)
    annual = store.query(
        f"SELECT fiscal_year, period_end, {_FINANCIAL_COLUMNS}, eps_diluted, source_url "  # noqa: S608
        "FROM fin_annual WHERE slug = ? ORDER BY period_end DESC LIMIT ?",
        [slug, years],
    )
    latest = store.query(
        f"SELECT basis, period_end, {_FINANCIAL_COLUMNS} FROM fin_ttm WHERE slug = ?",  # noqa: S608
        [slug],
    )
    return {
        "slug": slug,
        "ccy": _reporting_ccy(store, slug),
        "unit": MONEY_UNIT,
        "years": [_in_millions(row) for row in reversed(annual)],
        "latest": _in_millions(latest[0]) if latest else None,
    }


def price_history(store: Store, slug: str, days: int) -> Row:
    _check_slug(store, slug)
    bars = store.query(
        "SELECT date, close, volume FROM raw_prices WHERE slug = ? "
        "AND date > (SELECT max(date) FROM raw_prices WHERE slug = ?) - ?::INTEGER "
        "ORDER BY date",
        [slug, slug, days],
    )
    (company,) = store.query("SELECT name, quote_ccy FROM companies WHERE slug = ?", [slug])
    return {
        "slug": slug,
        "name": company["name"],
        "ccy": company["quote_ccy"],
        "bars": [_cleaned(bar) for bar in bars],
    }


def gap_list(store: Store) -> list[Row]:
    return [_cleaned(row) for row in store.gaps()]


def run_query(store: Store, sql: str) -> Row:
    try:
        rows, truncated = store.select(sql, MAX_QUERY_ROWS)
    except (ValueError, duckdb.Error) as exc:
        raise ToolError(str(exc)) from exc
    return {"rows": [_cleaned(row) for row in rows], "truncated": truncated}


def chart_of(store: Store, sql: str, spec: dict[str, object], title: str | None) -> Row:
    """Run the query and put its rows into the spec, so a chart only ever shows queried data."""
    if not any(key in spec for key in _SPEC_ROOTS):
        msg = f"spec needs a Vega-Lite mark or composition ({', '.join(_SPEC_ROOTS)})"
        raise ToolError(msg)
    result = run_query(store, sql)
    drawn = {key: value for key, value in spec.items() if key not in {"data", "datasets"}}
    return {**result, "title": title, "spec": {**drawn, "data": {"values": result["rows"]}}}


def ui_html(view: str, page: Path = UI_PAGE) -> str:
    """Return the built page, told which view to draw, or a note to build it when it is missing."""
    if not page.exists():
        return _NOT_BUILT
    html = page.read_text(encoding="utf-8")
    if html.count(_HTML_TAG) != 1:
        msg = f"{page} must hold exactly one {_HTML_TAG}"
        raise ValueError(msg)
    return html.replace(_HTML_TAG, f'<html lang="en" data-view="{view}">')


def view_uri(view: str) -> str:
    return f"ui://watchlist/{view}"


def refresh_data(settings: Settings, job: Job | None) -> list[str]:
    if not settings.watchlist_path.exists():
        msg = f"{settings.watchlist_path} not found"
        raise ToolError(msg)
    reports = run_refresh(settings, load_watchlist(settings.watchlist_path), job)
    return [line for report in reports for line in report.lines()]


def _check_slug(store: Store, slug: str) -> None:
    known = [row["slug"] for row in store.query("SELECT slug FROM companies ORDER BY slug")]
    if slug not in known:
        msg = f"unknown slug {slug!r}; known: {', '.join(map(str, known))}"
        raise ToolError(msg)


def _reporting_ccy(store: Store, slug: str) -> object:
    (row,) = store.query("SELECT reporting_ccy FROM companies WHERE slug = ?", [slug])
    return row["reporting_ccy"]


def _in_millions(row: Row) -> Row:
    return {
        key: clean(value / _MILLION if key in _IN_MILLIONS and isinstance(value, float) else value)
        for key, value in row.items()
    }


def _cleaned(row: Row) -> Row:
    return {key: clean(value) for key, value in row.items()}


# ---------------------------------------------------------------------- server
def build_server(settings: Settings) -> MCPServer:  # noqa: C901, PLR0915  (a small wrapper per tool)
    # MCP reads the return annotations: a spelled-out dict comes back as the structured result
    # itself, anything else (lists, aliases) wrapped as {"result": ...}
    # tools run on worker threads; DuckDB allows one configuration per file and process
    lock = threading.RLock()  # re-entrant: a change reads its result back under the lock
    apps = Apps()
    for view in VIEWS:
        # no CSP domains: the page inlines its scripts and cannot fetch or load anything
        apps.add_html_resource(view_uri(view), ui_html(view), name=f"{view} view")

    def read[T](tool: Callable[[Store], T]) -> T:
        with lock:
            try:
                with Store.open(settings, read_only=True) as store:
                    return tool(store)
            except duckdb.CatalogException as exc:
                msg = f"the marts are missing ({exc}); run `just build`"
                raise ToolError(msg) from exc
            except duckdb.IOException as exc:
                if not settings.db_path.exists():
                    msg = "no data yet: run `just refresh`"
                    raise ToolError(msg) from exc
                msg = "database busy (is a refresh running?); try again shortly"
                raise ToolError(msg) from exc

    @apps.tool(resource_uri=view_uri("valuation"), annotations=_READ)
    def valuation(slugs: list[str] | None = None) -> list[dict[str, object]]:
        """Price, 52-week range, market cap and valuation ratios.

        Also draws them: a sortable table and one ratio as bars across the companies. Use it to
        compare or chart valuations.

        Market cap is in billions: mcap_bn in the quote currency, mcap_eur_bn in EUR (compare
        sizes with this one). Ratios use trailing twelve months where basis is 'ttm', else the
        last annual report (fin_period_end). A P/E or EV/EBITDA below zero (a loss, negative
        EBITDA) is not meaningful. nd_ebitda below zero means net cash; it is empty when
        EBITDA is not positive. debt_reported false means no borrowings were tagged and debt
        counts as zero.
        """
        return read(lambda store: valuation_of(store, slugs))

    @apps.tool(resource_uri=view_uri("financials"), annotations=_READ)
    def financials(slug: str, years: int = 10) -> dict[str, object]:
        """One company's annual figures, oldest first, in millions of its reporting currency.

        Also draws them: revenue bars with the operating margin, and net income against FCF,
        with a TTM bar. Use it to chart a company's revenue, margins or earnings.

        `latest` holds the trailing-twelve-month figures (or the latest year) used by
        `valuation`. source_url links each year's filing.
        """
        return read(lambda store: financials_of(store, slug, years))

    @apps.tool(resource_uri=view_uri("prices"), annotations=_READ)
    def prices(slug: str, days: int = 365) -> dict[str, object]:
        """Daily closes and volumes for the last `days` calendar days, in the quote currency.

        Also draws them as a price chart with 1M/3M/1Y range buttons. Use it to chart or show
        a company's price; the default year covers the range buttons (history is ~400 days).
        """
        days = max(1, min(days, settings.price_history_days))
        return read(lambda store: price_history(store, slug, days))

    @apps.tool(resource_uri=view_uri("chart"), annotations=_READ)
    def chart(sql: str, spec: dict[str, object], title: str | None = None) -> dict[str, object]:
        """Draw a custom chart: run one SELECT (as in `query`) and plot it with a Vega-Lite spec.

        Not for one company's price over time (use `prices`, with `days` for the period), its
        revenue, margins or earnings (use `financials`), or one ratio across companies (use
        `valuation`): those draw their own charts. Use it for anything else, such as a scatter
        of two ratios or several companies' prices together.
        `spec` is a Vega-Lite v6 spec without data: the query's rows become its data, so use
        the query's column names as fields. Take column names from `describe`; some differ from
        the `valuation` tool's keys (fcf_yield_pct, net_debt_ebitda, grp, market_cap_eur).
        Money in the marts is in full units of each company's reporting currency (market_cap_eur
        is in EUR), so compare ratios across companies, not amounts. Ratios have outliers
        (negative or very high P/E): filter them in the SQL so the rest stays readable. Draw
        lines straight, without `interpolate`: smoothing shows values that never happened.
        """
        return read(lambda store: chart_of(store, sql, spec, title))

    server = MCPServer("watchlist", instructions=_INSTRUCTIONS, extensions=[apps])

    @server.tool(annotations=_READ)
    def describe() -> dict[str, dict[str, object]]:
        """List the tables and views `query` can read, with grain, units and column types."""
        return read(describe_tables)

    def change[T](apply: Callable[[], T]) -> T:
        with lock:
            try:
                return apply()
            except (live.ChangeRefusedError, WatchlistEditError) as exc:
                raise ToolError(str(exc)) from exc

    def changed(slug: str, result: live.Change) -> dict[str, object]:
        (row,) = read(lambda store: valuation_of(store, [slug]))
        return {"slug": slug, "report": result.report, "hints": result.hints, "valuation": row}

    @server.tool(annotations=_READ)
    def companies(group: str | None = None) -> list[dict[str, object]]:
        """List the watchlist entries (identifiers, listing, sources), optionally one group."""
        with lock:
            return list_companies(settings, group)

    @server.tool(annotations=_LOOKUP)
    def lookup_company(query: str) -> dict[str, object]:
        """Search the SEC ticker list (by ticker or name) and GLEIF (by legal name).

        Use it to fill in a new entry: SEC gives cik, ticker and fiscal_year_end_month; GLEIF
        gives the LEI, country and ISINs (bonds included: pick the share ISIN, usually the one
        with the company's country prefix). Choose prices: twelvedata for US listings, euronext
        for Euronext markets (mic XAMS, XBRU, XDUB, XLIS, XMSM, XMIL, XOSL, XPAR), else yahoo.
        Choose financials: sec with a cik, esef for EU/EEA filers with an LEI, manual for
        companies on neither (German issuers, for example). Ask the user when unsure.
        """
        return lookup(settings, query)

    @server.tool(annotations=_EDIT)
    def add_company(company: Company, force: bool = False) -> dict[str, object]:  # noqa: FBT001, FBT002
        """Add a company to the watchlist, fetch its prices and financials, and publish.

        Takes a minute or so. If its prices cannot be fetched the add is undone and the reason
        returned (usually a wrong ticker, yahoo symbol or isin); force=true keeps it anyway.
        Returns the refresh report, hints for anything still missing, and its valuation row.
        """
        return changed(
            company.slug, change(lambda: live.add_company(settings, company, force=force))
        )

    @server.tool(annotations=_EDIT)
    def update_company(company: Company, force: bool = False) -> dict[str, object]:  # noqa: FBT001, FBT002
        """Replace the entry with the same slug (e.g. add an LEI, switch the price source).

        Fetches the company's data again; the change is undone if its prices fail.
        Get the current entry from `companies` first and change only what needs changing.
        """
        return changed(
            company.slug, change(lambda: live.update_company(settings, company, force=force))
        )

    @server.tool(annotations=_REMOVE)
    def remove_company(slug: str) -> dict[str, object]:
        """Take a company off the watchlist and drop its stored prices and financials.

        Only call it when the user asks. watchlist.yaml.bak keeps the previous list.
        """
        change(lambda: live.remove_company(settings, slug))
        return {"removed": slug}

    @server.tool(annotations=_READ)
    def gaps() -> list[dict[str, object]]:
        """Companies whose latest fiscal year misses figures (period_end null: no years at all)."""
        return read(gap_list)

    @server.tool(annotations=_READ)
    def query(sql: str) -> dict[str, object]:
        """Run one read-only DuckDB SELECT over the tables `describe` lists (max 500 rows).

        Unqualified names resolve to the raw tables and the marts. Take column names from
        `describe`; some differ from the `valuation` tool's keys (fcf_yield_pct,
        net_debt_ebitda, grp). Money is in full units of the reporting currency unless a column
        says otherwise.
        """
        return read(lambda store: run_query(store, sql))

    @server.tool(annotations=_REFRESH)
    def refresh(what: Job | None = None) -> list[str]:
        """Fetch fresh fx, prices and/or financials over the network and rebuild the marts.

        Takes about a minute and rewrites the local data. Only call it when the user asks for
        fresh data. Leave `what` empty to refresh everything.
        """
        with lock:
            return refresh_data(settings, what)

    return server
