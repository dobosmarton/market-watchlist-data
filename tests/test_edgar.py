from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from wl.sources import edgar
from wl.sources.base import MissingIdentifierError, ProviderError

from .conftest import fixture_bytes, make_company, one

if TYPE_CHECKING:
    from wl.store import Store

SINCE = date(2025, 11, 1)
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0001234567.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1234567"
BUY_XML = f"{ARCHIVE}/000123456726000010/form4-buy.xml"
SALES_XML = f"{ARCHIVE}/000123456726000009/form4-sales.xml"


def entry(accession: str = "0001234567-26-000009") -> edgar.Entry:
    return edgar.Entry(
        cik=1234567, accession=accession, filed=date(2026, 10, 15), form="4", items="",
        period=None, document="xslF345X06/form4-sales.xml",
    )  # fmt: skip


def mock_submissions() -> respx.Route:
    return respx.get(SUBMISSIONS).mock(
        return_value=httpx.Response(200, content=fixture_bytes("edgar_submissions.json"))
    )


@respx.mock
def test_filings_keep_reports_current_reports_and_holder_schedules(client: httpx.Client) -> None:
    mock_submissions()
    filings = edgar.EdgarFilingsProvider(client, SINCE).fetch(make_company())
    # Form 4s become insider trades, a 144 is dropped, and so is the 10-Q from before SINCE
    assert [(f.form, f.date) for f in filings] == [
        ("8-K", date(2026, 10, 2)),
        ("10-Q", date(2026, 8, 5)),
        ("SCHEDULE 13G/A", date(2026, 7, 1)),
        ("6-K", date(2026, 5, 1)),
    ]
    current, report = filings[0], filings[1]
    assert current.title == "8-K: results; officer or director change"
    assert (current.items, current.id) == ("2.02,5.02,9.01", "0001234567-26-000008")
    assert current.url == f"{ARCHIVE}/000123456726000008/acme-8k.htm"
    assert (report.title, report.period) == (
        "10-Q for the period ended 2026-06-30",
        date(2026, 6, 30),
    )
    assert filings[2].url.endswith("/xslSCHEDULE_13G_X02/primary_doc.xml")  # the rendered page


@pytest.mark.parametrize(
    ("form", "items", "period", "title"),
    [
        ("8-K/A", "9.01", None, "8-K/A: exhibits only"),
        ("8-K", "1.01, 2.01", None, "8-K: material agreement; acquisition or disposal completed"),
        ("20-F", "", date(2025, 12, 31), "20-F for the period ended 2025-12-31"),
        ("40-F/A", "", None, "40-F/A"),
        ("SC 13D", "", None, "SC 13D: a holder of 5% or more that may seek to influence the company"),  # noqa: E501
        ("SCHEDULE 13G", "", None, "SCHEDULE 13G: a passive holder of 5% or more"),
        ("6-K", "", None, "6-K"),
    ],
)  # fmt: skip
def test_filing_titles(form: str, items: str, period: date | None, title: str) -> None:
    assert edgar.filing_title(form, items, period) == title


def test_form4_sales_under_a_plan() -> None:
    trades = edgar.parse_form4("acme", entry(), fixture_bytes("form4_sales.xml"))
    assert [(t.line, t.code, t.trade_date, t.shares, t.price) for t in trades] == [
        (1, "S", date(2026, 10, 13), 3000, 100),
        (2, "S", date(2026, 10, 14), 2000, 101),  # the date's time zone is dropped
        (3, "F", date(2026, 10, 14), 500, 101),
    ]
    first = trades[0]
    assert (first.person, first.role, first.planned) == (
        "ROE RICHARD",
        "Chief Financial Officer",
        True,
    )
    assert (first.acquired, first.derivative, first.owned_after) == (False, False, 47000)
    assert (first.security, first.ccy, first.filed) == ("Common Stock", "USD", date(2026, 10, 15))
    assert first.url == f"{ARCHIVE}/000123456726000009/xslF345X06/form4-sales.xml"
    assert not first.amendment
    amended = edgar.parse_form4(
        "acme", replace(entry(), form="4/A"), fixture_bytes("form4_sales.xml")
    )
    assert all(trade.amendment for trade in amended)


def test_form4_buy_with_an_option_award() -> None:
    trades = edgar.parse_form4("acme", entry(), fixture_bytes("form4_buy.xml"))
    buy, award = trades  # the units without a transaction date are left out
    assert (buy.code, buy.acquired, buy.derivative, buy.planned) == ("P", True, False, False)
    assert (buy.person, buy.role, buy.shares, buy.price) == ("DOE JANE", "Director", 1000, 49.5)
    assert (award.code, award.derivative, award.security) == (
        "A",
        True,
        "Stock Option (right to buy)",
    )


def test_form4_roles_combine() -> None:
    sales = fixture_bytes("form4_sales.xml")
    every_role = sales.replace(b"<isDirector>0", b"<isDirector>1").replace(
        b"<isTenPercentOwner>0", b"<isTenPercentOwner>1"
    )
    other = sales.replace(b"<isOfficer>1", b"<isOfficer>0").replace(b"<isOther>0", b"<isOther>1")
    assert {t.role for t in edgar.parse_form4("acme", entry(), every_role)} == {
        "Chief Financial Officer, Director, 10% owner"
    }
    assert {t.role for t in edgar.parse_form4("acme", entry(), other)} == {"Other"}
    no_owner = b"<ownershipDocument><issuer/></ownershipDocument>"
    with pytest.raises(ProviderError, match="no reporting owner"):
        edgar.parse_form4("acme", entry(), no_owner)


@respx.mock
def test_insiders_fetch_only_form4s_not_stored_yet(client: httpx.Client) -> None:
    mock_submissions()
    buy = respx.get(BUY_XML).mock(
        return_value=httpx.Response(200, content=fixture_bytes("form4_buy.xml"))
    )
    sales = respx.get(SALES_XML).mock(return_value=httpx.Response(200))
    pauses: list[float] = []

    def known(slug: str) -> set[str]:
        assert slug == "acme"
        return {"0001234567-26-000009"}

    provider = edgar.EdgarInsidersProvider(client, SINCE, known, pauses.append)
    trades = provider.fetch(make_company())
    assert (buy.called, sales.called) == (True, False)
    assert {t.id for t in trades} == {"0001234567-26-000010"}
    assert pauses == [edgar.PAUSE_S]


@respx.mock
def test_insiders_skip_an_unreadable_form4_and_cap_each_run(
    client: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_submissions()
    respx.get(BUY_XML).mock(return_value=httpx.Response(404))
    respx.get(SALES_XML).mock(
        return_value=httpx.Response(200, content=fixture_bytes("form4_sales.xml"))
    )
    provider = edgar.EdgarInsidersProvider(client, SINCE, lambda _slug: set(), lambda _s: None)
    assert {t.id for t in provider.fetch(make_company())} == {"0001234567-26-000009"}

    monkeypatch.setattr(edgar, "MAX_FORM4", 1)  # the newest one only: the 404
    assert provider.fetch(make_company()) == []


def test_edgar_needs_a_cik(client: httpx.Client) -> None:
    for provider in (
        edgar.EdgarFilingsProvider(client, SINCE),
        edgar.EdgarInsidersProvider(client, SINCE, lambda _slug: set()),
    ):
        with pytest.raises(MissingIdentifierError, match="cik"):
            provider.fetch(make_company(cik=None))


def test_store_keeps_filings_and_trades_until_the_company_goes(store: Store) -> None:
    trades = edgar.parse_form4("acme", entry(), fixture_bytes("form4_sales.xml"))
    current = replace(entry("0001234567-26-000008"), form="8-K", items="2.02", document="a.htm")
    filings = edgar.filings_of("acme", [entry(), current])  # the Form 4 is not a filing here
    assert [f.form for f in filings] == ["8-K"]
    assert store.upsert_filings(filings) == 1
    assert store.upsert_insider_trades(trades) == 3
    store.upsert_insider_trades(trades)  # stored again, not twice
    assert one(store, "SELECT count(*) FROM raw_insider_trades") == (3,)
    assert store.insider_filing_ids("acme", "sec") == {"0001234567-26-000009"}
    assert store.insider_filing_ids("acme", "bafin") == set()
    store.delete_company("acme")
    assert store.insider_filing_ids("acme", "sec") == set()
    assert one(store, "SELECT count(*) FROM raw_filings") == (0,)
