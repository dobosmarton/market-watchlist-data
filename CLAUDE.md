# watchlist-data: notes for Claude

- Python 3.13, uv, src layout (`src/wl`). Run everything through `uv run` or `just`.
- Before finishing any change, run `just check` (ruff format, ruff check, basedpyright strict,
  ty, pytest with coverage ≥ 85%). All must pass; ty is informational.
- Tests never touch the network: mock HTTP with respx and keep payloads in `tests/fixtures/`.
- Sources return typed records from `wl.models`; storage and SQL never see raw API payloads.
- Monetary values stay in the company's reporting currency in `raw_facts` and `fin_annual`;
  currency conversion happens only in `src/wl/transform/models/valuation.sql` using ECB rates.
- Marts are SQLMesh view models in `src/wl/transform/models/`; the raw tables they read are
  declared in `external_models.yaml` (a test checks it against `store.py`).
- MCP tools live in `src/wl/mcp_server.py` as plain functions over a `Store`; the server opens a
  read-only store per call. Only `refresh` and the list tools (`wl.live`) write.
- `watchlist.yaml` is written only by `wl.watchlist_file`, which edits entry blocks as text and
  checks the result against the intended list; tests point `watchlist_path` at a tmp file.
- A company's data source is chosen in `watchlist.yaml`, never by branching on slugs in code.
  `financials/<slug>.yaml` also adds typed years to an `sec`/`esef` company (`WithManualYears`).
- Do not commit `.env`, `data/` or `exports/`.
