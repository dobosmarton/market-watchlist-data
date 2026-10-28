"""Typed records that flow from sources into storage."""

from __future__ import annotations

from datetime import date, datetime

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


class FetchMeta(_Record):
    source: str
    fetched_at: datetime
