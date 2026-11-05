"""SEC filings and insider trades (Form 4) from EDGAR's free submissions API (send a User-Agent).

One submissions request per company lists its recent filings. Filings are kept as they are
listed; each new Form 4 costs one more request, so only the ones not stored yet are fetched.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field

from wl.http import get_bytes
from wl.log import get_logger
from wl.models import Filing, InsiderTrade
from wl.sources.base import MissingIdentifierError, ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from wl.config import Company

log = get_logger(__name__)

SOURCE = "sec"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"
MAX_FORM4 = 200  # new Form 4s fetched per company and run, newest first
PAUSE_S = 0.1  # between Form 4 requests: the SEC allows at most 10 a second

_REPORTS = ("10-K", "10-Q", "20-F", "40-F")
_KEPT = (*_REPORTS, "8-K", "6-K", "SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G")
# Forms kept as filings, amendments included. Form 4s become insider trades instead.
FORMS = frozenset((*_KEPT, *(f"{form}/A" for form in _KEPT)))
INSIDER_FORMS = frozenset({"4", "4/A"})

# 8-K items by number; 9.01 (exhibits) says nothing on its own
ITEMS = {
    "1.01": "material agreement",
    "1.02": "agreement terminated",
    "1.03": "bankruptcy",
    "1.05": "cybersecurity incident",
    "2.01": "acquisition or disposal completed",
    "2.02": "results",
    "2.03": "new debt",
    "2.04": "debt accelerated",
    "2.05": "exit or restructuring costs",
    "2.06": "impairment",
    "3.01": "listing standards notice",
    "3.02": "unregistered share sale",
    "3.03": "shareholder rights changed",
    "4.01": "auditor change",
    "4.02": "earlier statements no longer reliable",
    "5.01": "change in control",
    "5.02": "officer or director change",
    "5.03": "articles or bylaws amended",
    "5.07": "shareholder vote",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
}


class _Recent(BaseModel):
    accession: list[str] = Field(alias="accessionNumber")
    filed: list[date] = Field(alias="filingDate")
    report_date: list[str] = Field(alias="reportDate")
    form: list[str]
    items: list[str]
    document: list[str] = Field(alias="primaryDocument")


class _Filings(BaseModel):
    recent: _Recent


class _Submissions(BaseModel):
    filings: _Filings


@dataclass(frozen=True)
class Entry:
    """One filing in a company's submissions list."""

    cik: int
    accession: str
    filed: date
    form: str
    items: str
    period: date | None
    document: str  # for XML forms, the path of the rendered page: "xslF345X05/form4.xml"

    def url(self, document: str | None = None) -> str:
        folder = self.accession.replace("-", "")
        return ARCHIVES_URL.format(cik=self.cik, folder=folder, document=document or self.document)

    @property
    def xml_url(self) -> str:
        """The filing's own XML, without the stylesheet folder that renders it as a page."""
        return self.url(self.document.rsplit("/", 1)[-1])


def parse_submissions(cik: int, raw: bytes, since: date) -> list[Entry]:
    """Filings from ``since`` on, newest first (the order the SEC lists them in)."""
    recent = _Submissions.model_validate_json(raw).filings.recent
    rows = zip(
        recent.accession, recent.filed, recent.form, recent.items, recent.report_date,
        recent.document, strict=True,
    )  # fmt: skip
    return [
        Entry(cik, accession, filed, form, items, _date(period), document)
        for accession, filed, form, items, period, document in rows
        if filed >= since
    ]


def filing_title(form: str, items: str, period: date | None) -> str:
    """Say what a filing is in a line: the 8-K's items, or the period a report covers."""
    base = form.removesuffix("/A")
    if base == "8-K":
        named = [ITEMS[item.strip()] for item in items.split(",") if item.strip() in ITEMS]
        return f"{form}: {'; '.join(named) or 'exhibits only'}"
    if base in _REPORTS:
        return f"{form} for the period ended {period}" if period else form
    if "13D" in base:
        return f"{form}: a holder of 5% or more that may seek to influence the company"
    if "13G" in base:
        return f"{form}: a passive holder of 5% or more"
    return form


def filings_of(slug: str, entries: list[Entry]) -> list[Filing]:
    return [
        Filing(
            slug=slug, source=SOURCE, id=e.accession, date=e.filed, form=e.form,
            items=e.items or None, period=e.period, title=filing_title(e.form, e.items, e.period),
            url=e.url(),
        )
        for e in entries
        if e.form in FORMS
    ]  # fmt: skip


def parse_form4(slug: str, entry: Entry, raw: bytes) -> list[InsiderTrade]:
    """Every transaction in a Form 4, shares first and then derivatives (options, units)."""
    root = ET.fromstring(raw)  # noqa: S314  (the SEC's own XML; expat caps entity expansion)
    owner = root.find("reportingOwner")
    if owner is None:
        msg = f"Form 4 {entry.accession} names no reporting owner"
        raise ProviderError(msg)
    person = (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip()
    role = _role(owner.find("reportingOwnerRelationship"))
    footnotes = " ".join(note.text or "" for note in root.iter("footnote"))
    planned = _flag(root.findtext("aff10b5One")) or "10b5-1" in footnotes
    rows = [
        *root.iterfind("nonDerivativeTable/nonDerivativeTransaction"),
        *root.iterfind("derivativeTable/derivativeTransaction"),
    ]
    trades: list[InsiderTrade] = []
    for line, row in enumerate(rows, start=1):
        trade_date, shares = _date(_value(row, "transactionDate")), _number(row, "shares")
        if trade_date is None or shares is None:
            continue
        trades.append(
            InsiderTrade(
                slug=slug, source=SOURCE, id=entry.accession, line=line, filed=entry.filed,
                trade_date=trade_date, person=person, role=role,
                code=(row.findtext("transactionCoding/transactionCode") or "").strip(),
                acquired=_value(row, "transactionAmounts/transactionAcquiredDisposedCode") == "A",
                shares=shares, price=_number(row, "price"), ccy="USD",
                owned_after=_number(row, "owned_after"), planned=planned,
                derivative=row.tag == "derivativeTransaction",
                amendment=entry.form.endswith("/A"),
                security=_value(row, "securityTitle") or "", url=entry.url(),
            )
        )  # fmt: skip
    return trades


_NUMBERS = {
    "shares": "transactionAmounts/transactionShares",
    "price": "transactionAmounts/transactionPricePerShare",
    "owned_after": "postTransactionAmounts/sharesOwnedFollowingTransaction",
}


def _value(element: ET.Element, path: str) -> str | None:
    text = (element.findtext(f"{path}/value") or "").strip()
    return text or None


def _number(element: ET.Element, name: str) -> float | None:
    text = _value(element, _NUMBERS[name])
    return float(text) if text else None


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text else None  # Form 4 dates may carry a zone


def _flag(text: str | None) -> bool:
    return (text or "").strip().lower() in {"1", "true"}


def _role(relationship: ET.Element | None) -> str:
    """Officer title, Director, 10% owner: what makes the person an insider."""
    if relationship is None:
        return ""
    parts = [
        (relationship.findtext("officerTitle") or "Officer").strip()
        if _flag(relationship.findtext("isOfficer"))
        else "",
        "Director" if _flag(relationship.findtext("isDirector")) else "",
        "10% owner" if _flag(relationship.findtext("isTenPercentOwner")) else "",
        (relationship.findtext("otherText") or "Other").strip()
        if _flag(relationship.findtext("isOther"))
        else "",
    ]
    return ", ".join(part for part in parts if part)


def _cik(company: Company) -> int:
    if company.cik is None:
        msg = "no cik in watchlist.yaml"
        raise MissingIdentifierError(msg)
    return company.cik


def recent_filings(client: httpx.Client, cik: int, since: date) -> list[Entry]:
    return parse_submissions(cik, get_bytes(client, SUBMISSIONS_URL.format(cik=cik)), since)


class EdgarFilingsProvider:
    """A company's filings since a date: reports, 8-Ks, 6-Ks and 5%-holder schedules."""

    name = SOURCE

    def __init__(self, client: httpx.Client, since: date) -> None:
        self._client, self._since = client, since

    def fetch(self, company: Company) -> list[Filing]:
        return filings_of(company.slug, recent_filings(self._client, _cik(company), self._since))


class EdgarInsidersProvider:
    """Insider trades from the Form 4s filed since a date that are not stored yet.

    ``known`` gives the filing ids already stored for a company. A Form 4 that cannot be
    fetched or read is logged and skipped; the next run tries it again.
    """

    name = SOURCE

    def __init__(
        self,
        client: httpx.Client,
        since: date,
        known: Callable[[str], set[str]],
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client, self._since, self._known, self._pause = client, since, known, pause

    def fetch(self, company: Company) -> list[InsiderTrade]:
        known = self._known(company.slug)
        entries = recent_filings(self._client, _cik(company), self._since)
        new = [e for e in entries if e.form in INSIDER_FORMS and e.accession not in known]
        trades: list[InsiderTrade] = []
        for entry in new[:MAX_FORM4]:
            self._pause(PAUSE_S)
            trades += self._read(company.slug, entry)
        return trades

    def _read(self, slug: str, entry: Entry) -> list[InsiderTrade]:
        try:
            return parse_form4(slug, entry, get_bytes(self._client, entry.xml_url))
        except (httpx.HTTPStatusError, ET.ParseError, ProviderError, ValueError) as exc:
            log.warning("form4.skipped", slug=slug, accession=entry.accession, error=str(exc))
            return []
