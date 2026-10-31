from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest

from wl.config import Watchlist
from wl.export import write_exports
from wl.models import Fact, FxRate, PriceBar
from wl.pipeline import CompanyJob, FxJob, Job, build_jobs
from wl.sources.base import MissingIdentifierError, ProviderError

from .conftest import make_company, one

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from wl.config import Company
    from wl.settings import Settings
    from wl.sources.base import Provider
    from wl.store import Store


def _fact(tag: str, value: float, *, start: date | None, end: date, unit: str = "USD") -> Fact:
    return Fact(
        slug="acme", taxonomy="us-gaap", tag=tag, unit=unit, period_start=start, period_end=end,
        value=value, form="10-K", filed=date(2025, 2, 20), source="sec", source_url="u",
    )  # fmt: skip


def _acme_facts(*, without: str | None = None) -> list[Fact]:
    """One complete fiscal year for ACME, optionally without one tag."""
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    facts = [
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
    ]  # fmt: skip
    return [fact for fact in facts if fact.tag != without]


@pytest.fixture
def loaded(store: Store, watchlist: Watchlist) -> Store:
    """ACME with one fiscal year of facts, two price bars, and EUR/USD."""
    store.sync_companies(watchlist.companies)
    store.replace_facts("acme", _acme_facts())
    store.upsert_prices([
        PriceBar(slug="acme", date=date(2026, 10, d), open=None, high=h, low=lo, close=c,
                 volume=None, ccy="USD", source="twelvedata")
        for d, h, lo, c in ((26, 60.0, 40.0, 45.0), (27, 52.0, 48.0, 50.0))
    ])  # fmt: skip
    store.upsert_fx(
        [FxRate(date=date(2026, 10, 27), base="EUR", quote="USD", rate=1.1, source="t")]
    )
    return store


def test_fin_annual(loaded: Store) -> None:
    row = one(
        loaded, "SELECT fiscal_year, revenue, op_margin_pct, fcf, ebitda, debt, ccy FROM fin_annual"
    )
    assert row == (2024, 2e9, 10.0, 4e8, 3e8, 1e9, "USD")


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


def test_untagged_debt_counts_as_zero_once_cash_is_reported(loaded: Store) -> None:
    loaded.replace_facts("acme", _acme_facts(without="LongTermDebtNoncurrent"))
    net_debt, ev, reported = one(
        loaded,
        "SELECT net_debt, enterprise_value, debt_reported FROM valuation WHERE slug = 'acme'",
    )
    assert (net_debt, ev, reported) == (pytest.approx(-5e8), pytest.approx(4.5e9), False)


def test_no_enterprise_value_without_a_balance_sheet(loaded: Store) -> None:
    loaded.replace_facts("acme", _acme_facts(without="CashAndCashEquivalentsAtCarryingValue"))
    assert one(loaded, "SELECT enterprise_value FROM valuation WHERE slug = 'acme'") == (None,)


def test_gaps_list_missing_concepts_and_companies_without_financials(loaded: Store) -> None:
    loaded.replace_facts("acme", _acme_facts(without="LongTermDebtNoncurrent"))
    gaps = {row["slug"]: (row["period_end"], row["missing"]) for row in loaded.gaps()}
    assert gaps["acme"] == (date(2024, 12, 31), "debt")
    assert gaps["euro-co"][0] is None


def test_complete_companies_have_no_gaps(loaded: Store) -> None:
    assert "acme" not in {row["slug"] for row in loaded.gaps()}


def test_valuation_converts_quote_to_reporting_currency(store: Store) -> None:
    company = make_company("eu", ccy="NOK", reporting_ccy="USD")
    store.sync_companies([company])
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
    price_rep, pe = one(store, "SELECT price_rep, pe_ttm FROM valuation WHERE slug = 'eu'")
    assert price_rep == pytest.approx(11.0)  # 110 NOK = 10 EUR = 11 USD
    assert pe == pytest.approx(11.0)


def test_fin_annual_derives_esef_shares_and_dna_and_reads_extension_tags(store: Store) -> None:
    store.sync_companies([make_company("eu", reporting_ccy="EUR", financials="esef")])
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    capex = "PurchaseOfPropertyPlantAndEquipmentAndPurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"  # noqa: E501

    def esef(tag: str, value: float, taxonomy: str = "ifrs-full", unit: str = "EUR") -> Fact:
        fact = _fact(tag, value, start=y0, end=y1)
        return fact.model_copy(update={"slug": "eu", "taxonomy": taxonomy, "unit": unit})

    store.replace_facts("eu", [
        esef("Revenue", 1e9),
        esef("ProfitLossAttributableToOwnersOfParent", 1e8),
        esef("DilutedEarningsLossPerShare", 2.5, unit="EUR/shares"),
        esef(capex, 4e7, taxonomy="eu"),
        esef("DepreciationPropertyPlantAndEquipment", 3e7),
        esef("DepreciationRightofuseAssets", 1e7),
        esef("AmortisationExpense", 5e6),
    ])  # fmt: skip
    assert one(store, "SELECT capex, shares_diluted, dna FROM fin_annual") == (4e7, 4e7, 4.5e7)


def test_manual_figures_fill_gaps_but_never_override_xbrl(loaded: Store) -> None:
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)

    def manual(concept: str, value: float, start: date | None) -> Fact:
        fact = _fact(concept, value, start=start, end=y1)
        return fact.model_copy(update={"taxonomy": "manual", "source": "manual"})

    loaded.replace_facts("acme", [
        *_acme_facts(without="LongTermDebtNoncurrent"),
        manual("revenue", 1.0, y0),
        manual("debt_total", 7e8, None),
    ])  # fmt: skip
    assert one(loaded, "SELECT revenue, debt FROM fin_annual") == (2e9, 7e8)


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
    assert one(loaded, "SELECT count(*) FROM raw_facts WHERE slug = 'acme'") == (11,)


# ---------------------------------------------------------------- refresh jobs
def _bar() -> PriceBar:
    return PriceBar(
        slug="acme", date=date(2026, 10, 27), open=None, high=None, low=None, close=1.0,
        volume=None, ccy="USD", source="fake",
    )  # fmt: skip


class _Returns:
    def __init__(self, *bars: PriceBar) -> None:
        self._bars = list(bars)

    def fetch(self, company: Company) -> list[PriceBar]:
        return [bar.model_copy(update={"slug": company.slug}) for bar in self._bars]


class _Raises:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def fetch(self, company: Company) -> list[PriceBar]:  # noqa: ARG002  (Provider signature)
        raise self._error


def _prices_job(store: Store, providers: dict[str, Provider[PriceBar]]) -> CompanyJob[PriceBar]:
    def write(_company: Company, bars: Sequence[PriceBar]) -> int:
        return store.upsert_prices(bars)

    return CompanyJob(Job.PRICES, lambda c: c.sources.prices, providers, write)


def _watchlist(*prices_sources: str) -> Watchlist:
    return Watchlist(
        companies=tuple(
            make_company(f"c{i}", prices=source) for i, source in enumerate(prices_sources)
        )
    )


def test_fx_job_fetches_every_watchlist_currency(store: Store, watchlist: Watchlist) -> None:
    requested: list[set[str]] = []

    def fetch(currencies: set[str]) -> list[FxRate]:
        requested.append(currencies)
        return [FxRate(date=date(2026, 10, 27), base="EUR", quote="USD", rate=1.1, source="t")]

    report = FxJob(fetch, store.upsert_fx).run(watchlist)
    assert (requested, report.rows) == ([{"EUR", "USD"}], 1)


def test_company_job_writes_each_company(store: Store) -> None:
    job = _prices_job(store, {"twelvedata": _Returns(_bar())})
    report = job.run(_watchlist("twelvedata", "twelvedata"))
    assert (report.ok, report.rows) == (["c0", "c1"], 2)


def test_company_job_skips_companies_without_a_provider(store: Store) -> None:
    report = _prices_job(store, {}).run(_watchlist("yahoo"))
    assert report.skipped == {"c0": "no yahoo provider configured"}


def test_company_job_skips_companies_missing_an_identifier(store: Store) -> None:
    job = _prices_job(store, {"yahoo": _Raises(MissingIdentifierError("no cik"))})
    assert job.run(_watchlist("yahoo")).skipped == {"c0": "no cik"}


@pytest.mark.parametrize(
    "error", [ProviderError("yahoo changed"), httpx.ConnectError("yahoo changed")]
)
def test_one_failing_company_does_not_stop_the_others(store: Store, error: Exception) -> None:
    job = _prices_job(store, {"yahoo": _Raises(error), "twelvedata": _Returns(_bar())})
    report = job.run(_watchlist("yahoo", "twelvedata"))
    assert (report.failed, report.ok) == ({"c0": "yahoo changed"}, ["c1"])


def test_report_lines_list_failed_and_skipped_companies(store: Store) -> None:
    job = _prices_job(store, {"yahoo": _Raises(ProviderError("down"))})
    report = job.run(_watchlist("yahoo", "twelvedata"))
    assert report.lines() == [
        "prices: 0 rows, 0 ok, 1 skipped, 1 failed (c0)",
        "  failed  c0: down",
        "  skipped c1: no twelvedata provider configured",
    ]


def test_prices_without_twelvedata_key_are_skipped(store: Store, settings: Settings) -> None:
    no_key = settings.model_copy(update={"twelvedata_api_key": None})
    with httpx.Client() as client:
        report = build_jobs(store, client, no_key)[Job.PRICES].run(_watchlist("twelvedata"))
    assert report.skipped == {"c0": "no twelvedata provider configured"}


def test_financials_skip_companies_without_a_file_or_identifier(
    store: Store, settings: Settings
) -> None:
    watchlist = Watchlist(
        companies=(
            make_company("m", financials="manual"),
            make_company("s", cik=None),
            make_company("e", financials="esef"),
        )
    )
    with httpx.Client() as client:
        report = build_jobs(store, client, settings)[Job.FINANCIALS].run(watchlist)
    assert report.skipped == {
        "m": f"no {settings.manual_dir / 'm.yaml'} (see financials.example/)",
        "s": "no cik in watchlist.yaml",
        "e": "no lei in watchlist.yaml (run `wl resolve-ids`)",
    }
