"""Command line: ``wl refresh``, ``wl export``, ``wl companies``, ``wl resolve-ids``."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from wl.config import load_watchlist
from wl.export import write_exports
from wl.http import make_client
from wl.log import configure_logging
from wl.pipeline import (
    RunReport,
    refresh_financials,
    refresh_fx,
    refresh_prices,
    sync_companies,
)
from wl.settings import Settings
from wl.sources.gleif import lei_for_isin
from wl.store import Store

app = typer.Typer(no_args_is_help=True, help="Watchlist data pipeline.")


class What(StrEnum):
    ALL = "all"
    FX = "fx"
    PRICES = "prices"
    FINANCIALS = "financials"


@app.callback()
def _setup() -> None:
    configure_logging()


@app.command()
def refresh(what: Annotated[What, typer.Argument()] = What.ALL) -> None:
    """Fetch fresh data into the local store, then rebuild the marts."""
    settings = Settings()
    watchlist = load_watchlist(settings.watchlist_path)
    with Store.open(settings) as store, make_client(settings) as client:
        sync_companies(store, watchlist)
        reports: list[RunReport] = []
        if what in {What.ALL, What.FX}:
            reports.append(refresh_fx(store, client, watchlist, settings.price_history_days))
        if what in {What.ALL, What.PRICES}:
            reports.append(refresh_prices(store, client, watchlist, settings))
        if what in {What.ALL, What.FINANCIALS}:
            reports.append(refresh_financials(store, client, watchlist))
        store.build_marts()
    for report in reports:
        typer.echo(report.summary())
        for slug, reason in sorted(report.failed.items()):
            typer.echo(f"  failed  {slug}: {reason}")
        for slug, reason in sorted(report.skipped.items()):
            typer.echo(f"  skipped {slug}: {reason}")


@app.command()
def export() -> None:
    """Write dashboard JSON files to the export directory."""
    settings = Settings()
    with Store.open(settings) as store:
        store.build_marts()
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
        for c in watchlist.companies:
            if c.lei or not c.isin or c.sources.financials != "esef":
                continue
            lei = lei_for_isin(client, c.isin)
            typer.echo(f"{c.slug}: lei: {lei}" if lei else f"{c.slug}: no LEI found for {c.isin}")


@app.command()
def unmapped(slug: str, limit: int = 25) -> None:
    """List the most frequent annual tags for a company that concepts.py does not map yet."""
    settings = Settings()
    with Store.open(settings) as store:
        store.build_marts()
        rows = store.con.execute(
            """
            SELECT f.taxonomy, f.tag, any_value(f.unit) AS unit, count(*) AS n
            FROM raw_facts AS f
            ANTI JOIN concept_map AS m ON m.taxonomy = f.taxonomy AND m.tag = f.tag
            WHERE f.slug = ? AND f.taxonomy IN ('us-gaap', 'ifrs-full')
            GROUP BY ALL ORDER BY n DESC LIMIT ?
            """,
            [slug, limit],
        ).fetchall()
    for taxonomy, tag, unit, n in rows:
        typer.echo(f"{n:5}  {taxonomy}:{tag}  [{unit}]")


if __name__ == "__main__":
    app()
