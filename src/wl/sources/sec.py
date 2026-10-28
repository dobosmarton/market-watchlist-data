"""US filers: XBRL company facts from SEC EDGAR (free, no key; send a User-Agent)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from wl.http import get_bytes
from wl.models import Fact

if TYPE_CHECKING:
    import httpx

    from wl.config import Company

URL = "https://data.sec.gov/api/xbrl/companyfacts/{cik}.json"
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


def parse_company_facts(company: Company, raw: bytes, source_url: str) -> list[Fact]:
    """Keep facts from annual filings only; quarterly data comes from releases (milestone 2)."""
    payload = _CompanyFacts.model_validate_json(raw)
    out: list[Fact] = []
    for taxonomy, concepts in payload.facts.items():
        if taxonomy not in TAXONOMIES:
            continue
        for tag, concept in concepts.items():
            for unit, observations in concept.units.items():
                out.extend(
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
                    for obs in observations
                    if obs.form in ANNUAL_FORMS
                )
    return out


def fetch_facts(client: httpx.Client, company: Company) -> list[Fact]:
    if company.cik_padded is None:
        return []
    url = URL.format(cik=company.cik_padded)
    return parse_company_facts(company, get_bytes(client, url), url)
