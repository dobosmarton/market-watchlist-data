from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from wl.marts import TRANSFORM_DIR, build_marts
from wl.store import Store

from .conftest import one

if TYPE_CHECKING:
    from wl.settings import Settings


def test_external_models_match_the_store_tables(store: Store) -> None:
    declared = yaml.safe_load((TRANSFORM_DIR / "external_models.yaml").read_text())
    for model in declared:
        schema, table = model["name"].split(".")
        rows = store.query(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        )
        assert {row["column_name"]: row["data_type"] for row in rows} == model["columns"], table


def test_rebuilding_unchanged_models_keeps_the_marts(store: Store, settings: Settings) -> None:
    store.sync_companies([])
    build_marts(settings)
    with Store.open(settings) as reopened:
        assert one(reopened, "SELECT count(*) FROM marts.valuation") == (0,)
