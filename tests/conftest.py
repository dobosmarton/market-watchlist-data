from __future__ import annotations

import shutil
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr

from wl.config import Company, Listing, Sources, Watchlist
from wl.marts import build_marts
from wl.settings import Settings
from wl.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterator

# SQLMesh and its dependencies raise deprecation warnings while importing; warnings are errors
# in the tests, so import it once here with them silenced. Warnings at run time still fail.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import sqlmesh  # noqa: F401  # pyright: ignore[reportUnusedImport]

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


@pytest.fixture(scope="session")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the marts once per session into a store file that each test copies."""
    template = Settings(
        data_dir=tmp_path_factory.mktemp("template") / "data",
        storage="duckdb",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with Store.open(template):
        pass
    build_marts(template)
    return template.db_path


@pytest.fixture
def store(settings: Settings, built_db: Path) -> Iterator[Store]:
    """Copy the built store into this test's data dir (the same file name keeps the catalog)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(built_db, settings.db_path)
    with Store.open(settings) as s:
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
