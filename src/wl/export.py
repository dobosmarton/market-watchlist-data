"""Write the dashboard's data files from the marts (same column names the dashboard reads)."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from wl.store import Row, Store

_BILLION = 1e9
_MILLION = 1e6


def write_exports(store: Store, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for name, rows in (("valuation", valuation_rows(store)), ("financials", financial_rows(store))):
        (out_dir / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        written[name] = len(rows)
    return written


def valuation_rows(store: Store) -> list[Row]:
    rows = store.query(
        """
        SELECT slug, name, grp AS "group", quote_ccy AS ccy, price, price_date,
               wk52_low, wk52_high, wk52_pos,
               market_cap_quote / 1e9 AS mcap_bn, pe_ttm, ev_ebitda,
               fcf_yield_pct AS fcf_yield, net_debt_ebitda AS nd_ebitda, debt_reported,
               op_margin_pct, basis, fin_period_end
        FROM marts.valuation ORDER BY slug
        """
    )
    return [{key: _clean(value) for key, value in row.items()} for row in rows]


def financial_rows(store: Store) -> list[Row]:
    """Rows for the dashboard's ``financials`` dataset, scaled per company to bn or m."""
    rows = store.query(
        """
        SELECT slug, fiscal_year, revenue, op_income, op_margin_pct, net_income, fcf, ccy,
               max(abs(revenue)) OVER (PARTITION BY slug) AS largest_revenue
        FROM marts.fin_annual ORDER BY slug, fiscal_year
        """
    )
    return [_financial_row(row) for row in rows]


def _financial_row(row: Row) -> Row:
    units, divisor = _units(row["largest_revenue"])
    return {
        "id": f"{row['slug']}-{row['fiscal_year']}",
        "slug": row["slug"],
        "fy": str(row["fiscal_year"]),
        "estimate": False,
        "revenue": _scaled(row["revenue"], divisor),
        "op_income": _scaled(row["op_income"], divisor),
        "margin_pct": _clean(row["op_margin_pct"], 1),
        "net_income": _scaled(row["net_income"], divisor),
        "fcf": _scaled(row["fcf"], divisor),
        "units": units,
        "ccy": row["ccy"],
    }


def _units(largest_revenue: object) -> tuple[str, float]:
    """Billions once a company's largest revenue reaches one billion, millions below that."""
    if isinstance(largest_revenue, float) and largest_revenue >= _BILLION:
        return "bn", _BILLION
    return "m", _MILLION


def _scaled(value: object, divisor: float) -> object:
    return _clean(value / divisor) if isinstance(value, float) else None


def _clean(value: object, digits: int = 2) -> object:
    """JSON-safe values: NaN and infinity become null, dates ISO strings, floats rounded."""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, digits)
    if isinstance(value, date):
        return value.isoformat()
    return value
