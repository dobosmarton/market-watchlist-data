"""Shared HTTP client with retries for transient failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from wl.settings import Settings

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return isinstance(exc, httpx.TransportError)


def make_client(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=settings.http_timeout_s,
        follow_redirects=True,
        transport=transport,
    )


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def get_bytes(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> bytes:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.content
