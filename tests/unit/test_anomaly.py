"""Unit tests for sprout.kg.utils anomaly detection and priority scoring."""

from __future__ import annotations

from collections import Counter

import pytest

from sprout.kg.utils import (
    build_findings_by_app_type,
    compare_metrics,
    compute_priority,
    parse_excluded_event_codes,
    record_span,
    spatial_cell,
)


# --------------------------------------------------------------------------- #
# compare_metrics
# --------------------------------------------------------------------------- #


def test_compare_metrics_detects_deviation_above_threshold():
    summary = {"metrics.singulation": 98.0}
    record = {"metrics.singulation": 72.0}  # ~26.5% below
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert len(result) == 1
    assert result[0]["metric"] == "metrics.singulation"
    assert result[0]["pct_delta"] < -0.25


def test_compare_metrics_ignores_deviation_below_threshold():
    summary = {"metrics.singulation": 98.0}
    record = {"metrics.singulation": 95.0}  # ~3% below
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert result == []


def test_compare_metrics_skips_zero_summary():
    # Zero summary average: percent deviation is undefined, must be skipped.
    summary = {"metrics.foo": 0.0}
    record = {"metrics.foo": 5.0}
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert result == []


def test_compare_metrics_positive_deviation():
    summary = {"metrics.downforce": 50.0}
    record = {"metrics.downforce": 80.0}  # +60%
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert len(result) == 1
    assert result[0]["pct_delta"] > 0


def test_compare_metrics_respects_abs_threshold():
    summary = {"metrics.vacuum": 10.0}
    record = {"metrics.vacuum": 7.0}  # -30% but abs_delta = -3
    # abs_threshold=5 means this should NOT be flagged
    result = compare_metrics(summary, record, pct_threshold=0.25, abs_threshold=5.0)
    assert result == []


def test_compare_metrics_skips_missing_record_key():
    summary = {"metrics.singulation": 98.0, "metrics.vacuum": 50.0}
    record = {"metrics.vacuum": 10.0}  # singulation missing
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert all(a["metric"] != "metrics.singulation" for a in result)


def test_compare_metrics_multiple_anomalies():
    summary = {"metrics.a": 100.0, "metrics.b": 200.0}
    record = {"metrics.a": 60.0, "metrics.b": 300.0}
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert len(result) == 2


def test_compare_metrics_result_fields():
    summary = {"metrics.singulation": 100.0}
    record = {"metrics.singulation": 70.0}
    result = compare_metrics(summary, record, pct_threshold=0.25)
    assert len(result) == 1
    r = result[0]
    assert set(r.keys()) == {"metric", "summary", "record", "pct_delta", "abs_delta"}
    assert r["summary"] == 100.0
    assert r["record"] == 70.0
    assert abs(r["pct_delta"] - (-0.3)) < 1e-9
    assert abs(r["abs_delta"] - (-30.0)) < 1e-9


# --------------------------------------------------------------------------- #
# record_span
# --------------------------------------------------------------------------- #


def test_record_span_with_end():
    event = {"start_record": 100, "end_record": 150}
    assert record_span(event) == 50


def test_record_span_without_end():
    event = {"start_record": 100}
    assert record_span(event) == 0


def test_record_span_negative_clamped():
    event = {"start_record": 200, "end_record": 150}
    assert record_span(event) == 0


# --------------------------------------------------------------------------- #
# spatial_cell
# --------------------------------------------------------------------------- #


def test_spatial_cell_dict_location():
    event = {"location": {"lat": 41.1234, "lon": -93.5432}}
    cell = spatial_cell(event)
    assert cell == (41.1234, -93.5432)


def test_spatial_cell_list_location():
    event = {"location": [{"lat": 41.1234, "lon": -93.5432}]}
    cell = spatial_cell(event)
    assert cell == (41.1234, -93.5432)


def test_spatial_cell_missing_location():
    assert spatial_cell({}) is None
    assert spatial_cell({"location": None}) is None


def test_spatial_cell_incomplete_coords():
    assert spatial_cell({"location": {"lat": 41.0}}) is None


def test_spatial_cell_alternative_key_names():
    event = {"location": {"latitude": 41.12345, "longitude": -93.54321}}
    cell = spatial_cell(event)
    assert cell is not None


# --------------------------------------------------------------------------- #
# compute_priority
# --------------------------------------------------------------------------- #


def test_compute_priority_empty_anomalies():
    assert compute_priority([], {}, 1, Counter()) == 0.0


def test_compute_priority_increases_with_more_anomalies():
    anomaly_base = [{"pct_delta": -0.3}]
    anomaly_more = [{"pct_delta": -0.3}, {"pct_delta": -0.4}]
    event = {"start_record": 100, "end_record": 110}
    p1 = compute_priority(anomaly_base, event, 1, Counter())
    p2 = compute_priority(anomaly_more, event, 1, Counter())
    assert p2 > p1


def test_compute_priority_increases_with_longer_span():
    anomalies = [{"pct_delta": -0.5}]
    short = {"start_record": 100, "end_record": 105}
    long_ = {"start_record": 100, "end_record": 200}
    p_short = compute_priority(anomalies, short, 1, Counter())
    p_long = compute_priority(anomalies, long_, 1, Counter())
    assert p_long > p_short


def test_compute_priority_spatial_boost():
    anomalies = [{"pct_delta": -0.5}]
    event = {"start_record": 100, "location": {"lat": 41.0, "lon": -93.0}}
    no_cluster = Counter()
    clustered = Counter({(41.0, -93.0): 6})
    p_base = compute_priority(anomalies, event, 1, no_cluster)
    p_boost = compute_priority(anomalies, event, 1, clustered)
    assert p_boost > p_base


# --------------------------------------------------------------------------- #
# parse_excluded_event_codes
# --------------------------------------------------------------------------- #


def test_parse_excluded_empty():
    assert parse_excluded_event_codes("") == []


def test_parse_excluded_single():
    assert parse_excluded_event_codes("403") == [403]


def test_parse_excluded_multiple():
    assert parse_excluded_event_codes("403,217") == [403, 217]


def test_parse_excluded_range():
    assert parse_excluded_event_codes("12000-12002") == [12000, 12001, 12002]


def test_parse_excluded_mixed():
    result = parse_excluded_event_codes("403,12000-12002,217")
    assert result == [403, 12000, 12001, 12002, 217]


def test_parse_excluded_whitespace_tolerance():
    assert parse_excluded_event_codes(" 403 , 217 ") == [403, 217]


# --------------------------------------------------------------------------- #
# build_findings_by_app_type
# --------------------------------------------------------------------------- #


def _make_event(event_id, event_code, severity, metric, pct_delta):
    return {
        "event_id": event_id,
        "event_code": event_code,
        "severity": severity,
        "anomalies": [
            {
                "metric": metric,
                "summary": 100.0,
                "record": 100.0 * (1 + pct_delta),
                "pct_delta": pct_delta,
                "abs_delta": 100.0 * pct_delta,
            }
        ],
    }


def test_build_findings_groups_by_app_type():
    metric_to_app = {
        "metrics.singulation": {
            "application_id": "app-001",
            "application_type": "seeding",
            "application_name": "Seeding",
            "metric_name": "singulation",
        }
    }
    events = [
        _make_event("e1", 403, 0.8, "metrics.singulation", -0.4),
        _make_event("e2", 404, 0.6, "metrics.singulation", -0.3),
    ]
    findings = build_findings_by_app_type(events, metric_to_app, "app-001")
    assert len(findings) == 1
    assert findings[0]["application_type"] == "seeding"
    assert len(findings[0]["event_ids"]) == 2


def test_build_findings_fallback_application():
    events = [_make_event("e1", 403, 0.8, "metrics.unknown_metric", -0.5)]
    findings = build_findings_by_app_type(events, {}, "fallback-app")
    assert len(findings) == 1
    assert findings[0]["application_type"] == "unknown"
    assert findings[0]["application_id"] == "fallback-app"


def test_build_findings_sorted_by_severity():
    metric_to_app = {
        "metrics.a": {
            "application_id": "a1",
            "application_type": "type_a",
            "application_name": "Type A",
            "metric_name": "a",
        },
        "metrics.b": {
            "application_id": "b1",
            "application_type": "type_b",
            "application_name": "Type B",
            "metric_name": "b",
        },
    }
    events = [
        _make_event("e1", 403, 0.3, "metrics.a", -0.4),
        _make_event("e2", 404, 0.9, "metrics.b", -0.6),
    ]
    findings = build_findings_by_app_type(events, metric_to_app, "app")
    assert findings[0]["severity"] >= findings[1]["severity"]


def test_build_findings_empty_events():
    assert build_findings_by_app_type([], {}, "app") == []
