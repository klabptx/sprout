#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any

from sprout.config import get_settings
from sprout.kg.utils import (
    build_findings_by_app_type,
    build_metric_to_app_map,
    compare_metrics,
    compute_priority,
    load_applications,
    load_events,
    load_record_metrics,
    load_summary_metrics,
    spatial_cell,
)


def _select_default_app(apps: list[dict]) -> dict:
    """Pick the default application (same logic as the parse node)."""
    for app in apps:
        if app.get("default"):
            return app
    return apps[0]


def main() -> int:
    s = get_settings()
    output_dir = s.compare_output_dir
    output_prefix = s.compare_output_prefix
    max_events = s.compare_max_events
    excluded_codes: set[int] = set(s.excluded_event_codes())

    # Fetch data directly from Stitch
    apps = load_applications()
    if not apps:
        print("No applications found in Stitch.")
        return 1

    app = _select_default_app(apps)
    application_id = app["application_id"]

    summary_metrics = load_summary_metrics(application_id)
    if not summary_metrics:
        print("No summary metrics found.")
        return 1

    raw_events = load_events()
    if not raw_events:
        print("No diagnostic event details found.")
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, f"{output_prefix}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "applicationId": application_id,
                        "summary": summary_metrics,
                        "events": [],
                        "findings": [],
                        "note": "No diagnostic event details found.",
                    },
                    handle,
                    indent=2,
                )
        return 2

    # Normalize events into the shape expected by the comparison logic
    event_details: list[dict] = []
    code_counts: dict[str, int] = defaultdict(int)
    for evt in raw_events:
        code = evt.get("eventCode") or evt.get("event_code")
        start = evt.get("start_record") or evt.get("startRecord")
        if code is None or start is None:
            continue
        event_details.append({
            "eventCode": int(code),
            "start_record": int(start),
            "end_record": evt.get("end_record") or evt.get("endRecord"),
            "location": evt.get("location"),
        })
        code_counts[str(int(code))] += 1

    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in event_details:
        code = event["eventCode"]
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
            path = os.path.join(output_dir, f"{output_prefix}.json")
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
    events_out: list[dict[str, Any]] = []
    for idx, (priority, code, start_record, end_record, anomalies) in enumerate(results):
        severity = min(1.0, priority / max_priority)
        span = (
            max(0, int(end_record) - int(start_record)) if end_record is not None else None
        )
        events_out.append({
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

    for rank, evt in enumerate(events_out[:max_events], 1):
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

    findings = build_findings_by_app_type(events_out, metric_to_app, application_id)

    if findings:
        print("")
        print(f"Findings by application type ({len(findings)}):")
        for f in findings:
            print(f"  {f['application_name']} ({f['application_type']}): "
                  f"{f['event_count']} events, severity={f['severity']:.2f}")

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{output_prefix}.json")
        # Strip internal fields before serialization
        serializable_events = [
            {k: v for k, v in e.items() if k not in ("event_id",)}
            for e in events_out
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
