from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest

from wl import pipeline
from wl.config import Watchlist
from wl.export import write_exports
from wl.models import Fact, FxRate, PriceBar
from wl.pipeline import CompanyJob, FxJob, Job, RunReport, build_jobs
from wl.sources.base import MissingIdentifierError, ProviderError
from wl.store import Store

from .conftest import acme_facts, make_company, one, us_fact

_IN_2025 = date(2025, 6, 1)  # the fixtures' FY2024 is the latest year a report is due for

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from wl.config import Company
    from wl.settings import Settings
    from wl.sources.base import Provider


def test_fin_annual(loaded: Store) -> None:
    row = one(
        loaded, "SELECT fiscal_year, revenue, op_margin_pct, fcf, ebitda, debt, ccy FROM fin_annual"
    )
    assert row == (2024, 2e9, 10.0, 4e8, 3e8, 1e9, "USD")


def test_valuation(loaded: Store) -> None:
    (mcap, mcap_eur, pe, ev_ebitda, fcf_yield, nd_ebitda, pos) = one(
        loaded,
        "SELECT market_cap, market_cap_eur, pe_ttm, ev_ebitda, fcf_yield_pct, net_debt_ebitda, "
        "wk52_pos FROM valuation WHERE slug = 'acme'",
    )
    assert mcap == pytest.approx(5e9)  # 50 USD x 100m shares
    assert mcap_eur == pytest.approx(5e9 / 1.1)  # at 1.1 USD per EUR
    assert pe == pytest.approx(50.0)
    assert ev_ebitda == pytest.approx((5e9 + 5e8) / 3e8)
    assert fcf_yield == pytest.approx(8.0)
    assert nd_ebitda == pytest.approx(5e8 / 3e8)
    assert pos == pytest.approx(50.0)  # 50 within 40..60


def test_untagged_debt_counts_as_zero_once_cash_is_reported(loaded: Store) -> None:
    loaded.replace_facts("acme", acme_facts(without="LongTermDebtNoncurrent"))
    net_debt, ev, reported = one(
        loaded,
        "SELECT net_debt, enterprise_value, debt_reported FROM valuation WHERE slug = 'acme'",
    )
    assert (net_debt, ev, reported) == (pytest.approx(-5e8), pytest.approx(4.5e9), False)


def test_no_enterprise_value_without_a_balance_sheet(loaded: Store) -> None:
    loaded.replace_facts("acme", acme_facts(without="CashAndCashEquivalentsAtCarryingValue"))
    assert one(loaded, "SELECT enterprise_value FROM valuation WHERE slug = 'acme'") == (None,)


def test_no_net_debt_to_ebitda_when_ebitda_is_negative(loaded: Store) -> None:
    loss = us_fact(
        "OperatingIncomeLoss", -300_000_000, start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    loaded.replace_facts("acme", [*acme_facts(without="OperatingIncomeLoss"), loss])
    ev_ebitda, nd_ebitda = one(
        loaded, "SELECT ev_ebitda, net_debt_ebitda FROM valuation WHERE slug = 'acme'"
    )
    assert ev_ebitda == pytest.approx((5e9 + 5e8) / -2e8)  # EBITDA -300m + 100m D&A
    assert nd_ebitda is None  # its sign would read as net cash


def test_gaps_list_missing_concepts_and_companies_without_financials(loaded: Store) -> None:
    loaded.replace_facts("acme", acme_facts(without="LongTermDebtNoncurrent"))
    gaps = {row["slug"]: (row["period_end"], row["missing"]) for row in loaded.gaps(_IN_2025)}
    assert gaps["acme"] == (date(2024, 12, 31), "debt")
    assert gaps["euro-co"][0] is None


def test_complete_companies_have_no_gaps(loaded: Store) -> None:
    assert "acme" not in {row["slug"] for row in loaded.gaps(_IN_2025)}


def test_gaps_flag_a_year_once_the_next_report_is_due(loaded: Store) -> None:
    def acme_gap(today: date) -> object:
        return {row["slug"]: row["missing"] for row in loaded.gaps(today)}.get("acme")

    assert acme_gap(date(2026, 4, 30)) is None  # the FY2025 report is due by the end of April
    assert acme_gap(date(2026, 5, 1)) == "out of date"
    loaded.replace_facts("acme", acme_facts(without="LongTermDebtNoncurrent"))
    assert acme_gap(date(2026, 5, 1)) == "debt, out of date"


def test_valuation_converts_quote_to_reporting_currency(store: Store) -> None:
    company = make_company("eu", ccy="NOK", reporting_ccy="USD")
    store.sync_companies([company])
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    store.replace_facts("eu", [
        f.model_copy(update={"slug": "eu"}) for f in (
            us_fact("Revenues", 1e9, start=y0, end=y1),
            us_fact("NetIncomeLoss", 1e8, start=y0, end=y1),
            us_fact("WeightedAverageNumberOfDilutedSharesOutstanding", 1e8, start=y0, end=y1,
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
        fact = us_fact(tag, value, start=y0, end=y1)
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


def _manual(concept: str, value: float, *, flow: bool = True) -> Fact:
    """Build a typed figure for ACME's fiscal 2024, as ``financials/acme.yaml`` gives it."""
    fact = us_fact(concept, value, start=date(2024, 1, 1) if flow else None, end=date(2024, 12, 31))
    return fact.model_copy(update={"taxonomy": "manual", "source": "manual", "form": "manual"})


def test_manual_figures_fill_gaps_but_never_override_xbrl(loaded: Store) -> None:
    loaded.replace_facts("acme", [
        *acme_facts(without="LongTermDebtNoncurrent"),
        _manual("revenue", 1.0),
        _manual("debt_total", 7e8, flow=False),
    ])  # fmt: skip
    assert one(loaded, "SELECT revenue, debt FROM fin_annual") == (2e9, 7e8)


# ---------------------------------------------------------------- trailing twelve months
def _quarterly(tag: str, value: float, *, start: date | None, end: date, unit: str = "USD") -> Fact:
    fact = us_fact(tag, value, start=start, end=end, unit=unit)
    return fact.model_copy(update={"form": "10-Q", "filed": date(2025, 8, 1)})


H1_2025, H1_2024 = (date(2025, 1, 1), date(2025, 6, 30)), (date(2024, 1, 1), date(2024, 6, 30))


def _half_year(*, without_prior: str | None = None) -> list[Fact]:
    """ACME's 2025 half-year 10-Q: year to date, last year's comparatives, the balance sheet."""
    facts = [
        _quarterly("Revenues", 1.2e9, start=H1_2025[0], end=H1_2025[1]),
        _quarterly("Revenues", 0.9e9, start=H1_2024[0], end=H1_2024[1]),
        _quarterly("NetIncomeLoss", 8e7, start=H1_2025[0], end=H1_2025[1]),
        _quarterly("NetIncomeLoss", 4e7, start=H1_2024[0], end=H1_2024[1]),
        _quarterly("CashAndCashEquivalentsAtCarryingValue", 6e8, start=None, end=H1_2025[1]),
        _quarterly(
            "WeightedAverageNumberOfDilutedSharesOutstanding", 1.1e8,
            start=date(2025, 4, 1), end=H1_2025[1], unit="shares",
        ),
        # the prior year-end balance sheet repeated in the 10-Q must not replace the 10-K's
        _quarterly(
            "CashAndCashEquivalentsAtCarryingValue", 9e8, start=None, end=date(2024, 12, 31)
        ),
    ]  # fmt: skip
    return [f for f in facts if not (f.tag == without_prior and f.period_end == H1_2024[1])]


def test_ttm_adds_this_year_to_date_and_drops_last_years(loaded: Store) -> None:
    loaded.replace_facts("acme", [*acme_facts(), *_half_year()])
    row = one(
        loaded,
        "SELECT basis, period_end, revenue, net_income, op_income, cash, debt, shares_diluted "
        "FROM fin_ttm WHERE slug = 'acme'",
    )
    # revenue 2.0 + 1.2 - 0.9; op income has no quarterly figure and keeps the annual value
    assert row == ("ttm", H1_2025[1], 2.3e9, 1.4e8, 2e8, 6e8, 1e9, 1.1e8)


def test_ttm_of_a_spin_off_starts_from_its_typed_last_year(loaded: Store) -> None:
    # 10-Qs only (no 10-K yet), plus the year before the spin-off typed in from its Form 10
    typed = [_manual("revenue", 2e9), _manual("net_income", 1e8), _manual("cash", 5e8, flow=False)]
    loaded.replace_facts("acme", [*typed, *_half_year()])
    row = one(loaded, "SELECT basis, revenue, net_income, cash FROM fin_ttm WHERE slug = 'acme'")
    assert row == ("ttm", 2.3e9, 1.4e8, 6e8)
    assert one(loaded, "SELECT cash FROM fin_annual") == (5e8,)


def test_ttm_keeps_the_annual_value_without_last_years_comparative(loaded: Store) -> None:
    loaded.replace_facts("acme", [*acme_facts(), *_half_year(without_prior="Revenues")])
    basis, period_end, revenue, net_income = one(
        loaded, "SELECT basis, period_end, revenue, net_income FROM fin_ttm WHERE slug = 'acme'"
    )
    assert (basis, period_end, revenue, net_income) == ("annual", date(2024, 12, 31), 2e9, 1.4e8)


def test_companies_without_quarterlies_are_valued_on_the_annual_report(loaded: Store) -> None:
    basis, period_end = one(
        loaded, "SELECT basis, fin_period_end FROM valuation WHERE slug = 'acme'"
    )
    assert (basis, period_end) == ("annual", date(2024, 12, 31))


def test_export(loaded: Store, tmp_path: Path) -> None:
    written = write_exports(loaded, tmp_path)
    assert written == {"valuation": 2, "financials": 1}
    valuation = json.loads((tmp_path / "valuation.json").read_text())
    acme = next(r for r in valuation if r["slug"] == "acme")
    assert acme["mcap_bn"] == 5.0
    assert acme["mcap_eur_bn"] == 4.55  # 5bn USD at 1.1 USD per EUR, rounded
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
    bad = us_fact("Revenues", 1.0, start=None, end=date(2024, 12, 31))
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


def test_report_lines_list_failed_companies_and_skipped_ones_by_reason(store: Store) -> None:
    job = _prices_job(store, {"yahoo": _Raises(ProviderError("down"))})
    companies = (
        make_company("c0", prices="yahoo"), make_company("c1"),
        make_company("c2", prices="euronext", mic="XAMS"), make_company("c3"),
    )  # fmt: skip
    report = job.run(Watchlist(companies=companies))
    assert report.lines() == [
        "prices: 0 rows, 0 ok, 3 skipped, 1 failed (c0)",
        "  failed  c0: down",
        "  skipped c1, c3: no twelvedata provider configured",
        "  skipped c2: no euronext provider configured",
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


def _no_rebuild(_settings: Settings) -> None:
    pass


class _Recorder:
    def __init__(self, job: Job, seen: dict[Job, list[str]]) -> None:
        self._job, self._seen = job, seen

    def run(self, watchlist: Watchlist) -> RunReport:
        self._seen[self._job] = [c.slug for c in watchlist.companies]
        return RunReport(self._job)


def test_refresh_for_some_companies_still_covers_every_currency(
    settings: Settings, watchlist: Watchlist, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[Job, list[str]] = {}
    jobs = {job: _Recorder(job, seen) for job in Job}

    def recording_jobs(*_args: object) -> dict[Job, _Recorder]:
        return jobs

    monkeypatch.setattr(pipeline, "build_jobs", recording_jobs)
    monkeypatch.setattr(pipeline, "build_marts", _no_rebuild)
    reports = pipeline.run_refresh(settings, watchlist, slugs=["euro-co"])
    assert [r.job for r in reports] == list(Job)
    assert seen == {
        Job.FX: ["acme", "euro-co"],
        **{job: ["euro-co"] for job in list(Job)[1:]},
    }
    with Store.open(settings, read_only=True) as store:
        assert len(store.query("SELECT * FROM companies")) == 2  # the whole list is synced


@pytest.mark.parametrize(
    ("error", "described"),
    [
        (
            httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "https://x.test/series?apikey=secret"),
                response=httpx.Response(404),
            ),
            "HTTP 404 from https://x.test/series",
        ),
        (httpx.ConnectError("connection refused"), "connection refused"),
        (ProviderError("no data"), "no data"),
    ],
)  # fmt: skip
def test_failures_are_described_without_the_query_string(error: Exception, described: str) -> None:
    assert pipeline.describe_error(error) == described
