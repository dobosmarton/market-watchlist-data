from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from hypothesis import given
from hypothesis import strategies as st

from wl.sources import esef, fx, gleif, sec
from wl.sources.prices import ProviderError, TwelveDataProvider

from .conftest import fixture_bytes, make_company

if TYPE_CHECKING:
    from wl.config import Watchlist


# ---------------------------------------------------------------- FX
@respx.mock
def test_fx_range(client: httpx.Client) -> None:
    route = respx.get(url__startswith=fx.BASE_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("frankfurter_range.json"))
    )
    rates = fx.fetch_rates(client, date(2026, 10, 26), date(2026, 10, 27), {"EUR", "USD", "NOK"})
    assert route.calls.last.request.url.params["symbols"] == "NOK,USD"
    assert len(rates) == 8
    assert rates[0].date == date(2026, 10, 26)
    assert {r.base for r in rates} == {"EUR"}


def test_fx_nothing_to_fetch(client: httpx.Client) -> None:
    assert fx.fetch_rates(client, date(2026, 1, 1), date(2026, 1, 2), {"EUR"}) == []


# ---------------------------------------------------------------- SEC
def test_sec_keeps_annual_forms_and_known_taxonomies() -> None:
    facts = sec.parse_company_facts(make_company(), fixture_bytes("sec_companyfacts.json"), "u")
    forms = {f.form for f in facts}
    assert forms == {"10-K"}
    revenue = [f for f in facts if f.tag == "Revenues"]
    assert [f.value for f in revenue] == [1296745000, 1669626000]
    cash = next(f for f in facts if f.tag == "CashAndCashEquivalentsAtCarryingValue")
    assert cash.period_start is None
    assert {f.taxonomy for f in facts} == {"us-gaap", "dei"}


@respx.mock
def test_sec_fetch_uses_padded_cik(client: httpx.Client) -> None:
    route = respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json").mock(
        return_value=httpx.Response(200, content=fixture_bytes("sec_companyfacts.json"))
    )
    facts = sec.fetch_facts(client, make_company())
    assert route.called
    assert facts


def test_sec_without_cik_returns_nothing(client: httpx.Client) -> None:
    assert sec.fetch_facts(client, make_company(cik=None)) == []


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
    facts = esef.fetch_facts(client, watchlist.get("euro-co"))

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


def test_esef_without_lei_returns_nothing(client: httpx.Client) -> None:
    assert esef.fetch_facts(client, make_company(financials="esef")) == []


# ---------------------------------------------------------------- prices
@respx.mock
def test_twelvedata(client: httpx.Client) -> None:
    route = respx.get(TwelveDataProvider.url).mock(
        return_value=httpx.Response(200, content=fixture_bytes("twelvedata_series.json"))
    )
    bars = TwelveDataProvider(client, "k").fetch_daily(
        make_company(), date(2026, 10, 1), date(2026, 10, 27)
    )
    assert route.calls.last.request.url.params["symbol"] == "ACME"
    assert [b.close for b in bars] == [204.5, 208.0]
    assert bars[0].ccy == "USD"


@respx.mock
def test_twelvedata_error(client: httpx.Client) -> None:
    respx.get(TwelveDataProvider.url).mock(
        return_value=httpx.Response(200, json={"status": "error", "message": "plan limit"})
    )
    with pytest.raises(ProviderError, match="plan limit"):
        TwelveDataProvider(client, "k").fetch_daily(
            make_company(), date(2026, 10, 1), date(2026, 10, 27)
        )


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
