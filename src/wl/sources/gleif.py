"""LEIs (needed for ESEF filings) from the free GLEIF API: by ISIN, or by searching names."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from wl.http import get_bytes

if TYPE_CHECKING:
    import httpx

URL = "https://api.gleif.org/api/v1/lei-records"
FUZZY_URL = "https://api.gleif.org/api/v1/fuzzycompletions"
MAX_ISINS = 20


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class _Page(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_Record]


def lei_for_isin(client: httpx.Client, isin: str) -> str | None:
    page = _Page.model_validate_json(get_bytes(client, URL, {"filter[isin]": isin}))
    return page.data[0].id if page.data else None


class LeiMatch(BaseModel):
    """A legal entity that matches a lookup. ``isins`` includes bonds, not only the shares."""

    lei: str
    legal_name: str
    country: str
    status: str
    isins: list[str]


class _Ignoring(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Ref(_Ignoring):
    id: str


class _RefData(_Ignoring):
    data: _Ref


class _CompletionLinks(_Ignoring):
    lei_records: _RefData | None = Field(default=None, alias="lei-records")


class _Completion(_Ignoring):
    relationships: _CompletionLinks = _CompletionLinks()


class _Completions(_Ignoring):
    data: list[_Completion]


class _Name(_Ignoring):
    name: str


class _Address(_Ignoring):
    country: str


class _Entity(_Ignoring):
    legal_name: _Name = Field(alias="legalName")
    legal_address: _Address = Field(alias="legalAddress")
    status: str


class _RecordAttributes(_Ignoring):
    entity: _Entity


class _FullRecord(_Ignoring):
    id: str
    attributes: _RecordAttributes


class _RecordPage(_Ignoring):
    data: _FullRecord


class _IsinAttributes(_Ignoring):
    isin: str


class _Isin(_Ignoring):
    attributes: _IsinAttributes


class _IsinPage(_Ignoring):
    data: list[_Isin]


def search_entities(client: httpx.Client, name: str, limit: int = 5) -> list[LeiMatch]:
    """Legal entities whose name is close to ``name``, with country, status and ISINs."""
    raw = get_bytes(client, FUZZY_URL, {"field": "entity.legalName", "q": name})
    leis = [
        c.relationships.lei_records.data.id
        for c in _Completions.model_validate_json(raw).data
        if c.relationships.lei_records is not None
    ]
    return [_entity(client, lei) for lei in list(dict.fromkeys(leis))[:limit]]


def _entity(client: httpx.Client, lei: str) -> LeiMatch:
    record = _RecordPage.model_validate_json(get_bytes(client, f"{URL}/{lei}")).data
    isins = _IsinPage.model_validate_json(
        get_bytes(client, f"{URL}/{lei}/isins", {"page[size]": str(MAX_ISINS)})
    )
    entity = record.attributes.entity
    return LeiMatch(
        lei=record.id,
        legal_name=entity.legal_name.name,
        country=entity.legal_address.country,
        status=entity.status,
        isins=[isin.attributes.isin for isin in isins.data],
    )
