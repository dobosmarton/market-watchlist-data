from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr

from wl.config import Company, Listing, Sources, Watchlist
from wl.settings import Settings
from wl.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def one(store: Store, sql: str) -> tuple[Any, ...]:
    """Return the first row of a query as a tuple."""
    rows = store.query(sql)
    assert rows
    return tuple(rows[0].values())


def make_company(  # noqa: PLR0913
    slug: str = "acme",
    *,
    ccy: str = "USD",
    reporting_ccy: str = "USD",
    prices: str = "twelvedata",
    financials: str = "sec",
    cik: int | None = 1234567,
    lei: str | None = None,
    isin: str | None = None,
    mic: str = "XNYS",
) -> Company:
    return Company.model_validate(
        {
            "slug": slug,
            "name": slug.upper(),
            "group": "Test",
            "cik": cik,
            "lei": lei,
            "isin": isin,
            "listing": Listing(mic=mic, ticker=slug.upper(), ccy=ccy, yahoo=slug.upper()),
            "reporting_ccy": reporting_ccy,
            "sources": Sources.model_validate({"prices": prices, "financials": financials}),
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        export_dir=tmp_path / "exports",
        manual_dir=tmp_path / "financials",
        sec_user_agent="wl-tests test@example.com",
        twelvedata_api_key=SecretStr("test-key"),
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as c:
        yield c


@pytest.fixture
def store() -> Iterator[Store]:
    with Store.memory() as s:
        yield s


@pytest.fixture
def watchlist() -> Watchlist:
    return Watchlist(
        companies=(
            make_company("acme"),
            make_company(
                "euro-co", ccy="EUR", reporting_ccy="EUR", prices="yahoo", financials="esef",
                cik=None, lei="EXAMPLE0LEI000000001",
            ),
        )
    )  # fmt: skip
