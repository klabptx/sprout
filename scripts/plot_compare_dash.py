#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import os
from typing import Any

import dash
from dash import Dash, dcc, html, dash_table, Input, Output, State
import plotly.express as px


INPUT_GLOB = os.getenv("COMPARE_RESULTS_GLOB", "artifacts/compare_results/*.json")
PORT = int(os.getenv("COMPARE_DASH_PORT", "8050"))
CACHE_PATH = os.getenv("COMPARE_DASH_CACHE_PATH", "artifacts/compare_results/cache.json")
USE_CACHE = os.getenv("COMPARE_DASH_USE_CACHE", "true").lower() in ("1", "true", "yes")
MAX_ROWS = int(os.getenv("COMPARE_DASH_MAX_ROWS", "5000"))
BATCH_JSONL_PATHS = [
    p for p in [
        os.getenv("BATCH_JSONL_A", "artifacts/demo_batch_results52.jsonl"),
        os.getenv("BATCH_JSONL_B", "artifacts/demo_batch_results41.jsonl"),
    ]
    if os.path.exists(p)
]


def _load_batch_summaries(path: str) -> tuple[str, dict[str, dict]]:
    """Load a batch JSONL file and return (model_label, {file: summary_record})."""
    index: dict[str, dict] = {}
    model_label: str = os.path.basename(path)
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_key = record.get("file", "")
            result = record.get("result") or {}
            report = result.get("report") or {}
            summary = report.get("summary")
            llm_model = result.get("llm_model", "")
            # Use llm_model from the first record that has a real summary
            if llm_model and model_label == os.path.basename(path) and summary:
                model_label = llm_model
            index[file_key] = {
                "summary": summary,
                "severity": report.get("severity"),
                "confidence": report.get("confidence"),
                "llm_model": llm_model,
            }
    return model_label, index


def _load_results(paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_rows: list[dict[str, Any]] = []
    finding_rows: list[dict[str, Any]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        fname = os.path.basename(path)
        if isinstance(payload, list):
            for item in payload:
                item["file"] = fname
                event_rows.append(item)
            continue
        # Events: prefer "events" key, fall back to "results" for old files
        items = payload.get("events") or payload.get("results") or []
        for item in items:
            event_rows.append(
                {
                    "file": fname,
                    "eventCode": item.get("eventCode"),
                    "start_record": item.get("start_record"),
                    "end_record": item.get("end_record"),
                    "event_length": item.get("event_length"),
                    "priority": item.get("priority"),
                    "severity": item.get("severity"),
                    "anomalies": item.get("anomalies", []),
                }
            )
        # Findings by application type
        for finding in payload.get("findings", []):
            finding_rows.append({**finding, "file": fname})
    return event_rows, finding_rows


def _load_with_cache(paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not USE_CACHE:
        return _load_results(paths)
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    latest_mtime = max((os.path.getmtime(p) for p in paths), default=0)
    if os.path.exists(CACHE_PATH):
        cache_mtime = os.path.getmtime(CACHE_PATH)
        if cache_mtime >= latest_mtime:
            with open(CACHE_PATH, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if isinstance(cached, dict) and "events" in cached:
                return cached["events"], cached.get("findings", [])
            # Old cache format — just event rows
            return cached, []
    event_rows, finding_rows = _load_results(paths)
    with open(CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump({"events": event_rows, "findings": finding_rows}, handle)
    return event_rows, finding_rows


def _build_scatter(rows: list[dict[str, Any]]):
    if not rows:
        return px.scatter(title="No events")
    fig = px.scatter(
        rows,
        x=list(range(len(rows))),
        y="priority",
        size=[max(0.1, r.get("severity") or 0) for r in rows],
        color="eventCode",
        hover_data=["eventCode", "priority", "severity", "start_record", "end_record", "event_length", "file"],
        title="Events by Priority (ordered)",
    )
    fig.update_layout(xaxis_title="Rank (sorted)", yaxis_title="Priority")
    return fig


def main() -> None:
    cache_abs = os.path.abspath(CACHE_PATH)
    paths = sorted(p for p in glob.glob(INPUT_GLOB) if os.path.abspath(p) != cache_abs)
    rows, findings = _load_with_cache(paths)
    if MAX_ROWS > 0 and len(rows) > MAX_ROWS:
        rows = sorted(rows, key=lambda r: r.get("priority", 0), reverse=True)[:MAX_ROWS]
        print(f"Loaded top {MAX_ROWS} rows by priority (set COMPARE_DASH_MAX_ROWS=0 to disable).")

    batch_sources: list[tuple[str, dict]] = [_load_batch_summaries(p) for p in BATCH_JSONL_PATHS]

    app: Dash = dash.Dash(__name__)
    files = sorted({r["file"] for r in rows})
    event_codes = sorted({str(r["eventCode"]) for r in rows if r.get("eventCode") is not None})
    metric_keys = sorted(
        {
            a.get("metric")
            for r in rows
            for a in (r.get("anomalies") or [])
            if a.get("metric")
        }
    )
    app_types = sorted({f.get("application_type", "unknown") for f in findings})

    app.layout = html.Div(
        [
            html.H3("Event & Finding Explorer"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("File"),
                            dcc.Dropdown(
                                options=(
                                    [{"label": "All files", "value": "__ALL__"}]
                                    + [{"label": f, "value": f} for f in files]
                                ),
                                value="__ALL__" if files else None,
                                id="file-select",
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block"},
                    ),
                    html.Div(
                        [
                            html.Label("Application Type"),
                            dcc.Dropdown(
                                options=(
                                    [{"label": "All types", "value": "__ALL__"}]
                                    + [{"label": t, "value": t} for t in app_types]
                                ),
                                value="__ALL__",
                                id="apptype-select",
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block", "marginLeft": "1%"},
                    ),
                    html.Div(
                        [
                            html.Label("Event Code"),
                            dcc.Dropdown(
                                options=[{"label": c, "value": c} for c in event_codes],
                                value=None,
                                id="eventcode-select",
                                placeholder="All",
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block", "marginLeft": "1%"},
                    ),
                    html.Div(
                        [
                            html.Label("Metric filter"),
                            dcc.Dropdown(
                                options=[{"label": m, "value": m} for m in metric_keys],
                                value=None,
                                id="metric-select",
                                placeholder="All",
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block", "marginLeft": "1%"},
                    ),
                    html.Div(
                        [
                            html.Label("Lookup start_record"),
                            dcc.Input(
                                id="start-record-input",
                                type="number",
                                placeholder="start_record",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block", "marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Record range (min/max)"),
                            html.Div(
                                [
                                    dcc.Input(
                                        id="record-min-input",
                                        type="number",
                                        placeholder="min",
                                        style={"width": "48%"},
                                    ),
                                    dcc.Input(
                                        id="record-max-input",
                                        type="number",
                                        placeholder="max",
                                        style={"width": "48%", "marginLeft": "4%"},
                                    ),
                                ]
                            ),
                        ],
                        style={"width": "24%", "display": "inline-block", "marginLeft": "1%", "marginTop": "8px"},
                    ),
                ]
            ),
            dcc.Graph(id="priority-graph"),
            html.H4("Findings by Application Type"),
            dash_table.DataTable(
                id="findings-table",
                columns=[
                    {"name": "Application", "id": "application_name"},
                    {"name": "Type", "id": "application_type"},
                    {"name": "Events", "id": "event_count"},
                    {"name": "Severity", "id": "severity"},
                    {"name": "File", "id": "file"},
                ],
                data=[],
                page_size=8,
                style_table={"overflowX": "auto"},
                row_selectable="single",
            ),
            html.H4("Finding Detail"),
            html.Div(id="finding-detail", style={"whiteSpace": "pre-wrap", "fontFamily": "monospace", "padding": "8px", "background": "#f5f5f5", "borderRadius": "4px"}),
            html.H4("Metric Summary (filtered events)"),
            dash_table.DataTable(
                id="summary-table",
                columns=[
                    {"name": "metric", "id": "metric"},
                    {"name": "event_codes", "id": "event_codes"},
                    {"name": "event_count", "id": "event_count"},
                ],
                data=[],
                page_size=8,
                style_table={"overflowX": "auto"},
            ),
            html.H4("Selected event anomalies"),
            dash_table.DataTable(
                id="anomalies-table",
                columns=[
                    {"name": "metric", "id": "metric"},
                    {"name": "summary", "id": "summary"},
                    {"name": "record", "id": "record"},
                    {"name": "pct_delta", "id": "pct_delta"},
                    {"name": "abs_delta", "id": "abs_delta"},
                ],
                data=[],
                page_size=8,
                style_table={"overflowX": "auto"},
            ),
            dcc.Store(id="filtered-rows"),
            dcc.Store(id="filtered-findings"),
            html.Div(
                id="llm-comparison-section",
                style={"display": "none", "marginTop": "24px"},
                children=[
                    html.H4("LLM Summary Comparison"),
                    html.Div(
                        id="llm-comparison-panels",
                        style={"display": "flex", "gap": "16px"},
                    ),
                ],
            ),
        ],
        style={"padding": "16px"},
    )

    # Build index: for each finding, the set of metric keys it covers
    finding_metric_sets: dict[int, set[str]] = {}
    for i, f in enumerate(findings):
        finding_metric_sets[i] = {
            ms.get("metric_key", "") for ms in f.get("metric_summaries", [])
        }

    @app.callback(
        Output("filtered-rows", "data"),
        Output("filtered-findings", "data"),
        Output("priority-graph", "figure"),
        Output("summary-table", "data"),
        Output("findings-table", "data"),
        Input("file-select", "value"),
        Input("apptype-select", "value"),
        Input("eventcode-select", "value"),
        Input("metric-select", "value"),
        Input("start-record-input", "value"),
        Input("record-min-input", "value"),
        Input("record-max-input", "value"),
    )
    def _filter_rows(file_value, app_type, event_code, metric_value, start_record, record_min, record_max):
        filtered = rows
        filtered_findings = findings
        if file_value and file_value != "__ALL__":
            filtered = [r for r in filtered if r["file"] == file_value]
            filtered_findings = [f for f in filtered_findings if f.get("file") == file_value]
        if app_type and app_type != "__ALL__":
            # Filter findings by app type
            filtered_findings = [f for f in filtered_findings if f.get("application_type") == app_type]
            # Filter events to those whose anomalies include metrics from this app type
            app_metrics = set()
            for f in filtered_findings:
                for ms in f.get("metric_summaries", []):
                    app_metrics.add(ms.get("metric_key", ""))
            if app_metrics:
                filtered = [
                    r for r in filtered
                    if any(a.get("metric") in app_metrics for a in (r.get("anomalies") or []))
                ]
        if event_code:
            filtered = [r for r in filtered if str(r["eventCode"]) == str(event_code)]
        if metric_value:
            filtered = [
                r
                for r in filtered
                if any(a.get("metric") == metric_value for a in (r.get("anomalies") or []))
            ]
            filtered_findings = [
                f for f in filtered_findings
                if any(ms.get("metric_key") == metric_value for ms in f.get("metric_summaries", []))
            ]
        if start_record:
            sr = int(start_record)
            def _in_range(r):
                end = r.get("end_record")
                if end is None:
                    return r["start_record"] == sr
                return r["start_record"] <= sr <= int(end)
            filtered = [r for r in filtered if _in_range(r)]
        if record_min is not None:
            filtered = [r for r in filtered if r["start_record"] >= int(record_min)]
        if record_max is not None:
            filtered = [r for r in filtered if r["start_record"] <= int(record_max)]

        filtered = sorted(filtered, key=lambda r: r.get("priority", 0), reverse=True)
        fig = _build_scatter(filtered)

        # Build metric summary from filtered events
        summary_map: dict[str, dict[str, Any]] = {}
        for row in filtered:
            code = row.get("eventCode")
            for anomaly in row.get("anomalies") or []:
                metric = anomaly.get("metric") or "unknown"
                entry = summary_map.setdefault(metric, {"metric": metric, "codes": set(), "count": 0})
                if code is not None:
                    entry["codes"].add(str(code))
                entry["count"] += 1

        summary_rows = [
            {
                "metric": metric,
                "event_codes": ", ".join(sorted(entry["codes"])),
                "event_count": entry["count"],
            }
            for metric, entry in summary_map.items()
        ]
        summary_rows.sort(key=lambda r: r["event_count"], reverse=True)

        # Findings table rows
        findings_table = [
            {
                "application_name": f.get("application_name", ""),
                "application_type": f.get("application_type", ""),
                "event_count": f.get("event_count", 0),
                "severity": round(f.get("severity", 0), 4),
                "file": f.get("file", ""),
            }
            for f in filtered_findings
        ]
        findings_table.sort(key=lambda r: r["severity"], reverse=True)

        return filtered, filtered_findings, fig, summary_rows, findings_table

    @app.callback(
        Output("finding-detail", "children"),
        Input("findings-table", "selected_rows"),
        State("filtered-findings", "data"),
    )
    def _show_finding_detail(selected_rows, filtered_findings):
        if not filtered_findings or not selected_rows:
            if filtered_findings:
                return filtered_findings[0].get("diagnosis_prompt", "")
            return "No findings."
        idx = selected_rows[0]
        if idx >= len(filtered_findings):
            return ""
        return filtered_findings[idx].get("diagnosis_prompt", "")

    @app.callback(
        Output("anomalies-table", "data"),
        Input("priority-graph", "clickData"),
        State("filtered-rows", "data"),
    )
    def _show_anomalies(click_data, filtered):
        if not filtered:
            return []
        if not click_data:
            return filtered[0].get("anomalies", [])
        point_index = click_data["points"][0]["pointIndex"]
        if point_index is None or point_index >= len(filtered):
            return []
        return filtered[point_index].get("anomalies", [])

    @app.callback(
        Output("llm-comparison-section", "style"),
        Output("llm-comparison-panels", "children"),
        Input("file-select", "value"),
    )
    def _show_llm_comparison(file_value):
        if not file_value or file_value == "__ALL__" or not batch_sources:
            return {"display": "none", "marginTop": "24px"}, []

        # file_value is a compare_results filename like "compare_<stem>.json"
        # JSONL keys are "<stem>.2020" — strip the prefix/suffix to match
        stem = file_value
        if stem.startswith("compare_"):
            stem = stem[len("compare_"):]
        if stem.endswith(".json"):
            stem = stem[: -len(".json")]
        jsonl_key = stem + ".2020"

        panels = []
        for model_label, index in batch_sources:
            record = index.get(jsonl_key, {})
            summary = record.get("summary") or "No summary available."
            severity = record.get("severity")
            confidence = record.get("confidence")
            meta_parts = []
            if severity is not None:
                meta_parts.append(f"Severity: {severity:.2f}")
            if confidence is not None:
                meta_parts.append(f"Confidence: {confidence:.2f}")
            meta = "  |  ".join(meta_parts)

            panels.append(html.Div(
                [
                    html.Strong(model_label),
                    html.Div(
                        meta,
                        style={"fontSize": "0.85em", "color": "#666", "marginBottom": "6px", "marginTop": "2px"},
                    ),
                    html.Div(summary, style={"whiteSpace": "pre-wrap"}),
                ],
                style={
                    "flex": "1",
                    "padding": "12px",
                    "background": "#f5f5f5",
                    "borderRadius": "4px",
                    "fontFamily": "sans-serif",
                    "minWidth": "0",
                },
            ))

        return {"display": "block", "marginTop": "24px"}, panels

    app.run(debug=False, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
