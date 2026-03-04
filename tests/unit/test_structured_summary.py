"""Unit tests for sprout.kg.structured_summary."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from sprout.exceptions import StitchAPIError
from sprout.kg.structured_summary import (
    _detect_task,
    _extract_summary_metrics,
    _extract_value,
    _format_hybrids,
    _format_key,
    _pick_label,
    build_operational_prompt,
    build_structured_summary,
    format_structured_summary,
)


# --------------------------------------------------------------------------- #
# _format_key
# --------------------------------------------------------------------------- #


def test_format_key_snake_case():
    assert _format_key("singulation_pct_avg") == "Singulation Pct Avg"


def test_format_key_single_word():
    assert _format_key("acres") == "Acres"


# --------------------------------------------------------------------------- #
# _extract_value
# --------------------------------------------------------------------------- #


def test_extract_value_implement_average():
    assert _extract_value({"implement_average": 98.5}) == 98.5


def test_extract_value_labelled_scalar():
    assert _extract_value({"label": "Singulation", "unit": "%", "value": 98.5}) == 98.5


def test_extract_value_bare_number():
    assert _extract_value(42.0) == 42.0


def test_extract_value_none_when_empty():
    assert _extract_value({}) is None
    assert _extract_value(None) is None
    assert _extract_value("not a number") is None


def test_extract_value_prefers_implement_average():
    # implement_average takes priority over "value" field
    assert _extract_value({"implement_average": 1.0, "value": 2.0}) == 1.0


# --------------------------------------------------------------------------- #
# _pick_label
# --------------------------------------------------------------------------- #


def test_pick_label_from_value_label():
    val = {"label": "Singulation", "unit": "%", "value": 98.5}
    assert _pick_label("singulation_pct_avg", "A", val) == "Singulation (%)"


def test_pick_label_from_value_label_no_unit():
    val = {"label": "Population", "value": 32000}
    assert _pick_label("population_avg", "B", val) == "Population"


def test_pick_label_from_name():
    assert _pick_label("ride_quality_pct", "Good Ride", {}) == "Good Ride"


def test_pick_label_falls_back_to_key_for_short_name():
    assert _pick_label("channel_a_total", "A", {}) == "Channel A Total"


def test_pick_label_falls_back_to_key_for_missing_name():
    assert _pick_label("singulation_pct_avg", None, {}) == "Singulation Pct Avg"


# --------------------------------------------------------------------------- #
# _extract_summary_metrics
# --------------------------------------------------------------------------- #


def test_extract_summary_metrics_implement_average():
    payload = {
        "implement_average": {
            "singulation": {"label": "Singulation", "unit": "%", "value": 99.76},
            "population": {"label": "Population", "unit": "seeds/ac", "value": 32788.0},
        },
        "name": "Seeding",
        "type": {"key": "seeding", "name": "Seeding"},
    }
    metrics = _extract_summary_metrics(payload)
    assert len(metrics) == 2
    keys = {m["key"] for m in metrics}
    assert keys == {"singulation", "population"}
    sing = next(m for m in metrics if m["key"] == "singulation")
    assert sing["name"] == "Singulation (%)"
    assert sing["value"] == 99.76


def test_extract_summary_metrics_top_level_to_pair():
    payload = {
        "acres": {"label": "Area Covered", "unit": "ac", "value": 74.37},
        "average_speed": {"label": "Average Speed", "unit": "mph", "value": 7.7},
        "pass_count": 21,
        "type": {"key": "global", "name": "Equipment-Wide"},
    }
    metrics = _extract_summary_metrics(payload)
    assert len(metrics) == 2
    keys = {m["key"] for m in metrics}
    assert keys == {"acres", "average_speed"}


def test_extract_summary_metrics_skips_non_numeric_values():
    payload = {
        "implement_average": {
            "good": {"label": "Good", "unit": "%", "value": 50.0},
            "bad": {"label": "Bad", "unit": "%", "value": "N/A"},
        },
    }
    metrics = _extract_summary_metrics(payload)
    assert len(metrics) == 1
    assert metrics[0]["key"] == "good"


def test_extract_summary_metrics_empty_payload():
    assert _extract_summary_metrics({}) == []


# --------------------------------------------------------------------------- #
# format_structured_summary
# --------------------------------------------------------------------------- #


def test_format_empty_data():
    assert format_structured_summary([]) == ""


def test_format_single_app():
    data = [
        {
            "application_name": "Harvest App",
            "application_type_key": "harvest",
            "application_type_name": "Harvest",
            "raw_summary": {},
            "metrics": [
                {"key": "dry_yield_bu_per_ac", "name": "Dry Yield bu/ac", "value": 185.2},
                {"key": "grain_moisture_pct", "name": "Grain Moisture %", "value": 14.3},
            ],
        }
    ]
    result = format_structured_summary(data)
    assert "--- Structured Summary ---" in result
    assert "--- End Structured Summary ---" in result
    assert "Harvest (Harvest App)" in result
    assert "Dry Yield bu/ac: 185.20" in result
    assert "Grain Moisture %: 14.30" in result


def test_format_skips_null_values():
    data = [
        {
            "application_name": "Seed App",
            "application_type_key": "seeding",
            "application_type_name": "Seeding",
            "raw_summary": {},
            "metrics": [
                {"key": "population_avg", "name": "Population Avg", "value": 32450.0},
                {"key": "vacuum_in", "name": "Vacuum", "value": None},
            ],
        }
    ]
    result = format_structured_summary(data)
    assert "Population Avg: 32,450.00" in result
    assert "Vacuum" not in result


def test_format_same_name_and_type_no_duplicate_header():
    data = [
        {
            "application_name": "Harvest",
            "application_type_key": "harvest",
            "application_type_name": "Harvest",
            "raw_summary": {},
            "metrics": [
                {"key": "acres", "name": "Acres", "value": 150.0},
            ],
        }
    ]
    result = format_structured_summary(data)
    assert "Harvest (Harvest)" not in result
    assert "Harvest\n" in result


def test_format_multiple_apps():
    data = [
        {
            "application_name": "Harvest App",
            "application_type_key": "harvest",
            "application_type_name": "Harvest",
            "raw_summary": {},
            "metrics": [
                {"key": "yield", "name": "Yield", "value": 200.0},
            ],
        },
        {
            "application_name": "Seed App",
            "application_type_key": "seeding",
            "application_type_name": "Seeding",
            "raw_summary": {},
            "metrics": [
                {"key": "population", "name": "Population", "value": 32000.0},
            ],
        },
    ]
    result = format_structured_summary(data)
    assert "Harvest (Harvest App)" in result
    assert "Seeding (Seed App)" in result
    assert "Yield: 200.00" in result
    assert "Population: 32,000.00" in result


# --------------------------------------------------------------------------- #
# build_structured_summary
# --------------------------------------------------------------------------- #


@patch("sprout.kg.structured_summary.load_applications")
def test_build_handles_stitch_error(mock_load):
    mock_load.side_effect = StitchAPIError("connection refused", url="http://localhost:8888")
    result = build_structured_summary()
    assert result == ""


@patch("sprout.kg.structured_summary.load_applications")
def test_build_handles_empty_applications(mock_load):
    mock_load.return_value = []
    result = build_structured_summary()
    assert result == ""


@patch("sprout.kg.structured_summary._fetch_raw_summary")
@patch("sprout.kg.structured_summary.load_applications")
def test_build_uses_summary_endpoint(mock_load_apps, mock_fetch_raw):
    mock_load_apps.return_value = [
        {
            "application_id": "app1",
            "name": "Seeding",
            "type": {"key": "seeding", "name": "Seeding"},
        }
    ]
    mock_fetch_raw.return_value = {
        "implement_average": {
            "singulation": {"label": "Singulation", "unit": "%", "value": 99.76},
            "population": {"label": "Population", "unit": "seeds/ac", "value": 32788.0},
        },
        "name": "Seeding",
    }
    result = build_structured_summary()
    assert "--- Structured Summary ---" in result
    assert "Singulation (%): 99.76" in result
    assert "Population (seeds/ac): 32,788.00" in result


@patch("sprout.kg.structured_summary.load_application_metrics_with_values")
@patch("sprout.kg.structured_summary._fetch_raw_summary")
@patch("sprout.kg.structured_summary.load_applications")
def test_build_falls_back_to_metrics(mock_load_apps, mock_fetch_raw, mock_load_metrics):
    mock_load_apps.return_value = [
        {
            "application_id": "app1",
            "name": "Test Harvest",
            "type": {"key": "harvest", "name": "Harvest"},
        }
    ]
    mock_fetch_raw.side_effect = StitchAPIError("400", url="http://localhost:8888")
    mock_load_metrics.return_value = [
        {"key": "yield", "name": "Yield", "value": 180.5, "raw_value": {"implement_average": 180.5}},
    ]
    result = build_structured_summary()
    assert "--- Structured Summary ---" in result
    assert "Harvest (Test Harvest)" in result
    assert "Yield: 180.50" in result


# --------------------------------------------------------------------------- #
# _detect_task
# --------------------------------------------------------------------------- #


def test_detect_task_plant():
    assert _detect_task({"seeding", "global", "force"}) == "plant"


def test_detect_task_harvest():
    assert _detect_task({"harvest", "global", "implement"}) == "harvest"


def test_detect_task_spray():
    assert _detect_task({"liquid", "global", "implement"}) == "spray"


def test_detect_task_plant_takes_priority_over_liquid():
    # Plant runs can also have liquid apps (e.g. starter, nitrogen).
    assert _detect_task({"seeding", "liquid", "global"}) == "plant"


def test_detect_task_none():
    assert _detect_task({"global", "implement"}) is None


# --------------------------------------------------------------------------- #
# _format_hybrids
# --------------------------------------------------------------------------- #


def test_format_hybrids_single():
    hybrids = [{"name": {"label": "Hybrid", "value": "FS 6595X RIB"}}]
    assert _format_hybrids(hybrids) == "FS 6595X RIB"


def test_format_hybrids_two():
    hybrids = [
        {"name": {"label": "Hybrid", "value": "FS 6595X RIB"}},
        {"name": {"label": "Hybrid", "value": "SV Rate"}},
    ]
    assert _format_hybrids(hybrids) == "FS 6595X RIB, SV Rate"


def test_format_hybrids_many():
    hybrids = [
        {"name": {"label": "Hybrid", "value": f"H{i}"}}
        for i in range(4)
    ]
    assert _format_hybrids(hybrids) == "4 varieties"


def test_format_hybrids_empty():
    assert _format_hybrids([]) is None


def test_format_hybrids_missing_names():
    hybrids = [{"name": {"label": "Hybrid", "value": ""}}]
    assert _format_hybrids(hybrids) is None


# --------------------------------------------------------------------------- #
# build_operational_prompt
# --------------------------------------------------------------------------- #


def test_operational_prompt_none_when_no_task():
    apps_data = [
        {
            "application_type_key": "global",
            "application_type_name": "Equipment-Wide",
            "raw_summary": {},
            "metrics": [{"key": "acres", "name": "Acres", "value": 10.0}],
        },
    ]
    assert build_operational_prompt(apps_data) is None


def test_operational_prompt_plant():
    apps_data = [
        {
            "application_type_key": "seeding",
            "application_type_name": "Seeding",
            "raw_summary": {
                "hybrids": [
                    {"name": {"label": "Hybrid", "value": "FS 6595X RIB"}},
                    {"name": {"label": "Hybrid", "value": "SV Rate"}},
                ],
            },
            "metrics": [
                {"key": "population", "name": "Population (seeds/ac)", "value": 34146.76},
                {"key": "singulation", "name": "Singulation (%)", "value": 99.24},
            ],
        },
    ]
    result = build_operational_prompt(apps_data)
    assert result is not None
    assert "planting" in result
    assert "Population (seeds/ac): 34,146.76" in result
    assert "Singulation (%): 99.24" in result
    assert "Hybrids: FS 6595X RIB, SV Rate" in result


def test_operational_prompt_harvest():
    apps_data = [
        {
            "application_type_key": "global",
            "application_type_name": "Equipment-Wide",
            "raw_summary": {},
            "metrics": [
                {"key": "acres", "name": "Area Covered (ac)", "value": 9.8},
                {"key": "average_speed", "name": "Average Speed (mph)", "value": 2.7},
            ],
        },
        {
            "application_type_key": "harvest",
            "application_type_name": "Harvest",
            "raw_summary": {},
            "metrics": [
                {"key": "moisture", "name": "Moisture (%)", "value": 20.56},
                {"key": "dryyieldavg", "name": "Average Dry Yield (bu/ac)", "value": 223.0},
            ],
        },
    ]
    result = build_operational_prompt(apps_data)
    assert result is not None
    assert "harvest" in result
    assert "Area Covered (ac): 9.80" in result
    assert "Average Speed (mph): 2.70" in result
    assert "Moisture (%): 20.56" in result
    assert "Average Dry Yield (bu/ac): 223.00" in result


def test_operational_prompt_spray():
    apps_data = [
        {
            "application_type_key": "global",
            "application_type_name": "Equipment-Wide",
            "raw_summary": {},
            "metrics": [
                {"key": "acres", "name": "Area Covered (ac)", "value": 0.18},
                {"key": "average_speed", "name": "Average Speed (mph)", "value": 4.0},
            ],
        },
        {
            "application_type_key": "liquid",
            "application_type_name": "Liquid",
            "raw_summary": {},
            "metrics": [
                {"key": "rate", "name": "Rate (gal/ac)", "value": 43.0},
            ],
        },
    ]
    result = build_operational_prompt(apps_data)
    assert result is not None
    assert "spraying" in result
    assert "Area Covered (ac): 0.18" in result
    assert "Average Speed (mph): 4.00" in result
    assert "Rate (gal/ac): 43.00" in result


def test_operational_prompt_none_when_no_metrics():
    apps_data = [
        {
            "application_type_key": "harvest",
            "application_type_name": "Harvest",
            "raw_summary": {},
            "metrics": [],
        },
    ]
    # harvest detected but no matching metrics → None
    assert build_operational_prompt(apps_data) is None


def test_operational_prompt_plant_no_hybrids():
    apps_data = [
        {
            "application_type_key": "seeding",
            "application_type_name": "Seeding",
            "raw_summary": {},
            "metrics": [
                {"key": "population", "name": "Population (seeds/ac)", "value": 32000.0},
                {"key": "singulation", "name": "Singulation (%)", "value": 98.5},
            ],
        },
    ]
    result = build_operational_prompt(apps_data)
    assert result is not None
    assert "Population (seeds/ac): 32,000.00" in result
    assert "Hybrid" not in result
