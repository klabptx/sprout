#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any

from sprout.config import get_settings
from sprout.kg.utils import (
    get_json,
    load_record_metrics,
    build_metric_to_app_map,
    compare_metrics,
    spatial_cell,
    compute_priority,
    build_findings_by_app_type,
)


def _load_tailor_stream() -> dict:
    url = get_settings().tailor_stream_url_resolved()
    return get_json(url)


def _stream_id_from_url(url: str) -> str | None:
    parts = [p for p in url.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "streams":
        return parts[-1]
    return None


def main() -> int:
    s = get_settings()
    output_dir = s.compare_output_dir
    output_prefix = s.compare_output_prefix
    max_events = s.compare_max_events
    tailor_url = s.tailor_stream_url
    excluded_codes: set[int] = set(s.excluded_event_codes())

    stream = _load_tailor_stream()
    current = stream.get("currentStream", {})
    summary_rows = current.get("summaryData", [])
    event_details = current.get("diagnosticEventDetails", [])
    diagnostics = current.get("diagnostics", [])

    if not summary_rows:
        print("No summary data found.")
        return 1
    if not event_details:
        print("No diagnostic event details found.")
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            stream_id = _stream_id_from_url(tailor_url) if tailor_url else None
            prefix = output_prefix
            if stream_id:
                prefix = f"{prefix}_{stream_id}"
            path = os.path.join(output_dir, f"{prefix}.json")
            summary_row = summary_rows[0]
            summary_metrics = {
                k: float(v)
                for k, v in summary_row.items()
                if k.startswith("metrics.") and isinstance(v, (int, float))
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "applicationId": summary_row.get("applicationId"),
                        "summary": summary_metrics,
                        "events": [],
                        "findings": [],
                        "note": "No diagnostic event details found.",
                    },
                    handle,
                    indent=2,
                )
        return 2

    summary_row = summary_rows[0]
    application_id = summary_row.get("applicationId")
    summary_metrics = {
        k: float(v)
        for k, v in summary_row.items()
        if k.startswith("metrics.") and isinstance(v, (int, float))
    }

    if not application_id:
        print("No applicationId in summary row.")
        return 1
    if not summary_metrics:
        print("No summary metrics found (metrics.*).")
        return 1

    # Pre-compute per-code counts from diagnostics aggregate row
    code_counts: dict[str, int] = {}
    if diagnostics:
        codes_map = diagnostics[0].get("codes", {})
        for code_str, info in codes_map.items():
            code_counts[code_str] = info.get("count", 1) if isinstance(info, dict) else 1

    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in event_details:
        code = event.get("eventCode")
        start_record = event.get("start_record")
        if code is None or start_record is None:
            continue
        if int(code) in excluded_codes:
            continue
        grouped[str(code)].append(event)

    # Pre-compute spatial cell counts across all events for clustering
    spatial_counts: Counter = Counter()
    for events in grouped.values():
        for event in events:
            cell = spatial_cell(event)
            if cell is not None:
                spatial_counts[cell] += 1

    # Collect all anomalous results with their priority scores
    results: list[tuple[float, str, int, int | None, list[dict[str, Any]]]] = []
    for code, events in grouped.items():
        for event in events:
            start_record = int(event["start_record"])
            end_record = event.get("end_record")
            try:
                record_metrics = load_record_metrics(application_id, start_record)
            except Exception as exc:
                print(f"Failed to load record metrics for event {code} @ {start_record}: {exc}")
                continue

            anomalies = compare_metrics(summary_metrics, record_metrics)
            if not anomalies:
                continue

            priority = compute_priority(
                anomalies, event, code_counts.get(code, 1), spatial_counts
            )
            results.append((priority, code, start_record, end_record, anomalies))

    if not results:
        print("No anomalies found with current thresholds.")
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            stream_id = _stream_id_from_url(tailor_url) if tailor_url else None
            prefix = output_prefix
            if stream_id:
                prefix = f"{prefix}_{stream_id}"
            path = os.path.join(output_dir, f"{prefix}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "applicationId": application_id,
                        "summary": summary_metrics,
                        "events": [],
                        "findings": [],
                    },
                    handle,
                    indent=2,
                )
        return 2

    results.sort(key=lambda r: r[0], reverse=True)
    max_priority = max(results[0][0], 1e-9)

    # Build event dicts with normalized severity
    events: list[dict[str, Any]] = []
    for idx, (priority, code, start_record, end_record, anomalies) in enumerate(results):
        severity = min(1.0, priority / max_priority)
        span = (
            max(0, int(end_record) - int(start_record)) if end_record is not None else None
        )
        events.append({
            "event_id": idx,
            "event_code": int(code),
            "eventCode": int(code),
            "start_record": start_record,
            "end_record": int(end_record) if end_record is not None else None,
            "event_length": span,
            "priority": priority,
            "severity": round(severity, 4),
            "anomalies": anomalies,
        })

    for rank, evt in enumerate(events[:max_events], 1):
        print("")
        span_note = f" len={evt['event_length']}" if evt["event_length"] is not None else ""
        print(f"#{rank}  Event code {evt['event_code']} @ record {evt['start_record']}"
              f"{span_note}  [priority={evt['priority']:.2f}]")
        print(f"  anomalous metrics: {len(evt['anomalies'])}")
        for a in sorted(evt["anomalies"], key=lambda x: abs(x["pct_delta"]), reverse=True)[:6]:
            print(
                f"    {a['metric']}: record={a['record']:.3f} "
                f"summary={a['summary']:.3f} pct_delta={a['pct_delta']:.2%}"
            )

    # Build findings grouped by application type
    try:
        metric_to_app = build_metric_to_app_map()
    except Exception as exc:
        print(f"Warning: could not load application mapping: {exc}")
        metric_to_app = {}

    findings = build_findings_by_app_type(events, metric_to_app, application_id)

    if findings:
        print("")
        print(f"Findings by application type ({len(findings)}):")
        for f in findings:
            print(f"  {f['application_name']} ({f['application_type']}): "
                  f"{f['event_count']} events, severity={f['severity']:.2f}")

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        stream_id = _stream_id_from_url(tailor_url) if tailor_url else None
        prefix = output_prefix
        if stream_id:
            prefix = f"{prefix}_{stream_id}"
        path = os.path.join(output_dir, f"{prefix}.json")
        # Strip internal fields before serialization
        serializable_events = [
            {k: v for k, v in e.items() if k not in ("event_id",)}
            for e in events
        ]
        payload = {
            "applicationId": application_id,
            "summary": summary_metrics,
            "events": serializable_events,
            "findings": findings,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
