from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from wl import pipeline
from wl.export import write_exports
from wl.models import Fact, FxRate, PriceBar
from wl.sources import esef, fx
from wl.sources.prices import TwelveDataProvider, YahooProvider

from .conftest import fixture_bytes, make_company, one

if TYPE_CHECKING:
    from pathlib import Path

    from wl.config import Watchlist
    from wl.settings import Settings
    from wl.store import Store


def _fact(tag: str, value: float, *, start: date | None, end: date, unit: str = "USD") -> Fact:
    return Fact(
        slug="acme", taxonomy="us-gaap", tag=tag, unit=unit, period_start=start, period_end=end,
        value=value, form="10-K", filed=date(2025, 2, 20), source="sec", source_url="u",
    )  # fmt: skip


@pytest.fixture
def loaded(store: Store, watchlist: Watchlist) -> Store:
    """ACME with one fiscal year of facts, two price bars, and EUR/USD."""
    pipeline.sync_companies(store, watchlist)
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    store.replace_facts(
        "acme",
        [
            _fact("Revenues", 2_000_000_000, start=y0, end=y1),
            _fact("OperatingIncomeLoss", 200_000_000, start=y0, end=y1),
            _fact("NetIncomeLoss", 100_000_000, start=y0, end=y1),
            _fact("NetCashProvidedByUsedInOperatingActivities", 500_000_000, start=y0, end=y1),
            _fact("PaymentsToAcquirePropertyPlantAndEquipment", 100_000_000, start=y0, end=y1),
            _fact("DepreciationDepletionAndAmortization", 100_000_000, start=y0, end=y1),
            _fact("CashAndCashEquivalentsAtCarryingValue", 500_000_000, start=None, end=y1),
            _fact("LongTermDebtNoncurrent", 1_000_000_000, start=None, end=y1),
            _fact(
                "WeightedAverageNumberOfDilutedSharesOutstanding", 100_000_000,
                start=y0, end=y1, unit="shares",
            ),
            # a quarter and a foreign-currency value must be ignored
            _fact("Revenues", 9e9, start=date(2024, 10, 1), end=y1),
            _fact("Revenues", 9e9, start=y0, end=y1, unit="EUR"),
        ],
    )  # fmt: skip
    store.upsert_prices([
        PriceBar(slug="acme", date=date(2026, 10, d), open=None, high=h, low=lo, close=c,
                 volume=None, ccy="USD", source="twelvedata")
        for d, h, lo, c in ((26, 60.0, 40.0, 45.0), (27, 52.0, 48.0, 50.0))
    ])  # fmt: skip
    store.upsert_fx(
        [FxRate(date=date(2026, 10, 27), base="EUR", quote="USD", rate=1.1, source="t")]
    )
    store.build_marts()
    return store


def test_fin_annual(loaded: Store) -> None:
    row = loaded.con.execute(
        "SELECT fiscal_year, revenue, op_margin_pct, fcf, ebitda, debt, ccy FROM fin_annual"
    ).fetchall()
    assert row == [(2024, 2e9, 10.0, 4e8, 3e8, 1e9, "USD")]


def test_valuation(loaded: Store) -> None:
    (mcap, pe, ev_ebitda, fcf_yield, nd_ebitda, pos) = one(
        loaded,
        "SELECT market_cap, pe_ttm, ev_ebitda, fcf_yield_pct, net_debt_ebitda, wk52_pos "
        "FROM valuation WHERE slug = 'acme'",
    )
    assert mcap == pytest.approx(5e9)  # 50 USD x 100m shares
    assert pe == pytest.approx(50.0)
    assert ev_ebitda == pytest.approx((5e9 + 5e8) / 3e8)
    assert fcf_yield == pytest.approx(8.0)
    assert nd_ebitda == pytest.approx(5e8 / 3e8)
    assert pos == pytest.approx(50.0)  # 50 within 40..60


def test_valuation_converts_quote_to_reporting_currency(store: Store) -> None:
    company = make_company("eu", ccy="NOK", reporting_ccy="USD")
    store.sync_companies([("eu", "EU", "g", "NOK", "USD", 12)])
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    store.replace_facts("eu", [
        f.model_copy(update={"slug": "eu"}) for f in (
            _fact("Revenues", 1e9, start=y0, end=y1),
            _fact("NetIncomeLoss", 1e8, start=y0, end=y1),
            _fact("WeightedAverageNumberOfDilutedSharesOutstanding", 1e8, start=y0, end=y1,
                  unit="shares"),
        )
    ])  # fmt: skip
    store.upsert_prices([
        PriceBar(slug=company.slug, date=date(2026, 10, 27), open=None, high=None, low=None,
                 close=110.0, volume=None, ccy="NOK", source="yahoo")
    ])  # fmt: skip
    store.upsert_fx(
        [
            FxRate(date=date(2026, 10, 27), base="EUR", quote="NOK", rate=11.0, source="t"),
            FxRate(date=date(2026, 10, 27), base="EUR", quote="USD", rate=1.1, source="t"),
        ]
    )
    store.build_marts()
    price_rep, pe = one(store, "SELECT price_rep, pe_ttm FROM valuation WHERE slug = 'eu'")
    assert price_rep == pytest.approx(11.0)  # 110 NOK = 10 EUR = 11 USD
    assert pe == pytest.approx(11.0)


def test_export(loaded: Store, tmp_path: Path) -> None:
    written = write_exports(loaded, tmp_path)
    assert written == {"valuation": 2, "financials": 1}
    valuation = json.loads((tmp_path / "valuation.json").read_text())
    acme = next(r for r in valuation if r["slug"] == "acme")
    assert acme["mcap_bn"] == 5.0
    assert acme["price_date"] == "2026-10-27"
    euro_co = next(r for r in valuation if r["slug"] == "euro-co")
    assert euro_co["price"] is None
    financials = json.loads((tmp_path / "financials.json").read_text())
    assert financials == [{
        "id": "acme-2024", "slug": "acme", "fy": "2024", "estimate": False, "revenue": 2.0,
        "op_income": 0.2, "margin_pct": 10.0, "net_income": 0.1, "fcf": 0.4, "units": "bn",
        "ccy": "USD",
    }]  # fmt: skip


def test_replace_facts_rolls_back_on_error(loaded: Store) -> None:
    bad = _fact("Revenues", 1.0, start=None, end=date(2024, 12, 31))
    object.__setattr__(bad, "value", "not a number")
    with pytest.raises(Exception):  # noqa: B017, PT011
        loaded.replace_facts("acme", [bad])
    count = loaded.con.execute("SELECT count(*) FROM raw_facts WHERE slug = 'acme'").fetchone()
    assert count == (11,)


# ---------------------------------------------------------------- refresh jobs
@respx.mock
def test_refresh_jobs(
    store: Store, watchlist: Watchlist, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = httpx.Client()
    respx.get(url__startswith=fx.BASE_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("frankfurter_range.json"))
    )
    respx.get(TwelveDataProvider.url).mock(
        return_value=httpx.Response(200, content=fixture_bytes("twelvedata_series.json"))
    )
    respx.get(url__startswith="https://data.sec.gov").mock(
        return_value=httpx.Response(200, content=fixture_bytes("sec_companyfacts.json"))
    )
    respx.get(f"{esef.BASE}/api/filings").mock(return_value=httpx.Response(404))

    def no_yahoo(_self: YahooProvider, *_args: object) -> list[PriceBar]:
        return [
            PriceBar(slug="euro-co", date=datetime.now(UTC).date() - timedelta(days=1), open=None,
                     high=None, low=None, close=30.0, volume=None, ccy="EUR", source="yahoo")
        ]  # fmt: skip

    monkeypatch.setattr(YahooProvider, "fetch_daily", no_yahoo)
    pipeline.sync_companies(store, watchlist)

    fx_report = pipeline.refresh_fx(store, client, watchlist, days=5)
    assert fx_report.rows == 8

    sleeps: list[float] = []
    prices = pipeline.refresh_prices(store, client, watchlist, settings, sleep=sleeps.append)
    assert prices.ok == ["acme", "euro-co"]
    assert prices.rows == 3
    assert sleeps == [7.5]

    fin = pipeline.refresh_financials(store, client, watchlist)
    assert fin.ok == ["acme"]
    assert set(fin.failed) == {"euro-co"}
    assert "1 failed (euro-co)" in fin.summary()
    store.build_marts()


def test_refresh_prices_skips_and_reports_failures(
    store: Store, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wl.config import Watchlist  # noqa: PLC0415
    from wl.sources.prices import ProviderError  # noqa: PLC0415

    def broken(_self: YahooProvider, *_args: object) -> list[PriceBar]:
        msg = "yahoo changed"
        raise ProviderError(msg)

    monkeypatch.setattr(YahooProvider, "fetch_daily", broken)
    watchlist = Watchlist(companies=(make_company("a"), make_company("b", prices="yahoo")))
    no_key = settings.model_copy(update={"twelvedata_api_key": None})
    with httpx.Client() as client:
        report = pipeline.refresh_prices(store, client, watchlist, no_key)
    assert report.skipped == {"a": "no twelvedata provider configured"}
    assert report.failed == {"b": "yahoo changed"}
    assert "1 skipped" in report.summary()


def test_refresh_financials_skips(store: Store) -> None:
    from wl.config import Watchlist  # noqa: PLC0415

    watchlist = Watchlist(
        companies=(
            make_company("m", financials="manual"),
            make_company("s", cik=None),
            make_company("e", financials="esef"),
        )
    )
    with httpx.Client() as client:
        report = pipeline.refresh_financials(store, client, watchlist)
    assert report.skipped == {
        "m": "manual source (milestone 2)",
        "s": "missing identifier (cik or lei)",
        "e": "missing identifier (cik or lei)",
    }
