"""Resolve LEIs (needed for ESEF filings) from ISINs with the free GLEIF API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from wl.http import get_bytes

if TYPE_CHECKING:
    import httpx

URL = "https://api.gleif.org/api/v1/lei-records"


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class _Page(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_Record]


def lei_for_isin(client: httpx.Client, isin: str) -> str | None:
    page = _Page.model_validate_json(get_bytes(client, URL, {"filter[isin]": isin}))
    return page.data[0].id if page.data else None
