"""Central configuration for the ITR bot backend.

All tunables (model IDs per task, paths, server) live here so they are
swappable from a single place or via environment variables / .env.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings, overridable through environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""

    # Model routing (LiteLLM "openai/<model>" strings). Correctness-critical
    # tasks use the strongest reasoning model; lighter prose tasks use the mini.
    # All overridable via environment variables / .env.
    extraction_model: str = "openai/gpt-5"
    validation_model: str = "openai/gpt-5"
    sanity_check_model: str = "openai/gpt-5"
    orchestration_model: str = "openai/gpt-5-mini"
    chat_model: str = "openai/gpt-5-mini"

    # Reasoning effort hint forwarded to reasoning-capable models.
    extraction_reasoning_effort: str = "high"

    # Doc-intelligence self-critique loop bound.
    max_extraction_retries: int = 2

    # Storage locations (local deployment).
    data_dir: Path = REPO_ROOT / "backend" / "_data"
    uploads_dir: Path = REPO_ROOT / "backend" / "_data" / "uploads"
    logs_dir: Path = REPO_ROOT / "backend" / "_data" / "logs"
    db_path: Path = REPO_ROOT / "backend" / "_data" / "itr.db"

    # Server / CORS.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Assessment year context.
    assessment_year: str = "2026-27"
    financial_year: str = "2025-26"

    def ensure_dirs(self) -> None:
        """Create all local storage directories if missing."""
        for path in (self.data_dir, self.uploads_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
