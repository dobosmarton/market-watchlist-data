from __future__ import annotations

import shutil
import warnings
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr

from wl.config import Company, Listing, Sources, Watchlist
from wl.marts import build_marts
from wl.models import Fact, FxRate, PriceBar
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
        watchlist_path=tmp_path / "watchlist.yaml",  # never the real list
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


def us_fact(tag: str, value: float, *, start: date | None, end: date, unit: str = "USD") -> Fact:
    return Fact(
        slug="acme", taxonomy="us-gaap", tag=tag, unit=unit, period_start=start, period_end=end,
        value=value, form="10-K", filed=date(2025, 2, 20), source="sec", source_url="u",
    )  # fmt: skip


def acme_facts(*, without: str | None = None) -> list[Fact]:
    """One complete fiscal year for ACME, optionally without one tag."""
    y0, y1 = date(2024, 1, 1), date(2024, 12, 31)
    facts = [
        us_fact("Revenues", 2_000_000_000, start=y0, end=y1),
        us_fact("OperatingIncomeLoss", 200_000_000, start=y0, end=y1),
        us_fact("NetIncomeLoss", 100_000_000, start=y0, end=y1),
        us_fact("NetCashProvidedByUsedInOperatingActivities", 500_000_000, start=y0, end=y1),
        us_fact("PaymentsToAcquirePropertyPlantAndEquipment", 100_000_000, start=y0, end=y1),
        us_fact("DepreciationDepletionAndAmortization", 100_000_000, start=y0, end=y1),
        us_fact("CashAndCashEquivalentsAtCarryingValue", 500_000_000, start=None, end=y1),
        us_fact("LongTermDebtNoncurrent", 1_000_000_000, start=None, end=y1),
        us_fact(
            "WeightedAverageNumberOfDilutedSharesOutstanding", 100_000_000,
            start=y0, end=y1, unit="shares",
        ),
        # a quarter and a foreign-currency value must be ignored
        us_fact("Revenues", 9e9, start=date(2024, 10, 1), end=y1),
        us_fact("Revenues", 9e9, start=y0, end=y1, unit="EUR"),
    ]  # fmt: skip
    return [fact for fact in facts if fact.tag != without]


def acme_prior_year() -> list[Fact]:
    """ACME's fiscal 2023: revenue 1.6bn at a 25% operating margin, net income 50m."""
    y0, y1 = date(2023, 1, 1), date(2023, 12, 31)
    return [
        us_fact("Revenues", 1_600_000_000, start=y0, end=y1),
        us_fact("OperatingIncomeLoss", 400_000_000, start=y0, end=y1),
        us_fact("NetIncomeLoss", 50_000_000, start=y0, end=y1),
    ]


@pytest.fixture
def loaded(store: Store, watchlist: Watchlist) -> Store:
    """ACME with one fiscal year of facts, two price bars, and EUR/USD."""
    store.sync_companies(watchlist.companies)
    store.replace_facts("acme", acme_facts())
    store.upsert_prices([
        PriceBar(slug="acme", date=date(2026, 10, d), open=None, high=h, low=lo, close=c,
                 volume=None, ccy="USD", source="twelvedata")
        for d, h, lo, c in ((26, 60.0, 40.0, 45.0), (27, 52.0, 48.0, 50.0))
    ])  # fmt: skip
    store.upsert_fx(
        [FxRate(date=date(2026, 10, 27), base="EUR", quote="USD", rate=1.1, source="t")]
    )
    return store


@pytest.fixture
def served(loaded: Store, settings: Settings) -> Settings:
    """Close the loaded store so the MCP server and live changes can open it themselves."""
    loaded.__exit__(None, None, None)
    return settings
