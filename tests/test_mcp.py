from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
import duckdb
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from wl import mcp_server
from wl.mcp_server import (
    MAX_QUERY_ROWS,
    TABLES,
    VIEWS,
    build_server,
    chart_of,
    describe_tables,
    financials_of,
    gap_list,
    list_companies,
    price_history,
    run_query,
    signals_of,
    ui_html,
    valuation_of,
    view_uri,
)
from wl.pipeline import Job, RunReport
from wl.store import Store

from .conftest import acme_facts, acme_prior_year

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from mcp.types import CallToolResult

    from wl.config import Watchlist
    from wl.settings import Settings

EXAMPLE_WATCHLIST = Path(__file__).parents[1] / "watchlist.example.yaml"
READ_A_FILE = f"SELECT * FROM read_text('{EXAMPLE_WATCHLIST}')"  # noqa: S608


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def rows(value: object) -> list[dict[str, object]]:
    """Narrow a tool result field to its list of rows."""
    assert isinstance(value, list)
    return cast("list[dict[str, object]]", value)


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


# ---------------------------------------------------------------------- tool functions
def test_describe_lists_every_table_with_its_columns(loaded: Store) -> None:
    described = describe_tables(loaded)
    assert set(described) == set(TABLES)
    assert all(table["columns"] for table in described.values())
    columns = described["valuation"]["columns"]
    assert isinstance(columns, dict)
    assert columns["pe_ttm"] == "DOUBLE"


def test_companies_are_the_watchlist_file_entries(settings: Settings) -> None:
    settings.watchlist_path = EXAMPLE_WATCHLIST
    entries = list_companies(settings, None)
    assert [c["slug"] for c in entries][:2] == ["aapl", "msft"]
    assert entries[0]["listing"] == {"mic": "XNGS", "ticker": "AAPL", "ccy": "USD", "yahoo": "AAPL"}
    assert "lei" not in entries[0]  # unset identifiers are left out
    assert [c["slug"] for c in list_companies(settings, "Healthcare")] == ["novo-b"]


def test_companies_need_a_watchlist_file(settings: Settings) -> None:
    settings.watchlist_path = settings.data_dir / "missing.yaml"
    with pytest.raises(ToolError, match=r"missing\.yaml not found"):
        list_companies(settings, None)


def test_valuation_filters_slugs_and_rejects_unknown_ones(loaded: Store) -> None:
    (acme,) = valuation_of(loaded, ["acme"])
    assert (acme["mcap_bn"], acme["pe_ttm"], acme["basis"]) == (5.0, 50.0, "annual")
    assert acme["mcap_eur_bn"] == 4.55  # 5bn USD at 1.1 USD per EUR, rounded
    assert len(valuation_of(loaded, None)) == 2
    with pytest.raises(ToolError, match="unknown slug 'xyz'; known: acme, euro-co"):
        valuation_of(loaded, ["xyz"])


def test_financials_are_in_millions_with_the_latest_figures(loaded: Store) -> None:
    result = financials_of(loaded, "acme", years=10)
    assert (result["ccy"], result["unit"]) == ("USD", mcp_server.MONEY_UNIT)
    (year,) = rows(result["years"])
    assert (year["fiscal_year"], year["period_end"]) == (2024, "2024-12-31")
    assert (year["revenue"], year["fcf"], year["shares_diluted"]) == (2000.0, 400.0, 100.0)
    assert year["op_margin_pct"] == 10.0
    latest = result["latest"]
    assert isinstance(latest, dict)
    assert (latest["basis"], latest["revenue"]) == ("annual", 2000.0)


def test_prices_cover_the_last_days(loaded: Store) -> None:
    result = price_history(loaded, "acme", days=1)
    assert (result["name"], result["ccy"]) == ("ACME", "USD")
    assert result["bars"] == [{"date": "2026-10-27", "close": 50.0, "volume": None}]
    assert len(rows(price_history(loaded, "acme", days=90)["bars"])) == 2


def test_signals_filter_by_company_category_and_age(loaded: Store) -> None:
    loaded.replace_facts("acme", [*acme_facts(), *acme_prior_year()])
    today = date(2026, 11, 3)
    rows = signals_of(loaded, None, 30, None, today)
    assert {(r["name"], r["kind"], r["standing"]) for r in rows} == {
        ("ACME", "revenue_growing", True),
        ("ACME", "margin_down", True),
    }
    assert rows[0]["date"] == "2024-12-31"  # standing conditions come whatever their age
    assert signals_of(loaded, None, 30, "price", today) == []
    assert signals_of(loaded, ["euro-co"], 30, None, today) == []
    with pytest.raises(ToolError, match="unknown slug 'xyz'"):
        signals_of(loaded, ["xyz"], 30, None, today)


def test_gaps_name_companies_without_financials(loaded: Store) -> None:
    assert gap_list(loaded)[-1]["slug"] == "euro-co"


def test_query_runs_one_select_and_caps_the_rows(loaded: Store) -> None:
    result = run_query(loaded, "SELECT slug FROM valuation ORDER BY slug")
    assert result == {"rows": [{"slug": "acme"}, {"slug": "euro-co"}], "truncated": False}
    capped = run_query(loaded, f"SELECT range AS n FROM range({MAX_QUERY_ROWS + 10})")  # noqa: S608
    assert (len(rows(capped["rows"])), capped["truncated"]) == (MAX_QUERY_ROWS, True)


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM raw_fx", "SELECT 1; SELECT 2", "SELEC 1", "SELECT * FROM no_such_table"],
)
def test_query_refuses_anything_but_one_valid_select(loaded: Store, sql: str) -> None:
    with pytest.raises(ToolError):
        run_query(loaded, sql)


def test_read_only_store_reads_marts_but_cannot_write_or_reach_files(served: Settings) -> None:
    with Store.open(served, read_only=True) as store:
        assert len(store.query("SELECT * FROM valuation")) == 2
        with pytest.raises(duckdb.Error):
            store.query("UPDATE companies SET name = 'x'")
        with pytest.raises(duckdb.PermissionException):
            store.query(READ_A_FILE)


# ---------------------------------------------------------------------- server
@pytest.mark.anyio
async def test_server_lists_the_tools_and_answers_from_the_marts(served: Settings) -> None:
    async with Client(build_server(served)) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert tools == {
            "describe", "companies", "valuation", "financials", "prices", "gaps", "query",
            "refresh", "lookup_company", "add_company", "update_company", "remove_company",
            "chart", "signals",
        }  # fmt: skip
        result = await client.call_tool("financials", {"slug": "acme"})
        assert result.structured_content is not None
        assert result.structured_content["ccy"] == "USD"
        listed = await client.call_tool("valuation", {"slugs": ["acme"]})
        assert listed.structured_content is not None
        assert [row["slug"] for row in rows(listed.structured_content["result"])] == ["acme"]
        described = await client.call_tool("describe", {})
        assert described.structured_content is not None
        assert set(described.structured_content) == set(TABLES)
        gaps = await client.call_tool("gaps", {})
        assert not gaps.is_error


@pytest.mark.anyio
async def test_server_reports_tool_errors(served: Settings) -> None:
    async with Client(build_server(served)) as client:
        unknown = await client.call_tool("prices", {"slug": "xyz"})
        assert "unknown slug 'xyz'" in error_text(unknown)
        sealed = await client.call_tool("query", {"sql": READ_A_FILE})
        assert "Permission Error" in error_text(sealed)


@pytest.mark.anyio
async def test_server_without_data_says_to_refresh(settings: Settings) -> None:
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("valuation", {})
        assert "no data yet: run `just refresh`" in error_text(result)


@pytest.mark.anyio
async def test_server_without_marts_says_to_build(settings: Settings) -> None:
    with Store.open(settings):  # raw tables only
        pass
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("valuation", {})
        assert "the marts are missing" in error_text(result)
        assert "run `just build`" in error_text(result)


# holds a write connection to the database file given as the argument until killed
_WRITER = (
    "import duckdb, sys, time; con = duckdb.connect(sys.argv[1]); print('ready', flush=True); "
    "time.sleep(30)"
)


def test_server_reports_a_database_locked_by_another_process(served: Settings) -> None:
    command = [sys.executable, "-c", _WRITER, str(served.db_path)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as writer:  # noqa: S603
        try:
            assert writer.stdout is not None
            assert writer.stdout.readline().strip() == "ready"
            result = anyio.run(call_tool, build_server(served), "gaps")
            assert "database busy" in error_text(result)
        finally:
            writer.kill()


async def call_tool(server: MCPServer, name: str) -> CallToolResult:
    async with Client(server) as client:
        return await client.call_tool(name, {})


@pytest.mark.anyio
async def test_refresh_runs_the_pipeline_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[Job] | None] = []

    def fake_refresh(
        _settings: Settings, watchlist: Watchlist, jobs: list[Job] | None
    ) -> list[RunReport]:
        calls.append(jobs)
        return [RunReport(Job.PRICES, rows=2, ok=[c.slug for c in watchlist.companies[:1]])]

    monkeypatch.setattr(mcp_server, "run_refresh", fake_refresh)
    settings.watchlist_path = EXAMPLE_WATCHLIST
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("refresh", {"what": "prices"})
    assert calls == [[Job.PRICES]]
    assert result.structured_content == {"result": ["prices: 2 rows, 1 ok"]}


@pytest.mark.anyio
async def test_refresh_needs_a_watchlist(settings: Settings) -> None:
    settings.watchlist_path = settings.data_dir / "missing.yaml"
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("refresh", {})
    assert "missing.yaml not found" in error_text(result)


# ---------------------------------------------------------------------- charts (MCP Apps)
SCATTER: dict[str, object] = {
    "mark": "point",
    "encoding": {"x": {"field": "slug"}, "y": {"field": "pe_ttm", "type": "quantitative"}},
}


def test_chart_draws_only_the_queried_rows(loaded: Store) -> None:
    sneaky: dict[str, object] = {
        **SCATTER,
        "data": {"url": "https://example.com/x.csv"},
        "datasets": {"a": [{"slug": "made-up"}]},
    }
    result = chart_of(loaded, "SELECT slug, pe_ttm FROM valuation ORDER BY slug", sneaky, "P/E")
    spec = result["spec"]
    assert isinstance(spec, dict)
    assert spec["data"] == {"values": result["rows"]}
    assert "datasets" not in spec
    assert [row["slug"] for row in rows(result["rows"])] == ["acme", "euro-co"]
    assert (result["title"], result["truncated"]) == ("P/E", False)


@pytest.mark.parametrize(
    ("sql", "spec", "message"),
    [
        ("SELECT 1 AS n", {"encoding": {}}, "needs a Vega-Lite mark"),
        ("DELETE FROM raw_fx", SCATTER, "single SELECT"),
    ],
)
def test_chart_refuses_bad_specs_and_statements(
    loaded: Store, sql: str, spec: dict[str, object], message: str
) -> None:
    with pytest.raises(ToolError, match=message):
        chart_of(loaded, sql, spec, None)


def test_the_page_is_told_which_view_to_draw(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text('<!doctype html>\n<html lang="en"><div id="root"></div></html>')
    for view in VIEWS:
        assert f'<html lang="en" data-view="{view}">' in ui_html(view, page)
    page.write_text("<!doctype html>\n<html><div id='root'></div></html>")
    with pytest.raises(ValueError, match="exactly one"):
        ui_html("prices", page)


def test_an_unbuilt_page_says_how_to_build_it(tmp_path: Path) -> None:
    assert "just ui" in ui_html("prices", tmp_path / "index.html")


@pytest.mark.anyio
async def test_server_binds_the_views_to_their_tools(served: Settings) -> None:
    async with Client(build_server(served)) as client:
        tools = {tool.name: tool.meta or {} for tool in (await client.list_tools()).tools}
        bound = {name: meta["ui"]["resourceUri"] for name, meta in tools.items() if "ui" in meta}
        assert bound == {view: view_uri(view) for view in VIEWS}
        resource = (await client.read_resource(view_uri("valuation"))).contents[0]
        assert resource.mime_type == "text/html;profile=mcp-app"
        assert not resource.meta  # no CSP domains: the page cannot load or fetch anything
        drawn = await client.call_tool(
            "chart", {"sql": "SELECT slug FROM companies", "spec": SCATTER}
        )
        assert drawn.structured_content is not None
        assert len(rows(drawn.structured_content["rows"])) == 2
