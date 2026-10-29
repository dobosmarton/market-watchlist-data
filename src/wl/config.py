"""The watchlist: which companies we track and where each one's data comes from."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from pathlib import Path

type PriceSource = Literal["twelvedata", "yahoo"]
type FinancialsSource = Literal["sec", "esef", "manual"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Listing(_Frozen):
    mic: str = Field(pattern=r"^[A-Z]{4}$")
    ticker: str
    ccy: str = Field(pattern=r"^[A-Z]{3}$")
    yahoo: str


class Sources(_Frozen):
    prices: PriceSource
    financials: FinancialsSource


class Company(_Frozen):
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    group: str
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    lei: str | None = Field(default=None, pattern=r"^[A-Z0-9]{18}[0-9]{2}$")
    cik: int | None = None
    fiscal_year_end_month: int = Field(default=12, ge=1, le=12)
    listing: Listing
    reporting_ccy: str = Field(pattern=r"^[A-Z]{3}$")
    sources: Sources

    @property
    def needs_lei(self) -> bool:
        """ESEF filings are found by LEI; `wl resolve-ids` looks it up from the ISIN."""
        return self.sources.financials == "esef" and self.lei is None


class Watchlist(_Frozen):
    companies: tuple[Company, ...]

    @field_validator("companies")
    @classmethod
    def _unique_slugs(cls, value: tuple[Company, ...]) -> tuple[Company, ...]:
        slugs = [c.slug for c in value]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            msg = f"duplicate slugs in watchlist: {', '.join(dupes)}"
            raise ValueError(msg)
        return value

    def get(self, slug: str) -> Company:
        for company in self.companies:
            if company.slug == slug:
                return company
        msg = f"unknown company slug: {slug}"
        raise KeyError(msg)

    def currencies(self) -> set[str]:
        return {c.listing.ccy for c in self.companies} | {c.reporting_ccy for c in self.companies}


def load_watchlist(path: Path) -> Watchlist:
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Watchlist.model_validate(raw)
