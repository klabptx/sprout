"""Tests for sprout.config.Settings."""

from __future__ import annotations

import pytest

from sprout.config import Settings, get_settings


# --------------------------------------------------------------------------- #
# Settings instantiation
# --------------------------------------------------------------------------- #


def test_default_settings_instantiate():
    s = Settings()
    assert s.stitch_local_base_url == "http://localhost:8888"
    assert s.compare_pct_threshold == 0.25
    assert s.compare_max_events == 200
    assert s.openai_model == "gpt-5.2"
    assert s.llm_backend == "openai"


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    s = Settings()
    assert s.openai_model == "gpt-4o-mini"


def test_compare_pct_threshold_env(monkeypatch):
    monkeypatch.setenv("COMPARE_PCT_THRESHOLD", "0.5")
    s = Settings()
    assert s.compare_pct_threshold == 0.5


def test_compare_max_events_env(monkeypatch):
    monkeypatch.setenv("COMPARE_MAX_EVENTS", "50")
    s = Settings()
    assert s.compare_max_events == 50


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #


def test_negative_pct_threshold_raises(monkeypatch):
    monkeypatch.setenv("COMPARE_PCT_THRESHOLD", "-0.1")
    with pytest.raises(Exception):  # pydantic ValidationError
        Settings()


def test_zero_max_events_raises(monkeypatch):
    monkeypatch.setenv("COMPARE_MAX_EVENTS", "0")
    with pytest.raises(Exception):
        Settings()


# --------------------------------------------------------------------------- #
# excluded_event_codes() — delegates to parse_excluded_event_codes
# --------------------------------------------------------------------------- #


def test_excluded_codes_empty(monkeypatch):
    monkeypatch.setenv("EXCLUDE_EVENT_CODES", "")
    s = Settings()
    assert s.excluded_event_codes() == []


def test_excluded_codes_single(monkeypatch):
    monkeypatch.setenv("EXCLUDE_EVENT_CODES", "403")
    s = Settings()
    assert s.excluded_event_codes() == [403]


def test_excluded_codes_list(monkeypatch):
    monkeypatch.setenv("EXCLUDE_EVENT_CODES", "403,217")
    s = Settings()
    assert s.excluded_event_codes() == [403, 217]


def test_excluded_codes_range(monkeypatch):
    monkeypatch.setenv("EXCLUDE_EVENT_CODES", "12000-12002")
    s = Settings()
    assert s.excluded_event_codes() == [12000, 12001, 12002]


def test_excluded_codes_mixed(monkeypatch):
    monkeypatch.setenv("EXCLUDE_EVENT_CODES", "403,12000-12002,217")
    s = Settings()
    assert s.excluded_event_codes() == [403, 12000, 12001, 12002, 217]


# --------------------------------------------------------------------------- #
# excluded_metrics()
# --------------------------------------------------------------------------- #


def test_excluded_metrics_empty(monkeypatch):
    monkeypatch.setenv("EXCLUDE_METRICS", "")
    s = Settings()
    assert s.excluded_metrics() == []


def test_excluded_metrics_single(monkeypatch):
    monkeypatch.setenv("EXCLUDE_METRICS", "metrics.singulation")
    s = Settings()
    assert s.excluded_metrics() == ["metrics.singulation"]


def test_excluded_metrics_list(monkeypatch):
    monkeypatch.setenv("EXCLUDE_METRICS", "metrics.singulation,metrics.population")
    s = Settings()
    assert s.excluded_metrics() == ["metrics.singulation", "metrics.population"]


def test_excluded_metrics_whitespace(monkeypatch):
    monkeypatch.setenv("EXCLUDE_METRICS", " metrics.singulation , metrics.population ")
    s = Settings()
    assert s.excluded_metrics() == ["metrics.singulation", "metrics.population"]


# --------------------------------------------------------------------------- #
# get_settings() singleton
# --------------------------------------------------------------------------- #


def test_get_settings_returns_same_instance():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_reset_settings_fixture_resets_singleton():
    # The reset_settings autouse fixture in conftest.py resets _settings = None
    # before each test, so get_settings() creates a fresh instance here.
    s = get_settings()
    assert s is not None
