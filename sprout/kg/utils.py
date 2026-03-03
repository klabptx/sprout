"""Shared anomaly-detection and finding-accumulation logic.

Used by both the LangGraph pipeline (``sprout.graph``) and the batch CLI
scripts (``scripts/compare_event_records.py``).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

import requests

from sprout.exceptions import StitchAPIError


# ---------------------------------------------------------------------------
# Constants — read lazily from settings so tests can override via env vars.
# ---------------------------------------------------------------------------


def _stitch_base() -> str:
    from sprout.config import get_settings
    return get_settings().stitch_local_base_url.rstrip("/")


def _compare_pct_threshold() -> float:
    from sprout.config import get_settings
    return get_settings().compare_pct_threshold


def _compare_abs_threshold() -> float:
    from sprout.config import get_settings
    return get_settings().compare_abs_threshold


def _compare_max_events() -> int:
    from sprout.config import get_settings
    return get_settings().compare_max_events


# Keep module-level names for callers that imported them directly from utils.py.
# These are evaluated at import time from the environment, matching the old behaviour.
import os as _os
STITCH_BASE: str = _os.getenv("STITCH_LOCAL_BASE_URL", "http://localhost:8888").rstrip("/")
COMPARE_PCT_THRESHOLD: float = float(_os.getenv("COMPARE_PCT_THRESHOLD", "0.25"))
COMPARE_ABS_THRESHOLD: float = float(_os.getenv("COMPARE_ABS_THRESHOLD", "0.0"))
COMPARE_MAX_EVENTS: int = int(_os.getenv("COMPARE_MAX_EVENTS", "200"))


def parse_excluded_event_codes(raw: str) -> list[int]:
    """Parse a comma-separated list of codes and/or ranges into a flat list.

    Examples:
        "403"            → [403]
        "403,217"        → [403, 217]
        "12000-13000"    → [12000, 12001, ..., 13000]
        "403,12000-13000,217"  → [403, 12000..13000, 217]
    """
    codes: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                codes.extend(range(int(parts[0]), int(parts[1]) + 1))
        elif token.isdigit():
            codes.append(int(token))
    return codes


# ---------------------------------------------------------------------------
# Proto event-code definitions
# ---------------------------------------------------------------------------

_ENTRY_RE = re.compile(
    r"(\w+)\s*=\s*(\d+)\s*\[\s*\(option_event_definition\)\s*=\s*\{([^}]*(?:\}[^;])*?)\}\s*\]",
    re.DOTALL,
)
_FIELD_RE = re.compile(
    r"""(\w+)\s*:\s*("(?:[^"\\]|\\.)*"(?:\s*"(?:[^"\\]|\\.)*")*)""",
    re.DOTALL,
)


def parse_proto_event_codes(proto_path: str) -> dict[int, dict[str, str]]:
    """Parse a SystemLog.proto file and return event code definitions.

    Returns ``{code_int: {"name": ..., "title": ..., "description": ..., "recommendation": ...}}``.
    Fields absent in the proto entry are omitted from the dict.
    """
    try:
        with open(proto_path, "r") as f:
            text = f.read()
    except FileNotFoundError:
        return {}

    result: dict[int, dict[str, str]] = {}
    for m in _ENTRY_RE.finditer(text):
        name, code_str, body = m.group(1), m.group(2), m.group(3)
        code = int(code_str)
        entry: dict[str, str] = {"name": name}
        for fm in _FIELD_RE.finditer(body):
            field_name = fm.group(1)
            raw = fm.group(2)
            value = "".join(s.strip('"') for s in re.split(r'"\s*"', raw)).strip()
            if field_name in ("title", "description", "recommendation"):
                entry[field_name] = value
        result[code] = entry
    return result


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def get_json(url: str, retries: int = 3, timeout: int = 30) -> Any:
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        resp = requests.get(url, timeout=timeout)
        if resp.ok:
            return resp.json()
        last_error = f"{resp.status_code} for {url}: {resp.text.strip()}"
        if resp.status_code in {500, 502, 503, 504} and attempt < retries:
            continue
        break
    raise StitchAPIError(last_error or f"Request failed for {url}", url=url, attempts=retries)


def extract_metrics(payload: dict) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for item in payload.get("metrics", []):
        key = item.get("key") or item.get("name")
        if not key:
            continue
        value = item.get("value", {})
        if isinstance(value, dict) and value.get("implement_average") is not None:
            metrics[f"metrics.{key}"] = float(value["implement_average"])
    return metrics


def load_record_metrics(application_id: str, record_id: int) -> dict[str, float]:
    url = f"{_stitch_base()}/local/metrics/{application_id}?record={record_id}"
    try:
        payload = get_json(url)
    except StitchAPIError as exc:
        # 404 means the record simply has no metrics data — return empty so
        # the caller skips it instead of treating it as an error.
        if "404" in str(exc):
            return {}
        raise
    return extract_metrics(payload)


def load_applications() -> list[dict]:
    url = f"{_stitch_base()}/local/applications?l=en-US"
    return get_json(url)


def load_application_metric_keys(application_id: str) -> list[dict[str, str]]:
    url = f"{_stitch_base()}/local/metrics/{application_id}"
    payload = get_json(url)
    return [
        {"key": m["key"], "name": m.get("name", m["key"])}
        for m in payload.get("metrics", [])
        if m.get("key")
    ]


def load_summary_metrics(application_id: str) -> dict[str, float]:
    """Fetch summary-level averages for an application.

    Calls ``GET /local/metrics/{app_id}`` (without a ``?record=`` param) and
    extracts ``implement_average`` values into ``metrics.{key}: float`` format.
    """
    url = f"{_stitch_base()}/local/metrics/{application_id}"
    payload = get_json(url)
    return extract_metrics(payload)


def load_events(limit: int = 200) -> list[dict]:
    """Fetch diagnostic events from Stitch.

    Calls ``GET /local/events?start=0&limit={limit}`` and returns the raw
    event list.
    """
    url = f"{_stitch_base()}/local/events?start=0&limit={limit}"
    payload = get_json(url)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "events", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return nested
    return []


def build_metric_to_app_map() -> dict[str, dict[str, str]]:
    apps = load_applications()
    mapping: dict[str, dict[str, str]] = {}
    for app in apps:
        app_id = app.get("application_id", "")
        app_name = app.get("name", "")
        app_type = app.get("type", {})
        type_key = app_type.get("key", "unknown")
        type_name = app_type.get("name", app_name)
        try:
            metrics = load_application_metric_keys(app_id)
        except StitchAPIError:
            continue
        for m in metrics:
            mapping[f"metrics.{m['key']}"] = {
                "application_id": app_id,
                "application_type": type_key,
                "application_name": type_name,
                "metric_name": m["name"],
            }
    return mapping


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


def compare_metrics(
    summary: dict[str, float],
    record: dict[str, float],
    pct_threshold: float | None = None,
    abs_threshold: float | None = None,
) -> list[dict[str, Any]]:
    if pct_threshold is None:
        pct_threshold = _compare_pct_threshold()
    if abs_threshold is None:
        abs_threshold = _compare_abs_threshold()

    anomalies: list[dict[str, Any]] = []
    for key, summary_val in summary.items():
        if key not in record:
            continue
        record_val = record[key]
        abs_delta = record_val - summary_val
        if summary_val == 0:
            # Skip metrics with a zero summary average — percent deviation is
            # undefined and likely reflects a data/average calculation issue.
            continue
        pct_delta = abs_delta / abs(summary_val)
        if abs(pct_delta) >= pct_threshold and abs(abs_delta) >= abs_threshold:
            anomalies.append(
                {
                    "metric": key,
                    "summary": summary_val,
                    "record": record_val,
                    "pct_delta": pct_delta,
                    "abs_delta": abs_delta,
                }
            )
    return anomalies


def record_span(event: dict) -> int:
    start = int(event.get("start_record") or 0)
    end = event.get("end_record")
    if end is not None:
        return max(0, int(end) - start)
    return 0


def spatial_cell(event: dict) -> tuple[float, float] | None:
    loc = event.get("location")
    if not loc:
        return None
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if not isinstance(loc, dict):
        return None
    lat = loc.get("lat") or loc.get("latitude")
    lon = loc.get("lon") or loc.get("lng") or loc.get("longitude")
    if lat is None or lon is None:
        return None
    return (round(float(lat), 4), round(float(lon), 4))


def compute_priority(
    anomalies: list[dict[str, Any]],
    event: dict,
    code_count: int,
    spatial_counts: Counter,
) -> float:
    if not anomalies:
        return 0.0
    co = len(anomalies)
    co_score = co ** 2 * max(abs(a["pct_delta"]) for a in anomalies)
    span = record_span(event)
    duration_weight = 1.0 + math.log1p(span)
    frequency_factor = math.log2(1 + code_count)
    cell = spatial_cell(event)
    spatial_boost = 1.0
    if cell is not None and spatial_counts[cell] > 1:
        spatial_boost = min(2.0, spatial_counts[cell] / 3)
    return co_score * duration_weight * frequency_factor * spatial_boost


# ---------------------------------------------------------------------------
# Finding accumulation by application type
# ---------------------------------------------------------------------------


def build_findings_by_app_type(
    events: list[dict[str, Any]],
    metric_to_app: dict[str, dict[str, str]],
    fallback_application_id: str,
    event_code_defs: dict[int, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Accumulate anomalous events into findings grouped by application type.

    Each *event* dict must have at minimum::

        {
            "event_id":   <any hashable identifier>,
            "event_code": int,
            "severity":   float,
            "anomalies":  [{"metric": str, "summary": float, "pct_delta": float, ...}],
        }

    Returns a list of plain finding dicts (no KG wrappers), sorted by severity
    descending.
    """
    app_acc: dict[str, dict[str, Any]] = {}

    for evt in events:
        for anomaly in evt.get("anomalies", []):
            metric_key: str = anomaly["metric"]
            app_info = metric_to_app.get(metric_key)
            if not app_info:
                app_info = {
                    "application_id": fallback_application_id,
                    "application_type": "unknown",
                    "application_name": "Unknown",
                    "metric_name": metric_key.removeprefix("metrics."),
                }
            type_key = app_info["application_type"]
            if type_key not in app_acc:
                app_acc[type_key] = {
                    "application_id": app_info["application_id"],
                    "application_type": type_key,
                    "application_name": app_info["application_name"],
                    "event_ids": set(),
                    "code_events": defaultdict(set),
                    "metrics": {},
                    "max_severity": 0.0,
                }
            acc = app_acc[type_key]
            eid = evt["event_id"]
            acc["event_ids"].add(eid)
            acc["code_events"][str(evt["event_code"])].add(eid)
            acc["max_severity"] = max(acc["max_severity"], evt.get("severity", 0))

            if metric_key not in acc["metrics"]:
                acc["metrics"][metric_key] = {
                    "name": app_info["metric_name"],
                    "peak_pos": 0.0,
                    "peak_neg": 0.0,
                    "avg": anomaly["summary"],
                    "event_count": 0,
                }
            m = acc["metrics"][metric_key]
            m["event_count"] += 1
            pct_d = anomaly["pct_delta"]
            if pct_d > 0:
                m["peak_pos"] = max(m["peak_pos"], pct_d)
            else:
                m["peak_neg"] = min(m["peak_neg"], pct_d)

    findings: list[dict[str, Any]] = []
    for acc in app_acc.values():
        metric_summaries: list[dict[str, Any]] = []
        metric_lines: list[str] = []
        for mk, m in acc["metrics"].items():
            metric_summaries.append(
                {
                    "metric_key": mk,
                    "metric_name": m["name"],
                    "peak_pct_pos": m["peak_pos"],
                    "peak_pct_neg": m["peak_neg"],
                    "summary_average": m["avg"],
                    "event_count": m["event_count"],
                }
            )
            metric_lines.append(
                f"{m['name']}: +{m['peak_pos']:.0%} / {m['peak_neg']:.0%}, "
                f"avg {m['avg']:.3f} ({m['event_count']} events)"
            )

        event_code_counts = {
            code: len(evt_set) for code, evt_set in acc["code_events"].items()
        }
        defs = event_code_defs or {}
        code_lines: list[str] = []
        for code, count in sorted(
            event_code_counts.items(), key=lambda x: x[1], reverse=True
        ):
            ecd = defs.get(int(code))
            if ecd:
                line = f"{code}: {ecd.get('title', 'Unknown')} ({count})"
                if ecd.get("description"):
                    line += f"\n    {ecd['description']}"
                if ecd.get("recommendation"):
                    line += f"\n    Recommendation: {ecd['recommendation']}"
            else:
                line = f"{code} ({count})"
            code_lines.append(line)

        diagnosis_prompt = (
            f"{acc['application_name']} Findings:\n"
            f"Anomalous metrics detected:\n"
            + "\n".join(metric_lines)
            + "\n\nEvent codes:\n"
            + "\n".join(code_lines)
        )

        findings.append(
            {
                "application_id": acc["application_id"],
                "application_type": acc["application_type"],
                "application_name": acc["application_name"],
                "severity": acc["max_severity"],
                "event_ids": sorted(acc["event_ids"]),
                "event_count": len(acc["event_ids"]),
                "metric_summaries": metric_summaries,
                "event_code_counts": event_code_counts,
                "diagnosis_prompt": diagnosis_prompt,
            }
        )

    findings.sort(key=lambda f: f["severity"], reverse=True)
    return findings
