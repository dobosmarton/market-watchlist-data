"""Refresh jobs. Each one fetches, validates and writes one kind of data, company by company.

A failure for one company is logged and reported; it never stops the others.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from wl.log import get_logger
from wl.sources import esef, fx, sec
from wl.sources.prices import PriceProvider, ProviderError, TwelveDataProvider, YahooProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from wl.config import Company, Watchlist
    from wl.models import Fact, FxRate
    from wl.settings import Settings
    from wl.store import Store

log = get_logger(__name__)


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass
class RunReport:
    job: str
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


def sync_companies(store: Store, watchlist: Watchlist) -> None:
    store.sync_companies(
        (c.slug, c.name, c.group, c.listing.ccy, c.reporting_ccy, c.fiscal_year_end_month)
        for c in watchlist.companies
    )


def refresh_fx(store: Store, client: httpx.Client, watchlist: Watchlist, days: int) -> RunReport:
    report = RunReport("fx")
    end = _today()
    rates: list[FxRate] = fx.fetch_rates(
        client, end - timedelta(days=days), end, watchlist.currencies()
    )
    report.rows = store.upsert_fx(rates)
    report.ok.append("EUR")
    return report


def _providers(client: httpx.Client, settings: Settings) -> dict[str, PriceProvider]:
    providers: dict[str, PriceProvider] = {"yahoo": YahooProvider()}
    if settings.twelvedata_api_key is not None:
        providers["twelvedata"] = TwelveDataProvider(
            client, settings.twelvedata_api_key.get_secret_value()
        )
    return providers


def refresh_prices(
    store: Store,
    client: httpx.Client,
    watchlist: Watchlist,
    settings: Settings,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> RunReport:
    report = RunReport("prices")
    providers = _providers(client, settings)
    end = _today()
    start = end - timedelta(days=settings.price_history_days)
    for company in watchlist.companies:
        provider = providers.get(company.sources.prices)
        if provider is None:
            report.skipped[company.slug] = f"no {company.sources.prices} provider configured"
            continue
        try:
            bars = provider.fetch_daily(company, start, end)
        except (ProviderError, httpx.HTTPError) as exc:
            report.failed[company.slug] = str(exc)
            log.warning("prices.failed", slug=company.slug, provider=provider.name, error=str(exc))
            continue
        report.rows += store.upsert_prices(bars)
        report.ok.append(company.slug)
        if provider.name == "twelvedata":
            sleep(7.5)  # free plan: 8 requests per minute
    return report


def _fetch_financials(client: httpx.Client, company: Company) -> list[Fact] | None:
    match company.sources.financials:
        case "sec":
            return sec.fetch_facts(client, company) if company.cik else None
        case "esef":
            return esef.fetch_facts(client, company) if company.lei else None
        case "manual":
            return None


def refresh_financials(store: Store, client: httpx.Client, watchlist: Watchlist) -> RunReport:
    report = RunReport("financials")
    for company in watchlist.companies:
        try:
            facts = _fetch_financials(client, company)
        except httpx.HTTPError as exc:
            report.failed[company.slug] = str(exc)
            log.warning("financials.failed", slug=company.slug, error=str(exc))
            continue
        if facts is None:
            report.skipped[company.slug] = (
                "manual source (milestone 2)"
                if company.sources.financials == "manual"
                else "missing identifier (cik or lei)"
            )
            continue
        report.rows += store.replace_facts(company.slug, facts)
        report.ok.append(company.slug)
    return report
