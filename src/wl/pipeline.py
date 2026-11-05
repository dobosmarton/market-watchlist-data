"""Refresh jobs. Each one fetches, validates and writes one kind of data.

Per-company jobs report a company's failure and carry on; one company never stops the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Protocol

import httpx

from wl.config import Watchlist
from wl.http import make_client
from wl.log import get_logger
from wl.marts import build_marts
from wl.models import DateRange, Fact, Filing, InsiderTrade, PriceBar
from wl.sources import fx
from wl.sources.base import MissingIdentifierError, ProviderError
from wl.sources.edgar import EdgarFilingsProvider, EdgarInsidersProvider
from wl.sources.esef import EsefProvider
from wl.sources.manual import ManualProvider, WithManualYears
from wl.sources.prices import EuronextProvider, TwelveDataProvider, YahooProvider
from wl.sources.sec import SecProvider
from wl.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from wl.config import Company
    from wl.models import FxRate
    from wl.settings import Settings
    from wl.sources.base import Provider

log = get_logger(__name__)


class Job(StrEnum):
    FX = "fx"
    PRICES = "prices"
    FINANCIALS = "financials"
    FILINGS = "filings"
    INSIDERS = "insiders"


# What a watchlist change fetches for its company; filings and insiders wait for a refresh.
CORE_JOBS = (Job.FX, Job.PRICES, Job.FINANCIALS)


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
        """Render the summary, a line per failed company and one per reason for skipping."""
        skipped: dict[str, list[str]] = {}
        for slug, reason in sorted(self.skipped.items()):
            skipped.setdefault(reason, []).append(slug)
        return [
            self.summary(),
            *(f"  failed  {slug}: {reason}" for slug, reason in sorted(self.failed.items())),
            *(f"  skipped {', '.join(slugs)}: {reason}" for reason, slugs in skipped.items()),
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
            report.failed[company.slug] = describe_error(exc)
            log.warning(
                "refresh.failed", job=self.job, slug=company.slug, error=describe_error(exc)
            )
            return
        report.rows += self.write(company, records)
        report.ok.append(company.slug)


def describe_error(exc: Exception) -> str:
    """Say what failed without the request's query string, which can hold credentials.

    Only HTTP status errors quote the URL; transport errors describe the connection.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        url = exc.request.url.copy_with(query=None)
        return f"HTTP {exc.response.status_code} from {url}"
    return str(exc)


def run_refresh(
    settings: Settings,
    watchlist: Watchlist,
    jobs: Collection[Job] | None = None,
    *,
    slugs: Collection[str] | None = None,
) -> list[RunReport]:
    """Run some jobs, or all of them, then rebuild the marts once the store is closed.

    With ``slugs``, the per-company jobs cover those companies only; FX still covers every
    currency on the watchlist.
    """
    chosen = watchlist
    if slugs is not None:
        chosen = Watchlist(companies=tuple(c for c in watchlist.companies if c.slug in slugs))
    with Store.open(settings) as store, make_client(settings) as client:
        store.sync_companies(watchlist.companies)
        built = build_jobs(store, client, settings)
        selected = built.items() if jobs is None else [(job, built[job]) for job in jobs]
        reports = [
            selected_job.run(watchlist if name is Job.FX else chosen)
            for name, selected_job in selected
        ]
    build_marts(settings)
    return reports


def build_jobs(store: Store, client: httpx.Client, settings: Settings) -> dict[Job, RefreshJob]:
    """Wire the real sources to the store, in the order the jobs should run."""
    window = DateRange.last_days(settings.price_history_days)
    prices = CompanyJob[PriceBar](
        Job.PRICES,
        source_of=lambda company: company.sources.prices,
        providers=_price_providers(client, settings, window),
        write=lambda _company, bars: store.upsert_prices(bars),
    )
    manual = ManualProvider(settings.manual_dir)
    financials = CompanyJob[Fact](
        Job.FINANCIALS,
        source_of=lambda company: company.sources.financials,
        providers={
            "sec": WithManualYears(SecProvider(client), manual),
            "esef": WithManualYears(EsefProvider(client), manual),
            "manual": manual,
        },
        write=lambda company, facts: store.replace_facts(company.slug, facts),
    )
    since = DateRange.last_days(settings.filings_history_days).start
    filings = CompanyJob[Filing](
        Job.FILINGS,
        source_of=lambda _company: "sec",
        providers={"sec": EdgarFilingsProvider(client, since)},
        write=lambda _company, rows: store.upsert_filings(rows),
    )
    insiders = CompanyJob[InsiderTrade](
        Job.INSIDERS,
        source_of=lambda _company: "sec",
        providers={
            "sec": EdgarInsidersProvider(
                client, since, known=lambda slug: store.insider_filing_ids(slug, "sec")
            )
        },
        write=lambda _company, trades: store.upsert_insider_trades(trades),
    )
    return {
        Job.FX: FxJob(fetch=partial(fx.fetch_rates, client, window), write=store.upsert_fx),
        Job.PRICES: prices,
        Job.FINANCIALS: financials,
        Job.FILINGS: filings,
        Job.INSIDERS: insiders,
    }


def _price_providers(
    client: httpx.Client, settings: Settings, window: DateRange
) -> dict[str, Provider[PriceBar]]:
    providers: dict[str, Provider[PriceBar]] = {
        "yahoo": YahooProvider(window),
        "euronext": EuronextProvider(client, window),
    }
    if settings.twelvedata_api_key is not None:
        api_key = settings.twelvedata_api_key.get_secret_value()
        providers["twelvedata"] = TwelveDataProvider(client, api_key, window)
    return providers
