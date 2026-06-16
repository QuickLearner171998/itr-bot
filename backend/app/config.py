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
    extraction_model: str = "openai/gpt-5-mini"
    orchestration_model: str = "openai/gpt-5-mini"
    chat_model: str = "openai/gpt-5-mini"

    # Reasoning effort hint forwarded to reasoning-capable models.
    extraction_reasoning_effort: str = "high"

    # Fixed sampling seed for reproducible extraction/computation across runs.
    # OpenAI honours this best-effort so identical documents yield identical
    # extractions (avoids tax swinging between runs). Override via env if needed.
    llm_seed: int = 42

    # Doc-intelligence self-critique loop bound.
    max_extraction_retries: int = 1

    # Storage locations. On Render free tier there is no persistent disk, so we
    # default to /tmp which survives the process lifetime (one session = one
    # filing). Override DATA_DIR in env for a machine with persistent storage.
    data_dir: Path = Path("/tmp/itr_bot")
    uploads_dir: Path = Path("/tmp/itr_bot/uploads")
    logs_dir: Path = Path("/tmp/itr_bot/logs")
    db_path: Path = Path("/tmp/itr_bot/itr.db")

    # Logging.
    log_level: str = "INFO"
    log_pretty: bool = False  # JSON only in production (easier to grep in Render logs)

    # Extraction cache TTL in seconds (default 7 days). Set to 0 to disable.
    extraction_cache_ttl: int = 7 * 24 * 3600

    # Manual override folded into the cache fingerprint. The cache key is
    # auto-invalidated whenever extraction source files or model params change
    # (see store._extraction_fingerprint), so this rarely needs touching — bump
    # it only to force a global cache flush without a code change.
    extraction_version: str = "2026-06-16"

    # Assessment year context.
    assessment_year: str = "2026-27"
    financial_year: str = "2025-26"

    def ensure_dirs(self) -> None:
        """Create all local storage directories if missing."""
        for path in (self.data_dir, self.uploads_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
