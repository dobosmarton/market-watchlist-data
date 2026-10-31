"""DuckDB / DuckLake storage. Raw tables are written by sources; marts are SQL views on top."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING, Self

import duckdb

from wl.concepts import concept_rows

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence
    from types import TracebackType

    from pydantic import BaseModel

    from wl.config import Company
    from wl.models import Fact, FxRate, PriceBar
    from wl.settings import Settings

type Row = dict[str, object]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fx (
    date DATE NOT NULL, base VARCHAR NOT NULL, quote VARCHAR NOT NULL,
    rate DOUBLE NOT NULL, source VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (date, base, quote)
);
CREATE TABLE IF NOT EXISTS raw_prices (
    slug VARCHAR NOT NULL, date DATE NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE NOT NULL, volume DOUBLE,
    ccy VARCHAR NOT NULL, source VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (slug, date)
);
CREATE TABLE IF NOT EXISTS raw_facts (
    slug VARCHAR NOT NULL, taxonomy VARCHAR NOT NULL, tag VARCHAR NOT NULL, unit VARCHAR NOT NULL,
    period_start DATE, period_end DATE NOT NULL, value DOUBLE NOT NULL,
    fiscal_year INTEGER, fiscal_period VARCHAR, form VARCHAR, filed DATE, accession VARCHAR,
    source VARCHAR NOT NULL, source_url VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    slug VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, grp VARCHAR NOT NULL,
    quote_ccy VARCHAR NOT NULL, reporting_ccy VARCHAR NOT NULL,
    fiscal_year_end_month INTEGER NOT NULL
);
"""

_MART_FILES = ("fin_annual.sql", "fin_gaps.sql", "price_stats.sql", "valuation.sql")


class Store:
    """A DuckDB connection with typed writes. Once open, the raw tables and marts exist."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self._con.execute(_SCHEMA)
        self._create_marts()

    @classmethod
    def open(cls, settings: Settings) -> Self:
        return cls(_connect(settings))

    @classmethod
    def memory(cls) -> Self:
        return cls(duckdb.connect(":memory:"))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._con.close()

    # ------------------------------------------------------------------ writes
    def sync_companies(self, companies: Iterable[Company]) -> None:
        rows = [
            (c.slug, c.name, c.group, c.listing.ccy, c.reporting_ccy, c.fiscal_year_end_month)
            for c in companies
        ]
        with self._transaction():
            self._con.execute("DELETE FROM companies")
            self._con.executemany("INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?)", rows)

    def upsert_fx(self, rates: Sequence[FxRate]) -> int:
        return self._write("INSERT OR REPLACE INTO raw_fx", rates)

    def upsert_prices(self, bars: Sequence[PriceBar]) -> int:
        return self._write("INSERT OR REPLACE INTO raw_prices", bars)

    def replace_facts(self, slug: str, facts: Sequence[Fact]) -> int:
        """Replace all facts for one company (sources return full history each time)."""
        with self._transaction():
            self._con.execute("DELETE FROM raw_facts WHERE slug = ?", [slug])
            return self._write("INSERT INTO raw_facts", facts)

    def _write(self, insert_into: str, records: Sequence[BaseModel]) -> int:
        """Insert records into a raw table whose columns are the record fields + fetched_at."""
        if not records:
            return 0
        columns = [*type(records[0]).model_fields, "fetched_at"]
        placeholders = ", ".join("?" for _ in columns)
        fetched_at = datetime.now(UTC).replace(tzinfo=None)
        rows = [(*record.model_dump().values(), fetched_at) for record in records]
        sql = f"{insert_into} ({', '.join(columns)}) VALUES ({placeholders})"
        self._con.executemany(sql, rows)
        return len(rows)

    @contextmanager
    def _transaction(self) -> Generator[None]:
        self._con.begin()
        try:
            yield
        except BaseException:
            self._con.rollback()
            raise
        self._con.commit()

    # ------------------------------------------------------------------ reads
    def query(self, sql: str, params: Sequence[object] = ()) -> list[Row]:
        cursor = self._con.execute(sql, list(params))
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def unmapped_tags(self, slug: str, limit: int) -> list[Row]:
        """List a company's most frequent annual tags that ``concepts.py`` does not map yet."""
        return self.query(
            """
            SELECT f.taxonomy, f.tag, any_value(f.unit) AS unit, count(*) AS n
            FROM raw_facts AS f
            ANTI JOIN concept_map AS m ON m.taxonomy = f.taxonomy AND m.tag = f.tag
            WHERE f.slug = ? AND f.taxonomy IN ('us-gaap', 'ifrs-full')
            GROUP BY ALL ORDER BY n DESC LIMIT ?
            """,
            [slug, limit],
        )

    def gaps(self) -> list[Row]:
        """Companies whose latest fiscal year misses concepts (``period_end`` None: no years)."""
        return self.query(
            "SELECT slug, period_end, array_to_string(missing, ', ') AS missing FROM fin_gaps"
        )

    # ------------------------------------------------------------------ marts
    def _create_marts(self) -> None:
        """(Re)create the mart views so they always match the code. SQLMesh: milestone 2."""
        self._con.execute(
            "CREATE OR REPLACE TABLE concept_map "
            "(concept VARCHAR, taxonomy VARCHAR, tag VARCHAR, priority INTEGER, kind VARCHAR)"
        )
        self._con.executemany("INSERT INTO concept_map VALUES (?, ?, ?, ?, ?)", concept_rows())
        sql_dir = resources.files("wl") / "sql"
        for name in _MART_FILES:
            self._con.execute((sql_dir / name).read_text(encoding="utf-8"))


def _connect(settings: Settings) -> duckdb.DuckDBPyConnection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.storage == "duckdb":
        return duckdb.connect(str(settings.db_path))
    con = duckdb.connect()
    catalog = settings.data_dir / "catalog.sqlite"
    files = settings.data_dir / "lake"
    con.execute("INSTALL ducklake; INSTALL sqlite; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS lake (DATA_PATH '{files}/')")
    con.execute("USE lake")
    return con
