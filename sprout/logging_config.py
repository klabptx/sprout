"""Logging configuration for the Sprout pipeline."""
from __future__ import annotations

import logging
import logging.config


def configure_logging(verbose: bool = False) -> None:
    """Configure root logger for CLI use.

    Args:
        verbose: If True, set log level to DEBUG; otherwise INFO.
                 All log output goes to stderr so stdout remains clean for
                 machine-readable pipeline output (JSON, reports).
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "cli": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    "datefmt": "%H:%M:%S",
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "cli",
                }
            },
            "root": {"level": level, "handlers": ["stderr"]},
            # Suppress overly chatty third-party loggers at INFO level.
            "loggers": {
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "openai": {"level": "WARNING"},
            },
        }
    )
