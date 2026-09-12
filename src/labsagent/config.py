"""Settings. Deliberately minimal -- only what Phase 1 needs.

Expanded in Phase 3 when the run store and retry policy arrive. Building the
full labsagent.toml schema now would be speculative.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    model_name: str = "deepseek-flash"
    temperature: float = 0.0
    timeout_s: int = 30
    max_retries_per_task: int = 3

    @property
    def configured(self) -> bool:
        return bool(self.deepseek_api_key)


def load_settings() -> Settings:
    return Settings()
