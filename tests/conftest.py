"""Shared pytest fixtures for the Sprout test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    """Reset the Settings singleton before each test so env overrides take effect."""
    import sprout.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)
    yield
    monkeypatch.setattr(cfg, "_settings", None)


@pytest.fixture
def sample_stitch_metrics():
    """Load the recorded Stitch metrics fixture, if available."""
    path = FIXTURES_DIR / "sample_stitch_metrics.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}
