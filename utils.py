"""Shared anomaly-detection and finding-accumulation logic.

DEPRECATED: This module is a compatibility shim.
            Import from ``sprout.kg.utils`` directly.
"""
# Re-export everything from the canonical location so existing callers continue
# to work while the migration to sprout.kg.utils is underway.
from sprout.kg.utils import (  # noqa: F401
    STITCH_BASE,
    COMPARE_PCT_THRESHOLD,
    COMPARE_ABS_THRESHOLD,
    COMPARE_MAX_EVENTS,
    parse_excluded_event_codes,
    parse_proto_event_codes,
    get_json,
    extract_metrics,
    load_record_metrics,
    load_applications,
    load_application_metric_keys,
    build_metric_to_app_map,
    compare_metrics,
    record_span,
    spatial_cell,
    compute_priority,
    build_findings_by_app_type,
)
