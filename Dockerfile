# Same image runs on the Mac (Docker Desktop / OrbStack) and on a VPS.

# The chart views: ui/ builds into ../src/wl/ui
FROM node:24-bookworm-slim AS ui
RUN corepack enable
WORKDIR /app/ui
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY ui ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --all-extras --no-install-project

COPY README.md ./
COPY src ./src
COPY --from=ui /app/src/wl/ui ./src/wl/ui
RUN uv sync --locked --no-dev --all-extras

# The watchlist and hand-entered figures are not baked in: mount them at /app/watchlist.yaml
# and /app/financials (compose.yaml does).

ENV WL_DATA_DIR=/data WL_EXPORT_DIR=/exports
VOLUME ["/data", "/exports"]
ENTRYPOINT ["uv", "run", "--no-dev", "wl"]
CMD ["--help"]
