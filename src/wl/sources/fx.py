"""ECB reference FX rates via the Frankfurter API (free, no key)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel

from wl.http import get_bytes
from wl.models import FxRate

if TYPE_CHECKING:
    import httpx

BASE_URL = "https://api.frankfurter.dev/v1"
SOURCE = "frankfurter"


class _RangeResponse(BaseModel):
    base: str
    rates: dict[date, dict[str, float]]


def fetch_rates(
    client: httpx.Client, start: date, end: date, quotes: set[str], base: str = "EUR"
) -> list[FxRate]:
    """Daily rates for ``base`` → each quote currency between two dates (ECB business days)."""
    symbols = sorted(quotes - {base})
    if not symbols:
        return []
    url = f"{BASE_URL}/{start.isoformat()}..{end.isoformat()}"
    payload = _RangeResponse.model_validate_json(
        get_bytes(client, url, {"base": base, "symbols": ",".join(symbols)})
    )
    return [
        FxRate(date=day, base=payload.base, quote=quote, rate=rate, source=SOURCE)
        for day, by_quote in sorted(payload.rates.items())
        for quote, rate in sorted(by_quote.items())
    ]
