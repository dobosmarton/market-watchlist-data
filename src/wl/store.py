"""DuckDB / DuckLake storage. Raw tables are written by sources; marts are SQL views on top."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING, Self

import duckdb

from wl.concepts import concept_rows

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import TracebackType

    from wl.models import Fact, FxRate, PriceBar
    from wl.settings import Settings

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

_MART_FILES = ("fin_annual.sql", "price_stats.sql", "valuation.sql")


class Store:
    """Thin wrapper around a DuckDB connection with typed write helpers."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        self.con.execute(_SCHEMA)

    @classmethod
    def open(cls, settings: Settings) -> Self:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.storage == "ducklake":
            con = duckdb.connect()
            catalog = settings.data_dir / "catalog.sqlite"
            files = settings.data_dir / "lake"
            con.execute("INSTALL ducklake; INSTALL sqlite; LOAD ducklake;")
            con.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS lake (DATA_PATH '{files}/')")
            con.execute("USE lake")
            return cls(con)
        return cls(duckdb.connect(str(settings.db_path)))

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
        self.con.close()

    # ------------------------------------------------------------------ writes
    def upsert_fx(self, rates: Iterable[FxRate]) -> int:
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [(r.date, r.base, r.quote, r.rate, r.source, now) for r in rates]
        if rows:
            self.con.executemany("INSERT OR REPLACE INTO raw_fx VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def upsert_prices(self, bars: Iterable[PriceBar]) -> int:
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [
            (b.slug, b.date, b.open, b.high, b.low, b.close, b.volume, b.ccy, b.source, now)
            for b in bars
        ]
        if rows:
            self.con.executemany(
                "INSERT OR REPLACE INTO raw_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        return len(rows)

    def replace_facts(self, slug: str, facts: Sequence[Fact]) -> int:
        """Replace all facts for one company (sources return full history each time)."""
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [
            (
                f.slug, f.taxonomy, f.tag, f.unit, f.period_start, f.period_end, f.value,
                f.fiscal_year, f.fiscal_period, f.form, f.filed, f.accession,
                f.source, f.source_url, now,
            )
            for f in facts
        ]  # fmt: skip
        self.con.execute("BEGIN")
        try:
            self.con.execute("DELETE FROM raw_facts WHERE slug = ?", [slug])
            if rows:
                self.con.executemany(
                    "INSERT INTO raw_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return len(rows)

    def sync_companies(self, rows: Iterable[tuple[str, str, str, str, str, int]]) -> None:
        self.con.execute("DELETE FROM companies")
        self.con.executemany("INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?)", list(rows))

    # ------------------------------------------------------------------ marts
    def build_marts(self) -> None:
        """(Re)create the mart views. SQLMesh replaces this in milestone 2."""
        self.con.execute(
            "CREATE OR REPLACE TABLE concept_map "
            "(concept VARCHAR, taxonomy VARCHAR, tag VARCHAR, priority INTEGER, kind VARCHAR)"
        )
        self.con.executemany("INSERT INTO concept_map VALUES (?, ?, ?, ?, ?)", concept_rows())
        sql_dir = resources.files("wl") / "sql"
        for name in _MART_FILES:
            self.con.execute((sql_dir / name).read_text(encoding="utf-8"))
