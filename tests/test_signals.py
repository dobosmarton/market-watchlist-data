from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from wl.models import InsiderTrade, PriceBar
from wl.sources import edgar

from .conftest import acme_facts, acme_prior_year, fixture_bytes

if TYPE_CHECKING:
    from wl.config import Watchlist
    from wl.store import Row, Store


@pytest.fixture
def listed(store: Store, watchlist: Watchlist) -> Store:
    store.sync_companies(watchlist.companies)
    return store


def weekdays(count: int, start: date = date(2025, 6, 2)) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def load_closes(
    store: Store, closes: list[float], volumes: list[float] | None = None
) -> list[date]:
    dates = weekdays(len(closes))
    store.upsert_prices([
        PriceBar(slug="acme", date=day, open=None, high=None, low=None, close=close,
                 volume=volume, ccy="USD", source="twelvedata")
        for day, close, volume in zip(dates, closes, volumes or [1e6] * len(closes), strict=True)
    ])  # fmt: skip
    return dates


def price_signals(store: Store) -> Row:
    (row,) = store.query("SELECT * FROM price_signals WHERE slug = 'acme'")
    return row


def signal_kinds(store: Store) -> dict[str, Row]:
    rows = store.query("SELECT * FROM signals WHERE slug = 'acme'")
    return {str(row["kind"]): row for row in rows}


def average(values: list[float], window: int, at: int) -> float:
    return sum(values[at - window + 1 : at + 1]) / window


def last_change(flags: list[bool | None]) -> int | None:
    """Index of the last bar whose flag differs from the bar before (both known)."""
    changes = [
        i
        for i in range(1, len(flags))
        if flags[i] is not None and flags[i - 1] is not None and flags[i] != flags[i - 1]
    ]
    return changes[-1] if changes else None


def test_a_slide_after_a_rally(listed: Store) -> None:
    closes = [50 + 50 * i / 279 for i in range(280)] + [100 - 25 * (i + 1) / 20 for i in range(20)]
    dates = load_closes(listed, closes)
    row = price_signals(listed)
    assert row["price_date"] == dates[-1]
    assert row["drawdown_pct"] == pytest.approx(-25.0)  # 75 against the high of 100
    assert row["return_1m_pct"] == pytest.approx((75 / closes[-22] - 1) * 100)
    assert row["sma200"] == pytest.approx(average(closes, 200, len(closes) - 1))
    above = [None] * 199 + [closes[i] > average(closes, 200, i) for i in range(199, len(closes))]
    crossed = last_change(above)
    assert crossed is not None
    assert row["sma200_cross_date"] == dates[crossed]
    assert row["sma200_cross_bars_ago"] == len(closes) - 1 - crossed
    assert (row["above_sma200"], row["new_high_date"]) == (False, dates[279])

    kinds = signal_kinds(listed)
    assert {"drawdown", "big_move_1m", "crossed_below_sma200"} <= set(kinds)
    assert "new_52w_high" not in kinds  # 20 sessions ago, outside the last 5
    assert kinds["drawdown"]["standing"] is True
    assert kinds["drawdown"]["title"] == "25.0% below its 52-week high of 100.00 USD"
    assert (kinds["crossed_below_sma200"]["standing"], kinds["crossed_below_sma200"]["date"]) == (
        False,
        dates[crossed],
    )


def test_a_recovery_to_a_new_high(listed: Store) -> None:
    closes = [100 - 40 * i / 229 for i in range(230)] + [60 + 70 * (i + 1) / 40 for i in range(40)]
    dates = load_closes(listed, closes)
    row = price_signals(listed)
    golden = [None] * 199 + [
        average(closes, 50, i) > average(closes, 200, i) for i in range(199, len(closes))
    ]
    crossed = last_change(golden)
    assert crossed is not None
    assert (row["ma_cross"], row["ma_cross_date"]) == ("golden", dates[crossed])
    assert (row["new_high_date"], row["new_high_bars_ago"]) == (dates[-1], 0)
    assert signal_kinds(listed)["new_52w_high"]["title"] == "New 52-week high: 130.00 USD"


def test_volume_against_its_usual_level(listed: Store) -> None:
    closes = [100.0] * 120
    load_closes(listed, closes, [1e6] * 115 + [3e6] * 5)
    row = price_signals(listed)
    assert row["volume_ratio"] == pytest.approx(3.0)
    assert row["sma200"] is None  # fewer than 200 sessions
    assert row["new_high_date"] is None  # not a year of history to beat
    assert set(signal_kinds(listed)) == {"volume_spike"}


def test_growth_and_margin_against_the_year_before(listed: Store) -> None:
    listed.replace_facts("acme", [*acme_facts(), *acme_prior_year()])
    (row,) = listed.query("SELECT * FROM fundamental_signals WHERE slug = 'acme'")
    assert row["revenue_growth_pct"] == pytest.approx(25.0)  # 2.0bn against 1.6bn
    assert row["net_income_growth_pct"] == pytest.approx(100.0)
    assert row["op_margin_change_pp"] == pytest.approx(-15.0)  # 25% down to 10%
    assert row["fcf_margin_pct"] == pytest.approx(20.0)
    assert row["revenue_cagr_3y_pct"] is None
    kinds = signal_kinds(listed)
    assert set(kinds) == {"revenue_growing", "margin_down"}
    assert kinds["margin_down"]["title"] == "Operating margin -15.0 points in fiscal 2024, to 10.0%"
    assert (kinds["margin_down"]["date"], kinds["margin_down"]["standing"]) == (
        date(2024, 12, 31),
        True,
    )


def test_one_year_has_nothing_to_compare(listed: Store) -> None:
    listed.replace_facts("acme", acme_facts())
    (row,) = listed.query("SELECT * FROM fundamental_signals WHERE slug = 'acme'")
    assert (row["revenue_growth_pct"], row["op_margin_change_pp"]) == (None, None)
    assert signal_kinds(listed) == {}


def form4(name: str, accession: str, filed: date) -> list[InsiderTrade]:
    entry = edgar.Entry(
        cik=1234567, accession=accession, filed=filed, form="4", items="", period=None,
        document=f"xslF345X06/{name}.xml",
    )  # fmt: skip
    return edgar.parse_form4("acme", entry, fixture_bytes(f"{name}.xml"))


def test_insider_buys_and_large_sales(loaded: Store) -> None:
    sales = form4("form4_sales", "0001234567-26-000009", date(2026, 10, 15))
    small = sales[0].model_copy(
        update={"id": "0001234567-26-000011", "person": "POE SAM", "shares": 100.0}
    )
    loaded.upsert_insider_trades([
        *sales, small, *form4("form4_buy", "0001234567-26-000010", date(2026, 10, 20)),
    ])  # fmt: skip
    rows = loaded.query("SELECT * FROM signals WHERE category = 'insider' ORDER BY kind")
    assert [row["kind"] for row in rows] == ["insider_buy", "insider_sale"]  # POE's is small
    buy, sale = rows
    assert buy["title"] == "DOE JANE (Director) bought 1,000 shares (about EUR 45k) at 49.50 USD"
    assert buy["value"] == pytest.approx(45_000)  # 49,500 USD at 1.1 to the euro
    assert (buy["date"], buy["standing"]) == (date(2026, 10, 16), False)
    # 3,000 at 100 and 2,000 at 101 are 502,000 USD; the shares withheld for tax are no sale
    assert sale["title"] == (
        "ROE RICHARD (Chief Financial Officer) sold 5,000 shares (about EUR 456k) "
        "in October 2026, under a 10b5-1 plan"
    )
    assert sale["date"] == date(2026, 10, 14)
    assert str(sale["url"]).endswith("/000123456726000009/xslF345X06/form4_sales.xml")
    (trade,) = loaded.query("SELECT * FROM insider_trades WHERE code = 'F'")
    assert (trade["trade_type"], trade["value"]) == ("tax withholding", 50_500)


def test_filings_become_signals(loaded: Store) -> None:
    entries = edgar.parse_submissions(
        1234567, fixture_bytes("edgar_submissions.json"), date(2025, 11, 1)
    )
    loaded.upsert_filings(edgar.filings_of("acme", entries))
    rows = loaded.query("SELECT * FROM signals WHERE category = 'filing' ORDER BY date DESC")
    # the 6-K stays in raw_filings only: a foreign issuer files any press release as one
    assert [(row["kind"], row["title"]) for row in rows] == [
        ("current_report", "8-K: results; officer or director change"),
        ("new_report", "10-Q for the period ended 2026-06-30"),
        ("major_holder", "SCHEDULE 13G/A: a passive holder of 5% or more"),
    ]
    assert str(rows[0]["url"]).endswith("/000123456726000008/acme-8k.htm")


def test_an_amended_form4_replaces_the_trades_it_restates(loaded: Store) -> None:
    original = form4("form4_buy", "0001234567-26-000010", date(2026, 10, 20))
    amended = [
        trade.model_copy(update={"id": "0001234567-26-000012", "amendment": True, "price": 55.0})
        for trade in original
    ]
    loaded.upsert_insider_trades([*original, *amended])
    rows = loaded.query("SELECT id, price FROM insider_trades WHERE NOT derivative")
    assert [(row["id"], row["price"]) for row in rows] == [("0001234567-26-000012", 55.0)]
    (buy,) = loaded.query("SELECT title FROM signals WHERE kind = 'insider_buy'")
    assert str(buy["title"]).endswith("at 55.00 USD")
