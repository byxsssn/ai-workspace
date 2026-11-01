"""Operator-owned local MCP settings; never accept commands from a chat request."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AASettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_WORKSPACE_AA_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    enabled: bool = False
    python_executable: Path | None = None
    snapshot_path: Path | None = None
    timeout_seconds: float = Field(default=15, ge=1, le=30, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_connection(self) -> Self:
        if self.enabled and (
            self.python_executable is None or self.snapshot_path is None
        ):
            raise ValueError("AA MCP requires Python executable and snapshot paths")
        for path in (self.python_executable, self.snapshot_path):
            if path is not None and (not path.is_absolute() or "\x00" in str(path)):
                raise ValueError("AA MCP paths must be absolute local paths")
        return self


@lru_cache
def get_aa_settings() -> AASettings:
    return AASettings()
