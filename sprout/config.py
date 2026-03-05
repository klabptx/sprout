"""Centralised configuration for the Sprout pipeline.

All environment variables are declared here with types and defaults.
Import ``get_settings()`` anywhere in the codebase instead of calling
``os.getenv()`` directly.

Usage::

    from sprout.config import get_settings

    settings = get_settings()
    print(settings.openai_model)

Tests can override settings by resetting the module-level singleton::

    import sprout.config as cfg
    monkeypatch.setattr(cfg, "_settings", None)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    # next call to get_settings() will pick up the new env
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Allow extra fields so unknown env vars don't crash startup.
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Stitch file-system API
    # ------------------------------------------------------------------ #
    stitch_local_base_url: str = "http://localhost:8888"

    # ------------------------------------------------------------------ #
    # Anomaly detection thresholds
    # ------------------------------------------------------------------ #
    compare_pct_threshold: float = 0.25
    compare_abs_threshold: float = 0.0
    compare_max_events: int = 200

    # ------------------------------------------------------------------ #
    # Pipeline defaults
    # ------------------------------------------------------------------ #
    llm_backend: str = "openai"
    severity_threshold: float = 0.75
    exclude_event_codes: str = "100017"
    exclude_metrics: str = "metrics.liquidtankgallonsremaining"
    event_proto_path: str = "SystemLog.proto"
    sample_rate_hz: int = 5
    compare_output_dir: str = "artifacts/compare_results"
    compare_output_prefix: str = "compare"
    # ------------------------------------------------------------------ #
    # Confidence scoring (previously hardcoded in synthesize node)
    # ------------------------------------------------------------------ #
    confidence_base_good: float = 0.78
    confidence_base_poor: float = 0.62
    confidence_severity_penalty: float = 0.06

    # ------------------------------------------------------------------ #
    # LLM — OpenAI
    # ------------------------------------------------------------------ #
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.2"

    # ------------------------------------------------------------------ #
    # LLM — Local HuggingFace / PEFT
    # ------------------------------------------------------------------ #
    local_model_dir: str | None = None
    local_model_id: str | None = None
    local_adapter_dir: str | None = None
    local_base_model: str | None = None
    local_max_new_tokens: int = 256
    local_temperature: float = 0.2

    # ------------------------------------------------------------------ #
    # LLM — vLLM remote server
    # ------------------------------------------------------------------ #
    vllm_base_url: str | None = None
    vllm_model: str | None = None
    vllm_api_key: str | None = None

    # ------------------------------------------------------------------ #
    # LLM — Lambda.ai
    # ------------------------------------------------------------------ #
    lambda_api_key: str | None = None
    lambda_model: str = "lambda_ai/llama3.1-8b-instruct"

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #

    @field_validator("compare_pct_threshold")
    @classmethod
    def _pct_threshold_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("compare_pct_threshold must be >= 0")
        return v

    @field_validator("compare_max_events")
    @classmethod
    def _max_events_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("compare_max_events must be > 0")
        return v

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #

    def excluded_event_codes(self) -> list[int]:
        """Parse EXCLUDE_EVENT_CODES into a list of ints (supports ranges like 12000-13000)."""
        from sprout.kg.utils import parse_excluded_event_codes

        return parse_excluded_event_codes(self.exclude_event_codes)

    def excluded_metrics(self) -> list[str]:
        """Parse EXCLUDE_METRICS into a list of metric key strings."""
        if not self.exclude_metrics.strip():
            return []
        return [m.strip() for m in self.exclude_metrics.split(",") if m.strip()]


# --------------------------------------------------------------------------- #
# Module-level singleton — lazily initialised so tests can override env vars.
# --------------------------------------------------------------------------- #

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the shared Settings instance, creating it on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset the cached Settings singleton.

    The next call to ``get_settings()`` will create a fresh instance,
    re-reading environment variables.  Useful for Lambda handlers that
    change env vars between invocations.
    """
    global _settings
    _settings = None
