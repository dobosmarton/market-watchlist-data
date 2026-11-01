"""Change the watchlist and publish the result at once.

Adding or updating a company edits ``watchlist.yaml``, fetches that company's prices and
financials, rebuilds the marts and rewrites the dashboard exports. If its prices cannot be
fetched the change is undone, so the list never gains an entry that shows nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wl import watchlist_file
from wl.config import load_watchlist
from wl.export import write_exports
from wl.marts import build_marts
from wl.pipeline import Job, RunReport, run_refresh
from wl.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from wl.config import Company
    from wl.settings import Settings


class ChangeRefusedError(ValueError):
    """The change was undone; the message says why and what to check."""


@dataclass(frozen=True)
class Change:
    report: list[str]  # the refresh report lines for the company
    hints: list[str]  # what is still missing, and how to fill it in


def add_company(settings: Settings, company: Company, *, force: bool = False) -> Change:
    """Put a new company on the watchlist and fetch its data; ``force`` keeps it without prices."""
    return _apply(settings, company, watchlist_file.add, force=force, undo=_forget)


def update_company(settings: Settings, company: Company, *, force: bool = False) -> Change:
    """Replace a company's entry (same slug) and fetch its data again from the new entry."""
    return _apply(settings, company, watchlist_file.replace, force=force, undo=_refetch)


def remove_company(settings: Settings, slug: str) -> None:
    """Take a company off the watchlist and drop its data (a later add fetches it again)."""
    watchlist_file.remove(_existing(settings.watchlist_path), slug)
    _forget(settings, slug)
    _export(settings)


def _apply(
    settings: Settings,
    company: Company,
    edit: Callable[[Path, Company], str],
    *,
    force: bool,
    undo: Callable[[Settings, str], None],
) -> Change:
    path = _existing(settings.watchlist_path)
    previous = edit(path, company)
    reports = run_refresh(settings, load_watchlist(path), slugs=[company.slug])
    problem = _price_problem(reports, company.slug)
    if problem and not force:
        watchlist_file.restore(path, previous)
        undo(settings, company.slug)
        msg = (
            f"no prices for {company.slug} ({problem}), so the watchlist was left unchanged. "
            "Check the listing (ticker, yahoo symbol, mic) and isin, or pass force=true to keep "
            "the entry anyway."
        )
        raise ChangeRefusedError(msg)
    _export(settings)
    report = [line for r in reports if r.job is not Job.FX for line in r.lines()]
    return Change(report=report, hints=_hints(settings, company, reports))


def _price_problem(reports: list[RunReport], slug: str) -> str | None:
    for report in reports:
        if report.job is Job.PRICES:
            return report.failed.get(slug) or report.skipped.get(slug)
    return None


def _hints(settings: Settings, company: Company, reports: list[RunReport]) -> list[str]:
    financials = next(r for r in reports if r.job is Job.FINANCIALS)
    if company.slug in financials.ok:
        return []
    if company.sources.financials == "manual":
        path = settings.manual_dir / f"{company.slug}.yaml"
        return [f"type its annual figures into {path} (see financials.example/), then refresh"]
    if company.needs_lei:
        return ["add its LEI (lookup_company finds it) so its ESEF filings can be found"]
    typed = settings.manual_dir / f"{company.slug}.yaml"
    hint = (
        "its financials did not load; see the report for the reason. A recent spin-off has no "
        f"annual report yet: type its last years into {typed}"
    )
    return [hint]


def _forget(settings: Settings, slug: str) -> None:
    """Drop a company's data and bring the companies table back in line with the file."""
    with Store.open(settings) as store:
        store.delete_company(slug)
        store.sync_companies(load_watchlist(settings.watchlist_path).companies)
    build_marts(settings)


def _refetch(settings: Settings, slug: str) -> None:
    """Fetch a company's data again from its (restored) entry."""
    run_refresh(settings, load_watchlist(settings.watchlist_path), slugs=[slug])


def _export(settings: Settings) -> None:
    with Store.open(settings, read_only=True) as store:
        write_exports(store, settings.export_dir)


def _existing(path: Path) -> Path:
    if not path.exists():
        msg = f"{path} not found; start from watchlist.example.yaml"
        raise ChangeRefusedError(msg)
    return path
