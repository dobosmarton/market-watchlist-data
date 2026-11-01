from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import anyio
import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from wl import live, watchlist_file
from wl.config import load_watchlist
from wl.mcp_server import build_server, lookup
from wl.models import PriceBar
from wl.pipeline import Job, RunReport
from wl.sources import gleif, sec
from wl.store import Store

from .conftest import fixture_bytes, make_company

if TYPE_CHECKING:
    from collections.abc import Collection

    from mcp.types import CallToolResult

    from wl.config import Company, Watchlist
    from wl.settings import Settings


@dataclass
class FakeRefresh:
    """Stand in for ``run_refresh``: store a price bar for each company that should get one."""

    failing_prices: set[str] = field(default_factory=set[str])
    financials_ok: bool = False
    calls: list[list[str]] = field(default_factory=list[list[str]])

    def __call__(
        self,
        settings: Settings,
        watchlist: Watchlist,
        job: Job | None = None,
        *,
        slugs: Collection[str] | None = None,
    ) -> list[RunReport]:
        assert job is None
        chosen = [c.slug for c in watchlist.companies if slugs is None or c.slug in slugs]
        self.calls.append(chosen)
        prices = RunReport(Job.PRICES)
        financials = RunReport(Job.FINANCIALS)
        with Store.open(settings) as store:
            store.sync_companies(watchlist.companies)
            for slug in chosen:
                if slug in self.failing_prices:
                    prices.failed[slug] = "HTTP 404 for the ticker"
                    continue
                prices.rows += store.upsert_prices([_bar(slug)])
                prices.ok.append(slug)
                if self.financials_ok:
                    financials.ok.append(slug)
                else:
                    financials.skipped[slug] = "nothing to fetch"
        return [RunReport(Job.FX), prices, financials]


def _bar(slug: str) -> PriceBar:
    return PriceBar(
        slug=slug, date=date(2026, 10, 27), open=None, high=None, low=None, close=10.0,
        volume=None, ccy="USD", source="twelvedata",
    )  # fmt: skip


def _no_rebuild(_settings: Settings) -> None:
    pass


@pytest.fixture
def refresh(monkeypatch: pytest.MonkeyPatch) -> FakeRefresh:
    fake = FakeRefresh()
    monkeypatch.setattr(live, "run_refresh", fake)
    # the marts are views over the raw tables: new rows show without a (one-second) rebuild
    monkeypatch.setattr(live, "build_marts", _no_rebuild)
    return fake


@pytest.fixture
def listed(served: Settings, watchlist: Watchlist) -> Settings:
    """Write the test watchlist (acme, euro-co) to the settings' watchlist file."""
    entries = ("".join(watchlist_file.render(c)) for c in watchlist.companies)
    served.watchlist_path.write_text("companies:\n" + "\n".join(entries))
    assert load_watchlist(served.watchlist_path) == watchlist
    return served


def new_co(**changes: object) -> Company:
    return make_company("new-co").model_copy(update=changes)


def slugs_in_file(settings: Settings) -> list[str]:
    return [c.slug for c in load_watchlist(settings.watchlist_path).companies]


def stored(settings: Settings, sql: str) -> list[dict[str, object]]:
    with Store.open(settings, read_only=True) as store:
        return store.query(sql)


def exported(settings: Settings) -> list[str]:
    rows = json.loads((settings.export_dir / "valuation.json").read_text())
    return [row["slug"] for row in rows]


# ---------------------------------------------------------------------- live changes
def test_add_writes_the_entry_fetches_and_publishes(listed: Settings, refresh: FakeRefresh) -> None:
    change = live.add_company(listed, new_co())
    assert slugs_in_file(listed) == ["acme", "euro-co", "new-co"]
    assert refresh.calls == [["new-co"]]
    assert change.report[0] == "prices: 1 rows, 1 ok"
    assert exported(listed) == ["acme", "euro-co", "new-co"]
    assert stored(listed, "SELECT price FROM valuation WHERE slug = 'new-co'") == [{"price": 10.0}]


def test_add_without_prices_is_undone(listed: Settings, refresh: FakeRefresh) -> None:
    before = listed.watchlist_path.read_text()
    refresh.failing_prices.add("new-co")
    with pytest.raises(live.ChangeRefusedError, match=r"no prices for new-co \(HTTP 404"):
        live.add_company(listed, new_co())
    assert listed.watchlist_path.read_text() == before
    assert stored(listed, "SELECT slug FROM companies WHERE slug = 'new-co'") == []


def test_force_keeps_an_entry_without_prices(listed: Settings, refresh: FakeRefresh) -> None:
    refresh.failing_prices.add("new-co")
    live.add_company(listed, new_co(), force=True)
    assert "new-co" in slugs_in_file(listed)


def test_update_without_prices_restores_the_entry_and_its_data(
    listed: Settings, refresh: FakeRefresh
) -> None:
    before = listed.watchlist_path.read_text()
    refresh.failing_prices.add("acme")
    acme = load_watchlist(listed.watchlist_path).get("acme")
    with pytest.raises(live.ChangeRefusedError):
        live.update_company(listed, acme.model_copy(update={"name": "Acme Renamed"}))
    assert listed.watchlist_path.read_text() == before
    assert refresh.calls == [["acme"], ["acme"]]  # the change, then the old entry again


def test_remove_drops_the_entry_and_its_data(listed: Settings, refresh: FakeRefresh) -> None:
    live.remove_company(listed, "acme")
    assert refresh.calls == []  # nothing to fetch
    assert slugs_in_file(listed) == ["euro-co"]
    assert stored(listed, "SELECT count(*) AS n FROM raw_prices WHERE slug = 'acme'") == [{"n": 0}]
    assert exported(listed) == ["euro-co"]


def test_changes_need_a_watchlist_file(served: Settings) -> None:
    with pytest.raises(live.ChangeRefusedError, match=r"watchlist\.yaml not found"):
        live.remove_company(served, "acme")


@pytest.mark.parametrize(
    ("company", "hint"),
    [
        (new_co(sources=make_company(financials="manual").sources), "new-co.yaml"),
        (new_co(sources=make_company(financials="esef").sources, cik=None), "add its LEI"),
        (new_co(), "see the report"),
    ],
)
def test_hints_say_how_to_fill_missing_financials(
    listed: Settings, refresh: FakeRefresh, company: Company, hint: str
) -> None:
    assert refresh.financials_ok is False
    (message,) = live.add_company(listed, company).hints
    assert hint in message


def test_no_hints_once_financials_load(listed: Settings, refresh: FakeRefresh) -> None:
    refresh.financials_ok = True
    assert live.add_company(listed, new_co()).hints == []


# ---------------------------------------------------------------------- server tools
def call(settings: Settings, name: str, arguments: dict[str, object]) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(build_server(settings)) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(run)


def test_add_company_tool_returns_the_valuation_row(listed: Settings, refresh: FakeRefresh) -> None:
    entry = new_co().model_dump(exclude_none=True)
    result = call(listed, "add_company", {"company": entry})
    assert refresh.calls == [["new-co"]]
    content = result.structured_content
    assert content is not None
    assert content["slug"] == "new-co"
    (hint,) = content["hints"]  # pyright: ignore[reportGeneralTypeIssues]
    assert str(hint).startswith("its financials did not load")
    valuation = content["valuation"]
    assert isinstance(valuation, dict)
    assert valuation["price"] == 10.0  # pyright: ignore[reportUnknownMemberType]


def test_change_tools_report_refusals(listed: Settings, refresh: FakeRefresh) -> None:
    refresh.failing_prices.add("new-co")
    entry = new_co().model_dump(exclude_none=True)
    added = call(listed, "add_company", {"company": entry})
    assert added.is_error
    unknown = call(listed, "update_company", {"company": entry})
    assert unknown.is_error
    assert "'new-co' is not on the list" in str(unknown.content)


def test_update_and_remove_tools(listed: Settings, refresh: FakeRefresh) -> None:
    euro = load_watchlist(listed.watchlist_path).get("euro-co")
    renamed = euro.model_copy(update={"name": "Euro Renamed"}).model_dump(exclude_none=True)
    updated = call(listed, "update_company", {"company": renamed})
    assert not updated.is_error
    assert load_watchlist(listed.watchlist_path).get("euro-co").name == "Euro Renamed"
    removed = call(listed, "remove_company", {"slug": "acme"})
    assert removed.structured_content == {"removed": "acme"}
    assert refresh.calls == [["euro-co"]]


def test_add_company_rejects_a_malformed_entry(listed: Settings) -> None:
    bad = new_co().model_dump(exclude_none=True) | {"isin": "not-an-isin"}
    assert call(listed, "add_company", {"company": bad}).is_error


# ---------------------------------------------------------------------- lookup
@respx.mock
def test_lookup_combines_sec_and_gleif(settings: Settings) -> None:
    respx.get(sec.TICKERS_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("sec_company_tickers.json"))
    )
    respx.get(url__startswith="https://data.sec.gov/submissions/").mock(
        return_value=httpx.Response(200, content=fixture_bytes("sec_submissions.json"))
    )
    respx.get(gleif.FUZZY_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("gleif_fuzzy.json"))
    )
    lei = "529900D6BF99LW9R2E68"
    respx.get(f"{gleif.URL}/{lei}").mock(
        return_value=httpx.Response(200, content=fixture_bytes("gleif_record.json"))
    )
    respx.get(f"{gleif.URL}/{lei}/isins").mock(
        return_value=httpx.Response(200, content=fixture_bytes("gleif_isins.json"))
    )
    found = lookup(settings, "SAP SE")
    sec_hits, lei_hits = found["sec"], found["gleif"]
    assert isinstance(sec_hits, list)
    assert isinstance(lei_hits, list)
    assert [hit["ticker"] for hit in sec_hits] == ["SAP"]  # pyright: ignore[reportUnknownVariableType]
    assert [hit["lei"] for hit in lei_hits] == [lei]  # pyright: ignore[reportUnknownVariableType]


@respx.mock
def test_lookup_reports_network_errors(settings: Settings) -> None:
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(ToolError, match="lookup failed"):
        lookup(settings, "SAP")
