set dotenv-load := true

default: check

# Install dependencies (including dev tools) from the lockfile
sync:
    uv sync --locked --all-extras

# Format code
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Everything CI runs, in the same order
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright
    -uv run ty check
    uv run pytest

test *args:
    uv run pytest {{args}}

# Fetch fresh data: fx, prices, financials (or one of them)
refresh *what="all":
    uv run wl refresh {{what}}

# Write the dashboard JSON files
export:
    uv run wl export

# Concepts missing from each company's latest fiscal year
gaps:
    uv run wl gaps

# Resolve missing LEIs from GLEIF
resolve-ids:
    uv run wl resolve-ids
