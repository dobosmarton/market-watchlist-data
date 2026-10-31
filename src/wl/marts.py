"""Build the SQL marts (``marts.*`` views) with SQLMesh from the models in ``wl/transform``."""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlmesh.core.config import Config

    from wl.settings import Settings

TRANSFORM_DIR = resources.files("wl") / "transform"
_GATEWAY = "local"


def build_marts(settings: Settings) -> None:
    """Create or update the mart views; a no-op when no model changed since the last build.

    SQLMesh opens its own connection, so no other process may hold the database file open.
    """
    from sqlmesh import Context  # noqa: PLC0415  (slow import, only needed here)

    context = Context(paths=str(TRANSFORM_DIR), config=sqlmesh_config(settings))
    try:
        context.plan(auto_apply=True, no_prompts=True)
    finally:
        context.close()


def sqlmesh_config(settings: Settings) -> Config:
    """Point SQLMesh at the same storage ``Store.open`` uses: a DuckDB file or DuckLake."""
    from sqlmesh.core.config import (  # noqa: PLC0415
        Config,
        DuckDBConnectionConfig,
        GatewayConfig,
        ModelDefaultsConfig,
    )
    from sqlmesh.core.config.connection import DuckDBAttachOptions  # noqa: PLC0415

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.storage == "duckdb":
        connection = DuckDBConnectionConfig(database=str(settings.db_path))
    else:
        lake = DuckDBAttachOptions(
            type="ducklake",
            path=f"sqlite:{settings.data_dir / 'catalog.sqlite'}",
            data_path=f"{settings.data_dir / 'lake'}/",
        )
        connection = DuckDBConnectionConfig(catalogs={"lake": lake}, extensions=["ducklake"])
    return Config(
        cache_dir=str(settings.data_dir.resolve() / "sqlmesh-cache"),  # outside the package
        model_defaults=ModelDefaultsConfig(dialect="duckdb"),
        gateways={_GATEWAY: GatewayConfig(connection=connection)},
        default_gateway=_GATEWAY,
    )
