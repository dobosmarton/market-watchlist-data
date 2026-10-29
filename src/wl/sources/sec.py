"""US filers: XBRL company facts from SEC EDGAR (free, no key; send a User-Agent)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from wl.http import get_bytes
from wl.models import Fact
from wl.sources.base import MissingIdentifierError

if TYPE_CHECKING:
    from collections.abc import Iterator

    import httpx

    from wl.config import Company

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SOURCE = "sec"
# Annual filing forms whose facts we keep: domestic, foreign private issuer, Canadian MJDS.
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
TAXONOMIES = frozenset({"us-gaap", "ifrs-full", "dei"})


class _Observation(BaseModel):
    start: date | None = None
    end: date
    val: float
    accn: str
    fy: int | None = None
    fp: str | None = None
    form: str
    filed: date


class _Concept(BaseModel):
    units: dict[str, list[_Observation]] = Field(default_factory=dict)


class _CompanyFacts(BaseModel):
    cik: int
    entity_name: str = Field(alias="entityName")
    facts: dict[str, dict[str, _Concept]]


class SecProvider:
    name = SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch(self, company: Company) -> list[Fact]:
        if company.cik is None:
            msg = "no cik in watchlist.yaml"
            raise MissingIdentifierError(msg)
        url = URL.format(cik=company.cik)
        return parse_company_facts(company, get_bytes(self._client, url), url)


def parse_company_facts(company: Company, raw: bytes, source_url: str) -> list[Fact]:
    """Keep facts from annual filings only; quarterly data comes from releases (milestone 2)."""
    payload = _CompanyFacts.model_validate_json(raw)
    return [
        Fact(
            slug=company.slug,
            taxonomy=taxonomy,
            tag=tag,
            unit=unit,
            period_start=obs.start,
            period_end=obs.end,
            value=obs.val,
            fiscal_year=obs.fy,
            fiscal_period=obs.fp,
            form=obs.form,
            filed=obs.filed,
            accession=obs.accn,
            source=SOURCE,
            source_url=source_url,
        )
        for taxonomy, tag, unit, obs in _annual_observations(payload)
    ]


def _annual_observations(payload: _CompanyFacts) -> Iterator[tuple[str, str, str, _Observation]]:
    return (
        (taxonomy, tag, unit, obs)
        for taxonomy, concepts in payload.facts.items()
        if taxonomy in TAXONOMIES
        for tag, concept in concepts.items()
        for unit, observations in concept.units.items()
        for obs in observations
        if obs.form in ANNUAL_FORMS
    )
