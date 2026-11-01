"""MCP server: Claude reads the watchlist marts, and refreshes them when asked.

The tools are plain functions over a ``Store``. ``build_server`` wraps each one so that every
call opens its own read-only connection and closes it again, which leaves the database file
free for ``wl refresh`` in another process.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import duckdb
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from wl.config import load_watchlist
from wl.export import clean, valuation_rows
from wl.pipeline import Job, run_refresh
from wl.store import Store

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
    "except price and market_cap_quote (quote currency); full units",
}

_INSTRUCTIONS = """\
A personal stock watchlist: prices, ECB FX rates, and figures from annual reports (SEC, ESEF
or hand-entered) for a fixed list of companies, identified by slug.
Start with `companies` or `valuation`. `financials` gives one company's history in millions
of its reporting currency. For anything else, call `describe` and then `query` with DuckDB SQL.
Data is as fresh as the last refresh: check price_date and fin_period_end before calling it
current. Call `refresh` only when the user asks for fresh data."""

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_REFRESH = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
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


def list_companies(store: Store, group: str | None) -> list[Row]:
    rows = store.query(
        'SELECT slug, name, grp AS "group", quote_ccy, reporting_ccy, fiscal_year_end_month '
        "FROM companies ORDER BY slug"
    )
    return [row for row in rows if group is None or row["group"] == group]


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
    (company,) = store.query("SELECT quote_ccy FROM companies WHERE slug = ?", [slug])
    return {
        "slug": slug,
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
def build_server(settings: Settings) -> MCPServer:  # noqa: C901  (one small wrapper per tool)
    # MCP reads the return annotations: a spelled-out dict comes back as the structured result
    # itself, anything else (lists, aliases) wrapped as {"result": ...}
    server = MCPServer("watchlist", instructions=_INSTRUCTIONS)
    # tools run on worker threads; DuckDB allows one configuration per file and process
    lock = threading.Lock()

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

    @server.tool(annotations=_READ)
    def describe() -> dict[str, dict[str, object]]:
        """List the tables and views `query` can read, with grain, units and column types."""
        return read(describe_tables)

    @server.tool(annotations=_READ)
    def companies(group: str | None = None) -> list[dict[str, object]]:
        """List the watchlist companies (slug, name, group, currencies), optionally one group."""
        return read(lambda store: list_companies(store, group))

    @server.tool(annotations=_READ)
    def valuation(slugs: list[str] | None = None) -> list[dict[str, object]]:
        """Price, 52-week range, market cap (bn, quote currency) and valuation ratios.

        Ratios use trailing twelve months where basis is 'ttm', else the last annual report
        (fin_period_end). nd_ebitda below zero means net cash; debt_reported false means no
        borrowings were tagged and debt counts as zero.
        """
        return read(lambda store: valuation_of(store, slugs))

    @server.tool(annotations=_READ)
    def financials(slug: str, years: int = 10) -> dict[str, object]:
        """One company's annual figures, oldest first, in millions of its reporting currency.

        `latest` holds the trailing-twelve-month figures (or the latest year) used by
        `valuation`. source_url links each year's filing.
        """
        return read(lambda store: financials_of(store, slug, years))

    @server.tool(annotations=_READ)
    def prices(slug: str, days: int = 90) -> dict[str, object]:
        """Daily closes and volumes for the last `days` calendar days, in the quote currency."""
        days = max(1, min(days, settings.price_history_days))
        return read(lambda store: price_history(store, slug, days))

    @server.tool(annotations=_READ)
    def gaps() -> list[dict[str, object]]:
        """Companies whose latest fiscal year misses figures (period_end null: no years at all)."""
        return read(gap_list)

    @server.tool(annotations=_READ)
    def query(sql: str) -> dict[str, object]:
        """Run one read-only DuckDB SELECT over the tables `describe` lists (max 500 rows).

        Unqualified names resolve to the raw tables and the marts. Money is in full units of
        the reporting currency unless a column says otherwise.
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
