"""EU/EEA annual reports in ESEF format, via filings.xbrl.org (JSON:API + xBRL-JSON).

Each filing has an xBRL-JSON rendering produced by Arelle. We keep numeric facts with no
dimensions beyond concept/entity/period/unit, i.e. the consolidated face-statement values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from wl.http import get_bytes
from wl.models import Fact
from wl.sources.base import MissingIdentifierError

if TYPE_CHECKING:
    import httpx

    from wl.config import Company

BASE = "https://filings.xbrl.org"
SOURCE = "esef"
_CORE_DIMENSIONS = frozenset({"concept", "entity", "period", "unit", "language"})


class EsefProvider:
    name = SOURCE

    def __init__(self, client: httpx.Client, max_filings: int = 5) -> None:
        self._client = client
        self._max_filings = max_filings

    def fetch(self, company: Company) -> list[Fact]:
        if company.lei is None:
            msg = "no lei in watchlist.yaml (run `wl resolve-ids`)"
            raise MissingIdentifierError(msg)
        facts: list[Fact] = []
        for filing in list_filings(self._client, company.lei)[: self._max_filings]:
            raw = get_bytes(self._client, filing.report_url)
            facts.extend(parse_oim(company, raw, filing))
        return facts


# ---------------------------------------------------------------- filing index (JSON:API)
@dataclass(frozen=True)
class Filing:
    """One annual report that has an xBRL-JSON rendering we can read."""

    fxo_id: str
    period_end: date
    added: datetime | None
    report_url: str


class _FilingAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fxo_id: str
    country: str | None = None
    period_end: date
    date_added: datetime | None = None
    json_url: str | None = None
    error_count: int | None = None


class _FilingResource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    attributes: _FilingAttributes


class _FilingsPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_FilingResource]


def list_filings(client: httpx.Client, lei: str, limit: int = 10) -> list[Filing]:
    """Most recent filing per period end, newest first. Filter syntax: verify on first live run."""
    params = {
        "filter[entity.identifier]": lei,
        "sort": "-period_end",
        "page[size]": str(limit),
    }
    page = _FilingsPage.model_validate_json(get_bytes(client, f"{BASE}/api/filings", params))
    attributes = [resource.attributes for resource in page.data]
    readable = [
        Filing(a.fxo_id, a.period_end, a.date_added, urljoin(BASE, a.json_url))
        for a in attributes
        if a.json_url is not None
    ]
    latest: dict[date, Filing] = {}
    for filing in sorted(readable, key=_added_sort_key):
        latest[filing.period_end] = filing
    return [latest[period_end] for period_end in sorted(latest, reverse=True)]


def _added_sort_key(filing: Filing) -> str:
    return filing.added.isoformat() if filing.added else ""


# ---------------------------------------------------------------- xBRL-JSON facts
class _OimFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)


class _OimReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: dict[str, _OimFact]


def parse_oim(company: Company, raw: bytes, filing: Filing) -> list[Fact]:
    report = _OimReport.model_validate_json(raw)
    facts = (_to_fact(company, oim, filing) for oim in report.facts.values())
    return [fact for fact in facts if fact is not None]


def _to_fact(company: Company, oim: _OimFact, filing: Filing) -> Fact | None:
    """Convert to a Fact, or None for text values, breakdowns and incomplete facts."""
    dims = oim.dimensions
    unit, concept, period = dims.get("unit"), dims.get("concept"), dims.get("period")
    if oim.value is None or unit is None or concept is None or period is None:
        return None
    if set(dims) - _CORE_DIMENSIONS:  # segment / axis breakdowns
        return None
    try:
        value = float(oim.value)
    except ValueError:
        return None
    prefix, _, tag = concept.partition(":")
    start, end = parse_period(period)
    return Fact(
        slug=company.slug,
        taxonomy=prefix,
        tag=tag,
        unit=unit.removeprefix("iso4217:").replace("xbrli:", ""),
        period_start=start,
        period_end=end,
        value=value,
        fiscal_year=filing.period_end.year,
        fiscal_period="FY",
        form="ESEF",
        filed=filing.added.date() if filing.added else None,
        accession=filing.fxo_id,
        source=SOURCE,
        source_url=filing.report_url,
    )


def parse_period(text: str) -> tuple[date | None, date]:
    """OIM periods: an instant ``2024-12-31T00:00:00`` or a duration ``start/end``."""
    if "/" in text:
        start_text, end_text = text.split("/", 1)
        return datetime.fromisoformat(start_text).date(), _period_end(end_text)
    return None, _period_end(text)


def _period_end(stamp: str) -> date:
    """Instants and duration ends at midnight mean "end of the previous day"."""
    moment = datetime.fromisoformat(stamp)
    if "T" in stamp and moment.time() == datetime.min.time():
        return (moment - timedelta(days=1)).date()
    return moment.date()
