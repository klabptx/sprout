"""Domain-specific exception hierarchy for the Sprout pipeline."""
from __future__ import annotations


class SproutError(Exception):
    """Base class for all Sprout domain errors."""


class ConfigurationError(SproutError):
    """Missing or invalid configuration (env vars, file paths)."""


class TailorAPIError(SproutError):
    """Tailor stream fetch failed (network, auth, bad response shape)."""

    def __init__(self, message: str, url: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class StitchAPIError(SproutError):
    """Stitch metrics fetch failed after all retries."""

    def __init__(self, message: str, url: str = "", attempts: int = 0) -> None:
        super().__init__(message)
        self.url = url
        self.attempts = attempts


class ProtoParseError(SproutError):
    """SystemLog.proto could not be opened or parsed."""


class LLMBackendError(SproutError):
    """LLM call failed (network, auth, model error, or no backends available)."""

    def __init__(self, message: str, backend: str = "") -> None:
        super().__init__(message)
        self.backend = backend


class DataQualityError(SproutError):
    """Input data is structurally unusable (no summary rows, missing application_id, etc.)."""
