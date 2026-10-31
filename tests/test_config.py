from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wl.config import Company, Watchlist, load_watchlist

from .conftest import make_company

EXAMPLE_WATCHLIST = Path(__file__).parent.parent / "watchlist.example.yaml"


def test_example_watchlist_loads() -> None:
    assert load_watchlist(EXAMPLE_WATCHLIST).get("aapl").fiscal_year_end_month == 9


def test_example_watchlist_shows_every_source() -> None:
    companies = load_watchlist(EXAMPLE_WATCHLIST).companies
    used = {c.sources.prices for c in companies} | {c.sources.financials for c in companies}
    assert used == {"twelvedata", "yahoo", "euronext", "sec", "esef", "manual"}


def test_every_example_sec_company_has_a_cik() -> None:
    companies = load_watchlist(EXAMPLE_WATCHLIST).companies
    assert all(c.cik for c in companies if c.sources.financials == "sec")


def test_esef_company_without_lei_needs_one() -> None:
    assert make_company(financials="esef", lei=None).needs_lei


def test_sec_company_needs_no_lei() -> None:
    assert not make_company(financials="sec", lei=None).needs_lei


def test_euronext_prices_need_a_euronext_listing() -> None:
    with pytest.raises(ValidationError, match="needs a Euronext listing"):
        make_company(prices="euronext", mic="XNYS")


def test_unknown_slug_raises() -> None:
    with pytest.raises(KeyError, match="unknown company slug"):
        Watchlist(companies=(make_company(),)).get("nope")


def test_duplicate_slugs_rejected() -> None:
    company: Company = make_company()
    with pytest.raises(ValidationError, match="duplicate slugs"):
        Watchlist(companies=(company, company))


def test_bad_isin_rejected() -> None:
    with pytest.raises(ValidationError):
        Company.model_validate({**make_company().model_dump(), "isin": "not-an-isin"})
