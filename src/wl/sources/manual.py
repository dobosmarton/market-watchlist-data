"""Annual figures typed in by hand from a company's report, for filers no XBRL source covers.

One YAML file per company in the manual directory (``financials/<slug>.yaml``, gitignored);
``financials.example/`` shows the format. Figures become facts under the ``manual`` taxonomy,
which ``concepts.py`` maps onto every concept at the lowest priority.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wl.concepts import INSTANTS, MANUAL_TAXONOMY, Concept
from wl.models import Fact
from wl.sources.base import MissingIdentifierError, ProviderError

if TYPE_CHECKING:
    from pathlib import Path

    from wl.config import Company

SOURCE = "manual"
_SCALE = {"units": 1.0, "thousands": 1e3, "millions": 1e6}
_SHARE_CONCEPTS = frozenset({Concept.SHARES_DILUTED})


class _Year(BaseModel):
    """One fiscal year; the keys are the concept names from ``concepts.py``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period_end: date
    source_url: str
    revenue: float
    op_income: float | None = None
    net_income: float  # attributable to owners of the parent
    cfo: float | None = None
    capex: float | None = None
    dna: float | None = None
    cash: float | None = None
    debt_total: float | None = None  # borrowings, excluding lease liabilities
    debt_noncurrent: float | None = None
    debt_current: float | None = None
    shares_diluted: float | None = None  # diluted weighted average
    eps_diluted: float | None = None  # in currency units, never scaled


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    money_unit: Literal["units", "thousands", "millions"]
    shares_unit: Literal["units", "thousands", "millions"]
    years: tuple[_Year, ...] = Field(min_length=1)


class ManualProvider:
    name = SOURCE

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def fetch(self, company: Company) -> list[Fact]:
        path = self._directory / f"{company.slug}.yaml"
        if not path.exists():
            msg = f"no {path} (see financials.example/)"
            raise MissingIdentifierError(msg)
        return parse_report(company, path)


def parse_report(company: Company, path: Path) -> list[Fact]:
    try:
        report = _Report.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        msg = f"{path}: {exc}"
        raise ProviderError(msg) from exc
    _check(company, report, path)
    return [fact for year in report.years for fact in _facts(company, report, year, path)]


def _check(company: Company, report: _Report, path: Path) -> None:
    if report.currency != company.reporting_ccy:
        msg = f"{path}: currency {report.currency} is not reporting_ccy {company.reporting_ccy}"
        raise ProviderError(msg)
    for year in report.years:
        if year.period_end.month != company.fiscal_year_end_month:
            msg = (
                f"{path}: period_end {year.period_end} does not end in fiscal_year_end_month "
                f"{company.fiscal_year_end_month}"
            )
            raise ProviderError(msg)


def _facts(company: Company, report: _Report, year: _Year, path: Path) -> list[Fact]:
    values = year.model_dump(exclude={"period_end", "source_url"}, exclude_none=True)
    facts: list[Fact] = []
    for name, value in values.items():
        concept = Concept(name)
        unit, scale = _unit(report, concept)
        facts.append(
            Fact(
                slug=company.slug,
                taxonomy=MANUAL_TAXONOMY,
                tag=concept.value,
                unit=unit,
                period_start=None if concept in INSTANTS else _year_start(year.period_end),
                period_end=year.period_end,
                value=value * scale,
                fiscal_year=year.period_end.year,
                fiscal_period="FY",
                form=SOURCE,
                accession=path.name,
                source=SOURCE,
                source_url=year.source_url,
            )
        )
    return facts


def _unit(report: _Report, concept: Concept) -> tuple[str, float]:
    """Return the fact unit and the multiplier that turns the file's figure into it."""
    if concept is Concept.EPS_DILUTED:
        return f"{report.currency}/shares", 1.0
    if concept in _SHARE_CONCEPTS:
        return "shares", _SCALE[report.shares_unit]
    return report.currency, _SCALE[report.money_unit]


def _year_start(end: date) -> date:
    """Return the first day of the fiscal year that ends on ``end`` (29 Feb counts as 28)."""
    try:
        previous = end.replace(year=end.year - 1)
    except ValueError:
        previous = end.replace(year=end.year - 1, day=28)
    return previous + timedelta(days=1)
