"""EU/EEA annual reports in ESEF format, via filings.xbrl.org (JSON:API + xBRL-JSON).

Each filing has an xBRL-JSON rendering produced by Arelle. We keep numeric facts with no
dimensions beyond concept/entity/period/unit, i.e. the consolidated face-statement values.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from wl.http import get_bytes
from wl.log import get_logger
from wl.models import Fact

if TYPE_CHECKING:
    import httpx

    from wl.config import Company

BASE = "https://filings.xbrl.org"
SOURCE = "esef"
_CORE_DIMENSIONS = frozenset({"concept", "entity", "period", "unit", "language"})
log = get_logger(__name__)


# ---------------------------------------------------------------- filing index (JSON:API)
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


def _added(filing: _FilingAttributes) -> str:
    return filing.date_added.isoformat() if filing.date_added else ""


def list_filings(client: httpx.Client, lei: str, limit: int = 10) -> list[_FilingAttributes]:
    """Most recent filing per period end, newest first. Filter syntax: verify on first live run."""
    params = {
        "filter[entity.identifier]": lei,
        "sort": "-period_end",
        "page[size]": str(limit),
    }
    page = _FilingsPage.model_validate_json(get_bytes(client, f"{BASE}/api/filings", params))
    best: dict[date, _FilingAttributes] = {}
    for resource in page.data:
        attrs = resource.attributes
        if attrs.json_url is None:
            continue
        current = best.get(attrs.period_end)
        if current is None or _added(attrs) > _added(current):
            best[attrs.period_end] = attrs
    return [best[k] for k in sorted(best, reverse=True)]


# ---------------------------------------------------------------- xBRL-JSON facts
class _OimFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)


class _OimReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: dict[str, _OimFact]


def parse_period(text: str) -> tuple[date | None, date]:
    """OIM periods: an instant ``2024-12-31T00:00:00`` or a duration ``start/end``.

    Instants and duration ends at midnight mean "end of the previous day".
    """

    def to_date(stamp: str) -> date:
        moment = datetime.fromisoformat(stamp)
        if moment.time() == datetime.min.time() and "T" in stamp:
            return (moment - timedelta(days=1)).date()
        return moment.date()

    if "/" in text:
        start_text, end_text = text.split("/", 1)
        return datetime.fromisoformat(start_text).date(), to_date(end_text)
    return None, to_date(text)


def parse_oim(
    company: Company, raw: bytes, source_url: str, filing: _FilingAttributes
) -> list[Fact]:
    report = _OimReport.model_validate_json(raw)
    facts: list[Fact] = []
    for fact in report.facts.values():
        dims = fact.dimensions
        unit = dims.get("unit")
        concept = dims.get("concept")
        period = dims.get("period")
        if fact.value is None or unit is None or concept is None or period is None:
            continue
        if set(dims) - _CORE_DIMENSIONS:  # segment / axis breakdowns
            continue
        try:
            value = float(fact.value)
        except ValueError:
            continue
        prefix, _, tag = concept.partition(":")
        start, end = parse_period(period)
        facts.append(
            Fact(
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
                filed=filing.date_added.date() if filing.date_added else None,
                accession=filing.fxo_id,
                source=SOURCE,
                source_url=source_url,
            )
        )
    return facts


def fetch_facts(client: httpx.Client, company: Company, max_filings: int = 5) -> list[Fact]:
    if company.lei is None:
        log.warning("esef.skip_no_lei", slug=company.slug, hint="run `wl resolve-ids`")
        return []
    facts: list[Fact] = []
    for filing in list_filings(client, company.lei)[:max_filings]:
        if filing.json_url is None:
            continue
        url = filing.json_url if filing.json_url.startswith("http") else BASE + filing.json_url
        facts.extend(parse_oim(company, get_bytes(client, url), url, filing))
    return facts
