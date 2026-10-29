"""Command line: ``wl refresh``, ``wl export``, ``wl companies``, ``wl resolve-ids``."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

import typer

from wl.config import load_watchlist
from wl.export import write_exports
from wl.http import make_client
from wl.log import configure_logging
from wl.pipeline import Job, build_jobs
from wl.settings import Settings
from wl.sources.gleif import lei_for_isin
from wl.store import Store

if TYPE_CHECKING:
    import httpx

    from wl.config import Company

app = typer.Typer(no_args_is_help=True, help="Watchlist data pipeline.")


class What(StrEnum):
    ALL = "all"
    FX = Job.FX.value
    PRICES = Job.PRICES.value
    FINANCIALS = Job.FINANCIALS.value


@app.callback()
def _setup() -> None:
    configure_logging()


@app.command()
def refresh(what: Annotated[What, typer.Argument()] = What.ALL) -> None:
    """Fetch fresh data into the local store; the marts read it from there."""
    settings = Settings()
    watchlist = load_watchlist(settings.watchlist_path)
    with Store.open(settings) as store, make_client(settings) as client:
        store.sync_companies(watchlist.companies)
        jobs = build_jobs(store, client, settings)
        selected = list(jobs.values()) if what is What.ALL else [jobs[Job(what)]]
        reports = [job.run(watchlist) for job in selected]
    for report in reports:
        typer.echo("\n".join(report.lines()))


@app.command()
def export() -> None:
    """Write dashboard JSON files to the export directory."""
    settings = Settings()
    with Store.open(settings) as store:
        written = write_exports(store, settings.export_dir)
    for name, count in written.items():
        typer.echo(f"{settings.export_dir / name}.json: {count} rows")


@app.command()
def companies() -> None:
    """List the watchlist and where each company's data comes from."""
    watchlist = load_watchlist(Settings().watchlist_path)
    for c in watchlist.companies:
        ids = f"isin={c.isin or '-'} lei={c.lei or '-'} cik={c.cik or '-'}"
        typer.echo(
            f"{c.slug:10} {c.listing.ticker:9} prices={c.sources.prices:10} "
            f"financials={c.sources.financials:7} {ids}"
        )


@app.command("resolve-ids")
def resolve_ids() -> None:
    """Look up missing LEIs from GLEIF and print YAML lines to paste into watchlist.yaml."""
    settings = Settings()
    watchlist = load_watchlist(settings.watchlist_path)
    with make_client(settings) as client:
        for company in watchlist.companies:
            if company.needs_lei:
                typer.echo(_lei_line(client, company))


def _lei_line(client: httpx.Client, company: Company) -> str:
    if company.isin is None:
        return f"{company.slug}: no isin in watchlist.yaml to look the LEI up with"
    lei = lei_for_isin(client, company.isin)
    return f"{company.slug}: lei: {lei}" if lei else f"{company.slug}: no LEI for {company.isin}"


@app.command()
def unmapped(slug: str, limit: int = 25) -> None:
    """List the most frequent annual tags for a company that concepts.py does not map yet."""
    with Store.open(Settings()) as store:
        rows = store.unmapped_tags(slug, limit)
    for row in rows:
        typer.echo(f"{row['n']:5}  {row['taxonomy']}:{row['tag']}  [{row['unit']}]")


if __name__ == "__main__":
    app()
