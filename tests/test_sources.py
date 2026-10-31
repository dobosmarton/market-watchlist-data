from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from hypothesis import given
from hypothesis import strategies as st

from wl.config import load_watchlist
from wl.models import DateRange
from wl.sources import esef, fx, gleif, sec
from wl.sources.base import MissingIdentifierError, ProviderError
from wl.sources.manual import ManualProvider
from wl.sources.prices import EuronextProvider, TwelveDataProvider

from .conftest import fixture_bytes, make_company

if TYPE_CHECKING:
    from wl.config import Company, Watchlist

OCTOBER = DateRange(date(2026, 10, 1), date(2026, 10, 27))


# ---------------------------------------------------------------- FX
@respx.mock
def test_fx_range(client: httpx.Client) -> None:
    route = respx.get(url__startswith=fx.BASE_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("frankfurter_range.json"))
    )
    window = DateRange(date(2026, 10, 26), date(2026, 10, 27))
    rates = fx.fetch_rates(client, window, {"EUR", "USD", "NOK"})
    assert route.calls.last.request.url.params["symbols"] == "NOK,USD"
    assert len(rates) == 8
    assert rates[0].date == date(2026, 10, 26)
    assert {r.base for r in rates} == {"EUR"}


def test_fx_nothing_to_fetch(client: httpx.Client) -> None:
    assert fx.fetch_rates(client, OCTOBER, {"EUR"}) == []


# ---------------------------------------------------------------- SEC
def test_sec_keeps_annual_and_quarterly_forms_and_known_taxonomies() -> None:
    facts = sec.parse_company_facts(make_company(), fixture_bytes("sec_companyfacts.json"), "u")
    assert {f.form for f in facts} == {"10-K", "10-Q"}  # the 8-K is dropped
    revenue = [f for f in facts if f.tag == "Revenues"]
    assert [f.value for f in revenue] == [1296745000, 1669626000, 430081000]
    cash = next(f for f in facts if f.tag == "CashAndCashEquivalentsAtCarryingValue")
    assert cash.period_start is None
    assert {f.taxonomy for f in facts} == {"us-gaap", "dei"}


@respx.mock
def test_sec_fetch_uses_padded_cik(client: httpx.Client) -> None:
    route = respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json").mock(
        return_value=httpx.Response(200, content=fixture_bytes("sec_companyfacts.json"))
    )
    facts = sec.SecProvider(client).fetch(make_company())
    assert route.called
    assert facts


def test_sec_without_cik_is_a_missing_identifier(client: httpx.Client) -> None:
    with pytest.raises(MissingIdentifierError, match="cik"):
        sec.SecProvider(client).fetch(make_company(cik=None))


# ---------------------------------------------------------------- ESEF
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-01-01T00:00:00", (None, date(2024, 12, 31))),
        ("2024-12-31", (None, date(2024, 12, 31))),
        (
            "2024-01-01T00:00:00/2025-01-01T00:00:00",
            (date(2024, 1, 1), date(2024, 12, 31)),
        ),
        ("2024-01-01T00:00:00/2024-12-31T12:00:00", (date(2024, 1, 1), date(2024, 12, 31))),
    ],
)
def test_oim_period(text: str, expected: tuple[date | None, date]) -> None:
    assert esef.parse_period(text) == expected


@given(st.dates(min_value=date(2000, 1, 2), max_value=date(2100, 1, 1)))
def test_oim_midnight_instant_is_previous_day(day: date) -> None:
    start, end = esef.parse_period(f"{day.isoformat()}T00:00:00")
    assert start is None
    assert (day - end).days == 1


@respx.mock
def test_esef_fetch(client: httpx.Client, watchlist: Watchlist) -> None:
    listing = respx.get(f"{esef.BASE}/api/filings").mock(
        return_value=httpx.Response(200, content=fixture_bytes("xbrl_filings.json"))
    )
    report = respx.get(url__regex=r".*/ESEF/NL/1/report\.json").mock(
        return_value=httpx.Response(200, content=fixture_bytes("oim_report.json"))
    )
    facts = esef.EsefProvider(client).fetch(watchlist.get("euro-co"))

    params = listing.calls.last.request.url.params
    assert params["filter[entity.identifier]"] == "EXAMPLE0LEI000000001"
    assert report.called  # the newer of the two 2024 filings
    by_tag = {f.tag: f for f in facts}
    assert set(by_tag) == {
        "Revenue",
        "ProfitLossFromOperatingActivities",
        "CashAndCashEquivalents",
        "WeightedAverageShares",
    }
    assert by_tag["Revenue"].value == 3_300_000_000  # consolidated, not the segment value
    assert by_tag["Revenue"].unit == "EUR"
    assert by_tag["WeightedAverageShares"].unit == "shares"
    assert by_tag["Revenue"].period_end == date(2024, 12, 31)
    assert by_tag["Revenue"].filed == date(2025, 4, 1)


def test_esef_without_lei_is_a_missing_identifier(client: httpx.Client) -> None:
    with pytest.raises(MissingIdentifierError, match="lei"):
        esef.EsefProvider(client).fetch(make_company(financials="esef"))


# ---------------------------------------------------------------- manual
ROOT = Path(__file__).parent.parent
ACME_YEAR = """
currency: USD
money_unit: millions
shares_unit: millions
years:
  - period_end: 2024-12-31
    source_url: https://example.com/acme-2024.pdf
    revenue: 2000
    net_income: 100
    cash: 500
    shares_diluted: 100
    eps_diluted: 1.0
"""


def _manual(tmp_path: Path, text: str) -> ManualProvider:
    (tmp_path / "acme.yaml").write_text(text)
    return ManualProvider(tmp_path)


def test_manual_figures_become_scaled_facts(tmp_path: Path) -> None:
    facts = {f.tag: f for f in _manual(tmp_path, ACME_YEAR).fetch(make_company())}
    assert facts["revenue"].value == 2e9
    assert (facts["revenue"].period_start, facts["revenue"].unit) == (date(2024, 1, 1), "USD")
    assert (facts["cash"].period_start, facts["shares_diluted"].unit) == (None, "shares")
    assert (facts["eps_diluted"].value, facts["eps_diluted"].unit) == (1.0, "USD/shares")
    assert {f.taxonomy for f in facts.values()} == {"manual"}


def test_manual_without_a_file_is_a_missing_identifier(tmp_path: Path) -> None:
    with pytest.raises(MissingIdentifierError, match=r"acme\.yaml"):
        ManualProvider(tmp_path).fetch(make_company())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("currency: USD", "currency: EUR"), "reporting_ccy"),
        (("2024-12-31", "2024-06-30"), "fiscal_year_end_month"),
        (("cash: 500", "cash_flow: 500"), "cash_flow"),
        (("    revenue: 2000\n", ""), "revenue"),
    ],
)
def test_manual_rejects_bad_files(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        _manual(tmp_path, ACME_YEAR.replace(*change)).fetch(make_company())


def test_example_manual_file_matches_the_example_watchlist() -> None:
    sap = load_watchlist(ROOT / "watchlist.example.yaml").get("sap")
    facts = ManualProvider(ROOT / "financials.example").fetch(sap)
    assert next(f.value for f in facts if f.tag == "revenue") == 36.8e9


# ---------------------------------------------------------------- prices
def _euro_listed(isin: str | None = "NL0000000018") -> Company:
    return make_company(isin=isin, mic="XAMS", ccy="EUR", prices="euronext")


EURONEXT_URL = "https://live.euronext.com/en/ajax/AwlHistoricalPrice/getFullDownloadAjax/"


@respx.mock
def test_euronext(client: httpx.Client) -> None:
    route = respx.get(f"{EURONEXT_URL}NL0000000018-XAMS").mock(
        return_value=httpx.Response(200, content=fixture_bytes("euronext_history.csv"))
    )
    bars = EuronextProvider(client, OCTOBER).fetch(_euro_listed())
    assert route.calls.last.request.url.params["startdate"] == "2026-10-01"
    assert (bars[0].date, bars[0].close, bars[0].volume) == (date(2026, 10, 30), 1412.8, 818682)
    assert {(b.ccy, b.source) for b in bars} == {("EUR", "euronext")}
    assert len(bars) == 3


@respx.mock
@pytest.mark.parametrize(
    ("body", "message"),
    [(b'"Historical Data"\nDate;Open;High;Low;Last;Close\n', "no prices"), (b"<html>", "Date")],
)
def test_euronext_without_rows_is_an_error(client: httpx.Client, body: bytes, message: str) -> None:
    respx.get(url__startswith=EURONEXT_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ProviderError, match=message):
        EuronextProvider(client, OCTOBER).fetch(_euro_listed())


def test_euronext_without_isin_is_a_missing_identifier(client: httpx.Client) -> None:
    with pytest.raises(MissingIdentifierError):
        EuronextProvider(client, OCTOBER).fetch(_euro_listed(isin=None))


@respx.mock
def test_twelvedata(client: httpx.Client) -> None:
    route = respx.get(TwelveDataProvider.url).mock(
        return_value=httpx.Response(200, content=fixture_bytes("twelvedata_series.json"))
    )
    bars = TwelveDataProvider(client, "k", OCTOBER, sleep=lambda _: None).fetch(make_company())
    assert route.calls.last.request.url.params["symbol"] == "ACME"
    assert [b.close for b in bars] == [204.5, 208.0]
    assert bars[0].ccy == "USD"


@respx.mock
@pytest.mark.parametrize("payload", [{"status": "ok", "values": []}, {"status": "error"}])
def test_twelvedata_waits_after_every_request(client: httpx.Client, payload: object) -> None:
    respx.get(TwelveDataProvider.url).mock(return_value=httpx.Response(200, json=payload))
    sleeps: list[float] = []
    provider = TwelveDataProvider(client, "k", OCTOBER, sleep=sleeps.append)
    with contextlib.suppress(ProviderError):
        provider.fetch(make_company())
    assert sleeps == [7.5]  # free plan: 8 requests a minute


@respx.mock
def test_twelvedata_error(client: httpx.Client) -> None:
    respx.get(TwelveDataProvider.url).mock(
        return_value=httpx.Response(200, json={"status": "error", "message": "plan limit"})
    )
    provider = TwelveDataProvider(client, "k", OCTOBER, sleep=lambda _: None)
    with pytest.raises(ProviderError, match="plan limit"):
        provider.fetch(make_company())


# ---------------------------------------------------------------- GLEIF and retries
@respx.mock
def test_gleif(client: httpx.Client) -> None:
    respx.get(gleif.URL).mock(
        side_effect=[
            httpx.Response(200, content=fixture_bytes("gleif.json")),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert gleif.lei_for_isin(client, "NL0000000018") == "EXAMPLE0LEI000000001"
    assert gleif.lei_for_isin(client, "NL0000000000") is None


@respx.mock
def test_transient_errors_are_retried(client: httpx.Client) -> None:
    route = respx.get(gleif.URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, content=fixture_bytes("gleif.json")),
        ]
    )
    assert gleif.lei_for_isin(client, "NL0000000018") == "EXAMPLE0LEI000000001"
    assert route.call_count == 2


@respx.mock
def test_client_errors_are_not_retried(client: httpx.Client) -> None:
    route = respx.get(gleif.URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        gleif.lei_for_isin(client, "NL0000000018")
    assert route.call_count == 1
