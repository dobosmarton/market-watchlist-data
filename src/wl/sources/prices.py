"""Daily prices behind one protocol, so a company can switch provider with one YAML line."""

from __future__ import annotations

import time
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from wl.http import get_bytes
from wl.models import PriceBar
from wl.sources.base import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from wl.config import Company
    from wl.models import DateRange


# ---------------------------------------------------------------- Twelve Data (US listings)
class _TdValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    datetime: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None


class _TdMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    currency: str | None = None


class _TdSeries(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    message: str | None = None
    meta: _TdMeta | None = None
    values: list[_TdValue] = []


class TwelveDataProvider:
    """Free plan: 8 requests a minute, 800 a day, US listings only."""

    name = "twelvedata"
    url = "https://api.twelvedata.com/time_series"
    seconds_between_requests = 60 / 8

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        window: DateRange,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._window = window
        self._sleep = sleep

    def fetch(self, company: Company) -> list[PriceBar]:
        try:
            series = self._request(company)
        finally:
            self._sleep(self.seconds_between_requests)  # failed requests count too
        if series.status != "ok":
            raise ProviderError(series.message or "Twelve Data returned an error")
        ccy = (series.meta.currency if series.meta else None) or company.listing.ccy
        return [
            PriceBar(
                slug=company.slug, date=v.datetime, open=v.open, high=v.high, low=v.low,
                close=v.close, volume=v.volume, ccy=ccy, source=self.name,
            )
            for v in series.values
        ]  # fmt: skip

    def _request(self, company: Company) -> _TdSeries:
        params = {
            "symbol": company.listing.ticker,
            "interval": "1day",
            "start_date": self._window.start.isoformat(),
            "end_date": self._window.end.isoformat(),
            "order": "asc",
            "apikey": self._api_key,
        }
        return _TdSeries.model_validate_json(get_bytes(self._client, self.url, params))


# ---------------------------------------------------------------- Yahoo, unofficial
class YahooProvider:
    """Unofficial, personal use only; can break when Yahoo changes. Needs the `yahoo` extra."""

    name = "yahoo"

    def __init__(self, window: DateRange) -> None:
        self._window = window

    def fetch(self, company: Company) -> list[PriceBar]:
        try:
            import yfinance as yf  # noqa: PLC0415  (optional dependency)
        except ImportError as exc:
            msg = "install the optional extra: uv sync --extra yahoo"
            raise ProviderError(msg) from exc

        # yfinance is untyped; treat its pandas frame as Any and validate into PriceBar.
        ticker: Any = yf.Ticker(company.listing.yahoo)
        frame: Any = ticker.history(
            start=self._window.start.isoformat(),
            end=self._window.end.isoformat(),
            auto_adjust=False,
        )
        return [
            PriceBar(
                slug=company.slug,
                date=stamp.date(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
                ccy=company.listing.ccy,
                source=self.name,
            )
            for stamp, row in frame.iterrows()
        ]
