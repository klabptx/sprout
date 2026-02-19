"""Unit tests for sprout.nodes.synthesize.compute_confidence."""
from __future__ import annotations

import pytest

from sprout.nodes.synthesize import compute_confidence


def test_confidence_good_quality_high_severity():
    # data_quality_flag=False (good data), severity above threshold → no penalty
    c = compute_confidence(data_quality_flag=False, top_severity=0.9, severity_threshold=0.75)
    from sprout.config import get_settings
    s = get_settings()
    assert c == round(s.confidence_base_good, 2)


def test_confidence_good_quality_low_severity():
    # data_quality_flag=False, severity below threshold → penalty applied
    c = compute_confidence(data_quality_flag=False, top_severity=0.1, severity_threshold=0.75)
    from sprout.config import get_settings
    s = get_settings()
    expected = round(max(0.5, s.confidence_base_good - s.confidence_severity_penalty), 2)
    assert c == expected


def test_confidence_poor_quality_high_severity():
    # data_quality_flag=True (poor data), severity above threshold
    c = compute_confidence(data_quality_flag=True, top_severity=0.9, severity_threshold=0.75)
    from sprout.config import get_settings
    s = get_settings()
    assert c == round(s.confidence_base_poor, 2)


def test_confidence_poor_quality_low_severity():
    # Both quality and severity penalties
    c = compute_confidence(data_quality_flag=True, top_severity=0.1, severity_threshold=0.75)
    from sprout.config import get_settings
    s = get_settings()
    expected = round(max(0.5, s.confidence_base_poor - s.confidence_severity_penalty), 2)
    assert c == expected


def test_confidence_minimum_floor():
    # Even with worst inputs the floor is 0.5
    c = compute_confidence(data_quality_flag=True, top_severity=0.0, severity_threshold=1.0)
    assert c >= 0.5


def test_confidence_uses_settings_values(monkeypatch):
    import sprout.config as cfg
    monkeypatch.setenv("CONFIDENCE_BASE_GOOD", "0.90")
    monkeypatch.setenv("CONFIDENCE_BASE_POOR", "0.50")
    monkeypatch.setenv("CONFIDENCE_SEVERITY_PENALTY", "0.10")
    monkeypatch.setattr(cfg, "_settings", None)
    c = compute_confidence(data_quality_flag=False, top_severity=0.9, severity_threshold=0.75)
    assert c == 0.90
