"""Runtime settings, read from environment variables (prefix ``WL_``) and ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WL_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    export_dir: Path = Path("exports")
    watchlist_path: Path = Path("watchlist.yaml")
    storage: Literal["duckdb", "ducklake"] = "duckdb"

    sec_user_agent: str = Field(
        default="watchlist-data contact@example.com",
        description="SEC fair-access policy: identify the client with a name and email.",
    )
    twelvedata_api_key: SecretStr | None = None

    http_timeout_s: float = 30.0
    price_history_days: int = 400

    @property
    def db_path(self) -> Path:
        return self.data_dir / "watchlist.duckdb"
