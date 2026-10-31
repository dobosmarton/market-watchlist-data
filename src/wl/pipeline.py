"""Refresh jobs. Each one fetches, validates and writes one kind of data.

Per-company jobs report a company's failure and carry on; one company never stops the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Protocol

import httpx

from wl.log import get_logger
from wl.models import DateRange, Fact, PriceBar
from wl.sources import fx
from wl.sources.base import MissingIdentifierError, ProviderError
from wl.sources.esef import EsefProvider
from wl.sources.manual import ManualProvider
from wl.sources.prices import TwelveDataProvider, YahooProvider
from wl.sources.sec import SecProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from wl.config import Company, Watchlist
    from wl.models import FxRate
    from wl.settings import Settings
    from wl.sources.base import Provider
    from wl.store import Store

log = get_logger(__name__)


class Job(StrEnum):
    FX = "fx"
    PRICES = "prices"
    FINANCIALS = "financials"


@dataclass
class RunReport:
    job: Job
    rows: int = 0
    ok: list[str] = field(default_factory=list[str])
    failed: dict[str, str] = field(default_factory=dict[str, str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])

    def summary(self) -> str:
        parts = [f"{self.job}: {self.rows} rows", f"{len(self.ok)} ok"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.failed:
            parts.append(f"{len(self.failed)} failed ({', '.join(sorted(self.failed))})")
        return ", ".join(parts)

    def lines(self) -> list[str]:
        """Render the summary, then one line per failed and skipped company."""
        return [
            self.summary(),
            *(f"  failed  {slug}: {reason}" for slug, reason in sorted(self.failed.items())),
            *(f"  skipped {slug}: {reason}" for slug, reason in sorted(self.skipped.items())),
        ]


class RefreshJob(Protocol):
    def run(self, watchlist: Watchlist) -> RunReport: ...


@dataclass(frozen=True)
class FxJob:
    """ECB rates for every currency on the watchlist, in one request."""

    fetch: Callable[[set[str]], list[FxRate]]
    write: Callable[[Sequence[FxRate]], int]

    def run(self, watchlist: Watchlist) -> RunReport:
        currencies = watchlist.currencies()
        rows = self.write(self.fetch(currencies))
        return RunReport(Job.FX, rows=rows, ok=sorted(currencies))


@dataclass(frozen=True)
class CompanyJob[T]:
    """Fetches one kind of record company by company, from the source each company names."""

    job: Job
    source_of: Callable[[Company], str]
    providers: Mapping[str, Provider[T]]
    write: Callable[[Company, Sequence[T]], int]

    def run(self, watchlist: Watchlist) -> RunReport:
        report = RunReport(self.job)
        for company in watchlist.companies:
            self._refresh(company, report)
        return report

    def _refresh(self, company: Company, report: RunReport) -> None:
        source = self.source_of(company)
        provider = self.providers.get(source)
        if provider is None:
            report.skipped[company.slug] = f"no {source} provider configured"
            return
        try:
            records = provider.fetch(company)
        except MissingIdentifierError as exc:
            report.skipped[company.slug] = str(exc)
            return
        except (ProviderError, httpx.HTTPError) as exc:
            report.failed[company.slug] = str(exc)
            log.warning("refresh.failed", job=self.job, slug=company.slug, error=str(exc))
            return
        report.rows += self.write(company, records)
        report.ok.append(company.slug)


def build_jobs(store: Store, client: httpx.Client, settings: Settings) -> dict[Job, RefreshJob]:
    """Wire the real sources to the store, in the order the jobs should run."""
    window = DateRange.last_days(settings.price_history_days)
    prices = CompanyJob[PriceBar](
        Job.PRICES,
        source_of=lambda company: company.sources.prices,
        providers=_price_providers(client, settings, window),
        write=lambda _company, bars: store.upsert_prices(bars),
    )
    financials = CompanyJob[Fact](
        Job.FINANCIALS,
        source_of=lambda company: company.sources.financials,
        providers={
            "sec": SecProvider(client),
            "esef": EsefProvider(client),
            "manual": ManualProvider(settings.manual_dir),
        },
        write=lambda company, facts: store.replace_facts(company.slug, facts),
    )
    return {
        Job.FX: FxJob(fetch=partial(fx.fetch_rates, client, window), write=store.upsert_fx),
        Job.PRICES: prices,
        Job.FINANCIALS: financials,
    }


def _price_providers(
    client: httpx.Client, settings: Settings, window: DateRange
) -> dict[str, Provider[PriceBar]]:
    providers: dict[str, Provider[PriceBar]] = {"yahoo": YahooProvider(window)}
    if settings.twelvedata_api_key is not None:
        api_key = settings.twelvedata_api_key.get_secret_value()
        providers["twelvedata"] = TwelveDataProvider(client, api_key, window)
    return providers
