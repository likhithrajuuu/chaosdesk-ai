"""Validated service configuration. Secrets are never part of diagnostics."""

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from chaosdesk.retrieval import DEFAULT_KB_DIR


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    environment: Literal["development", "production"] = "development"
    mode: Literal["mock", "ai"] = "mock"
    api_token: SecretStr
    openai_api_key: Optional[SecretStr] = None
    model: str = "gpt-4.1-mini"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    database: Path = Path("var/chaosdesk.db")
    kb_dir: Path = DEFAULT_KB_DIR
    workers: int = Field(default=4, ge=1, le=32)
    max_pending: int = Field(default=1000, ge=1, le=100000)
    max_stored_jobs: int = Field(default=100000, ge=1, le=1000000)
    submissions_per_minute: int = Field(default=120, ge=1)
    model_requests_per_minute: int = Field(default=60, ge=1)
    model_requests_per_day: int = Field(default=10000, ge=1)
    model_timeout: float = Field(default=30, ge=1, le=60)
    model_output_tokens: int = Field(default=1500, ge=256, le=4096)
    lease_seconds: float = Field(default=180, ge=30, le=600)
    max_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_seconds: float = Field(default=2, ge=0.1, le=60)
    poll_seconds: float = Field(default=0.25, ge=0.01, le=5)
    shutdown_seconds: float = Field(default=45, ge=1, le=90)
    max_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    retention_days: int = Field(default=7, ge=1, le=365)

    @model_validator(mode="after")
    def check_runtime(self):
        if len(self.api_token.get_secret_value().encode()) < 32:
            raise ValueError("CHAOSDESK_API_TOKEN must contain at least 32 bytes")
        if self.mode == "ai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for AI mode")
        if self.environment == "production" and self.mode != "ai":
            raise ValueError("Production requires AI mode; mock mode is a demo baseline")
        if self.environment == "production" and any(
            os.getenv(key, "").lower() in ("true", "1")
            for key in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")
        ):
            raise ValueError("External prompt tracing must be disabled in production")
        if self.lease_seconds < self.model_timeout * 3 + 10:
            raise ValueError("Lease must exceed three request timeouts plus 10 seconds")
        if self.max_stored_jobs < self.max_pending:
            raise ValueError("Stored job capacity must cover pending capacity")
        return self

    @classmethod
    def from_env(cls):
        values = {name: os.environ["CHAOSDESK_" + name.upper()]
                  for name in cls.model_fields if "CHAOSDESK_" + name.upper() in os.environ}
        if "OPENAI_API_KEY" in os.environ:
            values["openai_api_key"] = os.environ["OPENAI_API_KEY"]
        if "OPENAI_MODEL" in os.environ and "model" not in values:
            values["model"] = os.environ["OPENAI_MODEL"]
        return cls.model_validate(values)
