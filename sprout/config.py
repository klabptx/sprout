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

from sprout.exceptions import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Allow extra fields so unknown env vars don't crash startup.
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Tailor stream API
    # ------------------------------------------------------------------ #
    tailor_stream_url: str | None = None
    tailor_base_url: str | None = None
    tailor_org_code: str | None = None
    tailor_stream_id: str | None = None

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
    exclude_event_codes: str = ""
    event_proto_path: str = "SystemLog.proto"
    sample_rate_hz: int = 5
    compare_output_dir: str = "artifacts/compare_output"
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
    vllm_api_key: str = "token-abc123"

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

    def tailor_stream_url_resolved(self) -> str:
        """Return the effective Tailor stream URL.

        Prefers ``TAILOR_STREAM_URL`` if set; otherwise composes from
        ``TAILOR_BASE_URL``, ``TAILOR_ORG_CODE``, and ``TAILOR_STREAM_ID``.

        Raises:
            ConfigurationError: If neither form is fully specified.
        """
        if self.tailor_stream_url:
            return self.tailor_stream_url
        if not all([self.tailor_base_url, self.tailor_org_code, self.tailor_stream_id]):
            raise ConfigurationError(
                "Set TAILOR_STREAM_URL, or set all three of "
                "TAILOR_BASE_URL, TAILOR_ORG_CODE, and TAILOR_STREAM_ID."
            )
        base = (self.tailor_base_url or "").rstrip("/")
        return f"{base}/tailor/{self.tailor_org_code}/streams/{self.tailor_stream_id}"

    def excluded_event_codes(self) -> list[int]:
        """Parse EXCLUDE_EVENT_CODES into a list of ints (supports ranges like 12000-13000)."""
        from sprout.kg.utils import parse_excluded_event_codes

        return parse_excluded_event_codes(self.exclude_event_codes)


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
