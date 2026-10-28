from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from wl.config import Watchlist, load_watchlist

from .conftest import make_company

if TYPE_CHECKING:
    from wl.config import Company

EXAMPLE_WATCHLIST = Path(__file__).parent.parent / "watchlist.example.yaml"


def test_example_watchlist_loads() -> None:
    assert load_watchlist(EXAMPLE_WATCHLIST).get("aapl").fiscal_year_end_month == 9


def test_example_watchlist_shows_every_source() -> None:
    companies = load_watchlist(EXAMPLE_WATCHLIST).companies
    used = {c.sources.prices for c in companies} | {c.sources.financials for c in companies}
    assert used == {"twelvedata", "yahoo", "sec", "esef", "manual"}


def test_every_example_sec_company_has_a_cik() -> None:
    companies = load_watchlist(EXAMPLE_WATCHLIST).companies
    assert all(c.cik for c in companies if c.sources.financials == "sec")


def test_cik_is_zero_padded() -> None:
    assert make_company(cik=1234).cik_padded == "CIK0000001234"
    assert make_company(cik=None).cik_padded is None


def test_unknown_slug_raises() -> None:
    with pytest.raises(KeyError, match="unknown company slug"):
        Watchlist(companies=(make_company(),)).get("nope")


def test_duplicate_slugs_rejected() -> None:
    company: Company = make_company()
    with pytest.raises(ValidationError, match="duplicate slugs"):
        Watchlist(companies=(company, company))


def test_bad_isin_rejected() -> None:
    with pytest.raises(ValidationError):
        make_company().model_copy(update={}).model_validate(
            {**make_company().model_dump(), "isin": "not-an-isin"}
        )
