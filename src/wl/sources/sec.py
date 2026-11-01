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
# Filing forms whose facts we keep: annual reports (domestic, foreign private issuer, Canadian
# MJDS) and the quarterly 10-Q that marts.fin_ttm builds trailing twelve months from.
FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-Q", "10-Q/A"})
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
    """Keep facts from annual reports and 10-Qs in the taxonomies the concept map uses."""
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
        for taxonomy, tag, unit, obs in _observations(payload)
    ]


def _observations(payload: _CompanyFacts) -> Iterator[tuple[str, str, str, _Observation]]:
    return (
        (taxonomy, tag, unit, obs)
        for taxonomy, concepts in payload.facts.items()
        if taxonomy in TAXONOMIES
        for tag, concept in concepts.items()
        for unit, observations in concept.units.items()
        for obs in observations
        if obs.form in FORMS
    )


# ---------------------------------------------------------------------- company lookup
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


class SecMatch(BaseModel):
    """An SEC registrant that matches a lookup, for filling in a watchlist entry."""

    cik: int
    name: str
    ticker: str | None
    exchange: str | None
    fiscal_year_end_month: int | None = None


class _Tickers(BaseModel):
    data: list[tuple[int, str, str | None, str | None]]  # cik, name, ticker, exchange


class _Submissions(BaseModel):
    fiscal_year_end: str | None = Field(default=None, alias="fiscalYearEnd")  # e.g. "0926"


def search_tickers(
    client: httpx.Client, query: str, limit: int = 10, detailed: int = 3
) -> list[SecMatch]:
    """Exact ticker matches first, then names containing the query.

    The first ``detailed`` matches also get their fiscal year-end month (one request each).
    """
    rows = _Tickers.model_validate_json(get_bytes(client, TICKERS_URL)).data
    wanted = query.strip().upper()
    exact = [row for row in rows if (row[2] or "").upper() == wanted]
    named = [row for row in rows if wanted in row[1].upper() and row not in exact]
    matches = [SecMatch(cik=c, name=n, ticker=t, exchange=e) for c, n, t, e in exact + named]
    return [
        _with_year_end(client, match) if i < detailed else match
        for i, match in enumerate(matches[:limit])
    ]


def _with_year_end(client: httpx.Client, match: SecMatch) -> SecMatch:
    raw = get_bytes(client, SUBMISSIONS_URL.format(cik=match.cik))
    year_end = _Submissions.model_validate_json(raw).fiscal_year_end
    month = int(year_end[:2]) if year_end else None
    return match.model_copy(update={"fiscal_year_end_month": month})
