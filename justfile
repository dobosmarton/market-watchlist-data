set dotenv-load := true

default: check

# Install dependencies (including dev tools) from the lockfiles
sync:
    uv sync --locked --all-extras
    pnpm --dir ui install --frozen-lockfile

# Format code
fmt:
    uv run ruff format .
    uv run ruff check --fix .
    pnpm --dir ui fmt

# Everything CI runs, in the same order
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright
    -uv run ty check
    uv run pytest
    pnpm --dir ui check

# Build the chart views (ui/) into src/wl/ui, where the MCP server serves them
ui:
    pnpm --dir ui build

test *args:
    uv run pytest {{args}}

# Fetch fresh data: fx, prices, financials (or one of them)
refresh *what="all":
    uv run wl refresh {{what}}

# Create or update the SQL marts (SQLMesh models in src/wl/transform)
build:
    uv run wl build

# Write the dashboard JSON files
export:
    uv run wl export

# Concepts missing from each company's latest fiscal year
gaps:
    uv run wl gaps

# Serve the data to Claude over MCP (stdio; Claude Code starts it from .mcp.json)
mcp:
    uv run wl mcp

# Resolve missing LEIs from GLEIF
resolve-ids:
    uv run wl resolve-ids
