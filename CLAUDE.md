# watchlist-data: notes for Claude

- Python 3.13, uv, src layout (`src/wl`). Run everything through `uv run` or `just`.
- Before finishing any change, run `just check` (ruff format, ruff check, basedpyright strict,
  ty, pytest with coverage ≥ 85%). All must pass; ty is informational.
- Tests never touch the network: mock HTTP with respx and keep payloads in `tests/fixtures/`.
- Sources return typed records from `wl.models`; storage and SQL never see raw API payloads.
- Monetary values stay in the company's reporting currency in `raw_facts` and `fin_annual`;
  currency conversion happens only in `sql/valuation.sql` using ECB rates.
- A company's data source is chosen in `watchlist.yaml`, never by branching on slugs in code.
- Do not commit `.env`, `data/` or `exports/`.
