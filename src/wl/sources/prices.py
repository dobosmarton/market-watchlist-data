"""Daily prices behind one protocol, so a company can switch provider with one YAML line."""

from __future__ import annotations

import csv
import time
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from wl.http import get_bytes
from wl.models import PriceBar
from wl.sources.base import MissingIdentifierError, ProviderError

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


# ---------------------------------------------------------------- Euronext markets
class EuronextProvider:
    """The CSV download behind live.euronext.com's price history; no key, Euronext MICs only."""

    name = "euronext"
    url = "https://live.euronext.com/en/ajax/AwlHistoricalPrice/getFullDownloadAjax/{isin}-{mic}"

    def __init__(self, client: httpx.Client, window: DateRange) -> None:
        self._client = client
        self._window = window

    def fetch(self, company: Company) -> list[PriceBar]:
        if company.isin is None:
            msg = "no isin in watchlist.yaml"
            raise MissingIdentifierError(msg)
        params = {
            "format": "csv",
            "decimal_separator": ".",
            "date_form": "d/m/Y",
            "op": "",
            "adjusted": "N",
            "base100": "",
            "startdate": self._window.start.isoformat(),
            "enddate": self._window.end.isoformat(),
        }
        url = self.url.format(isin=company.isin, mic=company.listing.mic)
        bars = parse_euronext_csv(company, get_bytes(self._client, url, params))
        if not bars:
            msg = f"Euronext has no prices for {company.isin} on {company.listing.mic}"
            raise ProviderError(msg)
        return bars


def parse_euronext_csv(company: Company, raw: bytes) -> list[PriceBar]:
    """Parse the download: title lines, then ``Date;Open;High;Low;Last;Close;...`` rows.

    Exchange holidays come as rows with zero open, high and low and the last close carried
    forward; they are not trading days, so they are dropped.
    """
    lines = raw.decode("utf-8-sig").splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("Date;")), None)
    if header is None:
        msg = "Euronext CSV has no Date header; the download format changed"
        raise ProviderError(msg)
    rows = csv.DictReader(lines[header:], delimiter=";")
    return [
        PriceBar(
            slug=company.slug,
            date=datetime.strptime(row["Date"], "%d/%m/%Y").date(),  # noqa: DTZ007  (a calendar day)
            open=_price(row["Open"]),
            high=_price(row["High"]),
            low=_price(row["Low"]),
            close=close,
            volume=_number(row["Number of Shares"]),
            ccy=company.listing.ccy,
            source=EuronextProvider.name,
        )
        for row in rows
        if (close := _price(row["Close"])) is not None
        and any(_price(row[column]) for column in ("Open", "High", "Low"))
    ]


def _number(text: str | None) -> float | None:
    """Return the value, or None for the dashes and blanks Euronext uses for no trade."""
    try:
        return float(text or "")
    except ValueError:
        return None


def _price(text: str | None) -> float | None:
    """Return a price, or None where Euronext writes 0 for no trade."""
    value = _number(text)
    return value or None


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
