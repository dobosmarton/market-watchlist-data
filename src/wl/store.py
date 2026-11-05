"""DuckDB / DuckLake storage. Raw tables are written by sources; marts are views on top.

The ``marts`` schema is built by SQLMesh (``wl.marts.build_marts``) from ``wl/transform``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

import duckdb

from wl.concepts import concept_rows

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence
    from datetime import date
    from types import TracebackType

    from pydantic import BaseModel

    from wl.config import Company
    from wl.models import Fact, Filing, FxRate, InsiderTrade, PriceBar
    from wl.settings import Settings

type Row = dict[str, object]

_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS marts;  -- filled by SQLMesh; created here for the search path
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
CREATE TABLE IF NOT EXISTS raw_filings (
    slug VARCHAR NOT NULL, source VARCHAR NOT NULL, id VARCHAR NOT NULL, date DATE NOT NULL,
    form VARCHAR NOT NULL, items VARCHAR, period DATE, title VARCHAR NOT NULL,
    url VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (slug, source, id)
);
CREATE TABLE IF NOT EXISTS raw_insider_trades (
    slug VARCHAR NOT NULL, source VARCHAR NOT NULL, id VARCHAR NOT NULL, line INTEGER NOT NULL,
    filed DATE NOT NULL, trade_date DATE NOT NULL, person VARCHAR NOT NULL,
    role VARCHAR NOT NULL, code VARCHAR NOT NULL, acquired BOOLEAN NOT NULL,
    shares DOUBLE NOT NULL, price DOUBLE, ccy VARCHAR NOT NULL, owned_after DOUBLE,
    planned BOOLEAN NOT NULL, derivative BOOLEAN NOT NULL, amendment BOOLEAN NOT NULL,
    security VARCHAR NOT NULL, url VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (slug, source, id, line)
);
CREATE TABLE IF NOT EXISTS concept_map (
    concept VARCHAR NOT NULL, taxonomy VARCHAR NOT NULL, tag VARCHAR NOT NULL,
    priority INTEGER NOT NULL, kind VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    slug VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, grp VARCHAR NOT NULL,
    quote_ccy VARCHAR NOT NULL, reporting_ccy VARCHAR NOT NULL,
    fiscal_year_end_month INTEGER NOT NULL
);
"""


class Store:
    """A DuckDB connection with typed writes. Once open, the raw tables exist.

    Unqualified names resolve in ``main`` and then ``marts``, so queries can say ``valuation``.
    A read-only store creates nothing and can reach no files besides the database.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, *, read_only: bool = False) -> None:
        self._con = con
        if not read_only:
            self._con.execute(_SCHEMA)
        self._con.execute("SET search_path = 'main,marts'")
        if not read_only:
            self._sync_concept_map()

    @classmethod
    def open(cls, settings: Settings, *, read_only: bool = False) -> Self:
        return cls(_connect(settings, read_only=read_only), read_only=read_only)

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
            if rows:  # DuckDB 1.x rejects executemany with no rows
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

    def upsert_filings(self, filings: Sequence[Filing]) -> int:
        return self._write("INSERT OR REPLACE INTO raw_filings", filings)

    def upsert_insider_trades(self, trades: Sequence[InsiderTrade]) -> int:
        return self._write("INSERT OR REPLACE INTO raw_insider_trades", trades)

    def delete_company(self, slug: str) -> None:
        """Drop a company's data (it left the watchlist, or its add was undone)."""
        with self._transaction():
            for table in ("raw_prices", "raw_facts", "raw_filings", "raw_insider_trades"):
                self._con.execute(f"DELETE FROM {table} WHERE slug = ?", [slug])  # noqa: S608

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

    def select(self, sql: str, max_rows: int) -> tuple[list[Row], bool]:
        """Run one SELECT from outside the code; returns the first rows and whether more exist.

        Open the store read-only for untrusted SQL: that connection cannot write or reach files.
        """
        statements = self._con.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            msg = "only a single SELECT (or WITH ... SELECT) statement is allowed"
            raise ValueError(msg)
        cursor = self._con.execute(sql)
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        records = [dict(zip(names, values, strict=True)) for values in rows[:max_rows]]
        return records, len(rows) > max_rows

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

    def insider_filing_ids(self, slug: str, source: str) -> set[str]:
        """List the filings a company's stored insider trades came from, not to fetch them again."""
        rows = self.query(
            "SELECT DISTINCT id FROM raw_insider_trades WHERE slug = ? AND source = ?",
            [slug, source],
        )
        return {str(row["id"]) for row in rows}

    def gaps(self, today: date) -> list[Row]:
        """Companies whose latest fiscal year misses concepts or is out of date.

        A year is out of date once it ended over 16 months before ``today``: the next one has
        ended and its annual report is due within four months. ``period_end`` None: no years.
        """
        return self.query(
            """
            WITH latest AS (
                SELECT slug, max(period_end) AS period_end FROM marts.fin_annual GROUP BY slug
            ),
            dated AS (
                SELECT slug, period_end < ?::DATE - INTERVAL 16 MONTH AS outdated FROM latest
            )
            SELECT c.slug, coalesce(g.period_end, l.period_end) AS period_end,
                   array_to_string(list_concat(
                       coalesce(g.missing, []::VARCHAR[]),
                       CASE WHEN d.outdated THEN ['out of date'] ELSE []::VARCHAR[] END
                   ), ', ') AS missing
            FROM main.companies AS c
            LEFT JOIN marts.fin_gaps AS g USING (slug)
            LEFT JOIN latest AS l USING (slug)
            LEFT JOIN dated AS d USING (slug)
            WHERE g.slug IS NOT NULL OR d.outdated
            ORDER BY c.slug
            """,
            [today],
        )

    # ------------------------------------------------------------------ concept map
    def _sync_concept_map(self) -> None:
        """Rewrite ``concept_map`` from ``concepts.py`` so the marts always use the code's map."""
        with self._transaction():
            self._con.execute("DELETE FROM concept_map")
            self._con.executemany("INSERT INTO concept_map VALUES (?, ?, ?, ?, ?)", concept_rows())


def _connect(settings: Settings, *, read_only: bool) -> duckdb.DuckDBPyConnection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.storage == "duckdb":
        if read_only:  # no file, network or extension access beyond the database itself
            config: dict[str, str | bool | int | float | list[str]] = {
                "enable_external_access": False
            }
            return duckdb.connect(str(settings.db_path), read_only=True, config=config)
        return duckdb.connect(str(settings.db_path))
    con = duckdb.connect()
    catalog = settings.data_dir / "catalog.sqlite"
    files = settings.data_dir / "lake"
    mode = ", READ_ONLY" if read_only else ""
    con.execute("INSTALL ducklake; INSTALL sqlite; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS lake (DATA_PATH '{files}/'{mode})")
    con.execute("USE lake")
    if read_only:
        con.execute("SET enable_external_access = false")
    return con
