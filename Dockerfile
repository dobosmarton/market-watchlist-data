# Same image runs on the Mac (Docker Desktop / OrbStack) and on a VPS.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --all-extras --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --all-extras

# The watchlist is not baked in: mount yours at /app/watchlist.yaml (compose.yaml does).

ENV WL_DATA_DIR=/data WL_EXPORT_DIR=/exports
VOLUME ["/data", "/exports"]
ENTRYPOINT ["uv", "run", "--no-dev", "wl"]
CMD ["--help"]
