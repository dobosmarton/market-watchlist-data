"""Typed records that flow from sources into storage, and the values sources are asked for."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FxRate(_Record):
    date: date
    base: str
    quote: str
    rate: float
    source: str


class PriceBar(_Record):
    slug: str
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    ccy: str
    source: str


class Fact(_Record):
    """One reported XBRL value: a concept for a period, in a unit."""

    slug: str
    taxonomy: str  # us-gaap | ifrs-full | dei | company extension prefix
    tag: str
    unit: str  # USD, EUR, shares, ...
    period_start: date | None  # None for instant (balance-sheet) facts
    period_end: date
    value: float
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # FY, Q1.. (SEC only)
    form: str | None = None  # 10-K, 40-F, ESEF
    filed: date | None = None
    accession: str | None = None
    source: str
    source_url: str


class Filing(_Record):
    """A regulatory filing about a company: an SEC form, later also exchange announcements."""

    slug: str
    source: str
    id: str  # unique within the source: the SEC accession number
    date: date  # filed or published
    form: str  # SEC form type (8-K, 10-Q, SCHEDULE 13G, ...)
    items: str | None = None  # 8-K item numbers, e.g. "2.02,9.01"
    period: date | None = None  # the period a report covers
    title: str
    url: str


class InsiderTrade(_Record):
    """One transaction an insider reported, such as a line of an SEC Form 4."""

    slug: str
    source: str
    id: str  # the filing's id; with `line`, unique within the source
    line: int
    filed: date
    trade_date: date
    person: str
    role: str  # officer title, Director, 10% owner
    code: str  # SEC transaction code: P buy, S sale, A award, M exercise, F tax, G gift ...
    acquired: bool
    shares: float
    price: float | None
    ccy: str
    owned_after: float | None
    planned: bool  # under a Rule 10b5-1 trading plan
    derivative: bool  # options, units and the like rather than the shares themselves
    amendment: bool  # from an amended filing (4/A), which restates the original's trades
    security: str
    url: str


@dataclass(frozen=True)
class DateRange:
    """Inclusive range of calendar days."""

    start: date
    end: date

    @classmethod
    def last_days(cls, days: int) -> DateRange:
        today = datetime.now(UTC).date()
        return cls(today - timedelta(days=days), today)
