"""Analyze node: compare record-level metrics against summary averages."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from typing import Any

from sprout.config import get_settings
from sprout.graph_types import EventPayload, FindingPayload, NodeEnvelope
from sprout.kg.utils import (
    build_findings_by_app_type,
    build_metric_to_app_map,
    compare_metrics,
    compute_priority,
    load_record_metrics,
    parse_proto_event_codes,
    spatial_cell,
)
from sprout.state import GraphState, KG, new_id

logger = logging.getLogger(__name__)


async def analyze_event_records(state: GraphState) -> dict:
    """Compare record-level metrics against summary for diagnostic events.

    Generates Event and Finding KG nodes for anomalous records.
    """
    summaries = state.get("summaryData", [])
    event_details = state.get("eventDetails", [])
    diagnostics = state.get("diagnostics", [])

    if not summaries or not event_details:
        logger.info("Analyze: no summary or event_details — skipping")
        return {"eventIds": [], "findingIds": [], "kg": {}}

    summary_row = summaries[0]
    application_id = summary_row.get("applicationId")
    summary_metrics = {
        k: float(v)
        for k, v in summary_row.items()
        if k.startswith("metrics.") and isinstance(v, (int, float))
    }

    if not application_id or not summary_metrics:
        logger.warning("Analyze: missing applicationId or summary metrics — skipping")
        return {"eventIds": [], "findingIds": [], "kg": {}}

    s = get_settings()

    # Pre-compute per-code counts from diagnostics aggregate
    code_counts: dict[str, int] = {}
    if diagnostics:
        codes_map = diagnostics[0].get("codes", {})
        for code_str, info in codes_map.items():
            code_counts[code_str] = info.get("count", 1) if isinstance(info, dict) else 1

    excluded_metrics: set[str] = set(state.get("excludeMetrics") or [])
    summary_metrics = {k: v for k, v in summary_metrics.items() if k not in excluded_metrics}

    if not summary_metrics:
        logger.info("Analyze: all summary metrics excluded — skipping")
        return {"eventIds": [], "findingIds": [], "kg": {}}

    excluded_codes: set[int] = set(state.get("excludeEventCodes") or [])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in event_details:
        code = event.get("eventCode")
        start_record = event.get("start_record")
        if code is None or start_record is None:
            continue
        if int(code) in excluded_codes:
            continue
        grouped[str(code)].append(event)

    # Pre-compute spatial cell counts for clustering boost
    spatial_counts: Counter = Counter()
    for events in grouped.values():
        for event in events:
            cell = spatial_cell(event)
            if cell is not None:
                spatial_counts[cell] += 1

    # Fetch record-level metrics and compare against summary
    results: list[tuple[float, str, int, int | None, list[dict[str, Any]], dict]] = []
    for code, events in grouped.items():
        for event in events:
            start_record = int(event["start_record"])
            end_record = event.get("end_record")
            try:
                record_metrics = await asyncio.to_thread(
                    load_record_metrics, application_id, start_record
                )
            except Exception as exc:
                logger.warning(
                    "Analyze: Stitch fetch failed for record %s (event %s): %s",
                    start_record,
                    code,
                    exc,
                )
                continue

            anomalies = compare_metrics(summary_metrics, record_metrics)
            if not anomalies:
                continue

            priority = compute_priority(
                anomalies, event, code_counts.get(code, 1), spatial_counts
            )
            results.append((priority, code, start_record, end_record, anomalies, event))

    if not results:
        logger.info("Analyze: no anomalies found with current thresholds")
        return {"eventIds": [], "findingIds": [], "kg": {}}

    results.sort(key=lambda r: r[0], reverse=True)
    top_results = results[: s.compare_max_events]
    max_priority = max(top_results[0][0], 1e-9)

    # Phase 1: create Event nodes
    event_code_defs = parse_proto_event_codes(s.event_proto_path)
    kg_updates: KG = {}
    event_ids: list[str] = []

    for priority, code, start_record, end_record, anomalies, event in top_results:
        severity = round(min(1.0, priority / max_priority), 2)
        span = max(0, int(end_record) - start_record) if end_record is not None else None
        top_anomalies = sorted(anomalies, key=lambda a: abs(a["pct_delta"]), reverse=True)
        summary_parts = [
            f"{a['metric']}={a['record']:.3f} (expected {a['summary']:.3f}, {a['pct_delta']:+.0%})"
            for a in top_anomalies[:3]
        ]
        ecd = event_code_defs.get(int(code))
        code_label = f"{code} ({ecd['title']})" if ecd and ecd.get("title") else str(code)
        event_summary = (
            f"Event code {code_label} @ record {start_record}: "
            f"{len(anomalies)} anomalous metric{'s' if len(anomalies) != 1 else ''}. "
            f"{'; '.join(summary_parts)}."
        )

        event_id = new_id("evt", state["runId"])
        event_ids.append(event_id)
        event_payload: EventPayload = {
            "event_id": event_id,
            "run_id": state["runId"],
            "event_code": int(code),
            "application_id": application_id,
            "start_record": start_record,
            "end_record": int(end_record) if end_record is not None else None,
            "event_length": span,
            "severity": severity,
            "priority_score": priority,
            "anomalies": anomalies,
            "summary": event_summary,
            "diagnosis_prompt": (
                f"Event code {code_label} at record {start_record}"
                + (f" (span {span} records)" if span else "")
                + f": {len(anomalies)} anomalous metrics. "
                + "; ".join(
                    f"{a['metric']}: record={a['record']:.3f}, summary={a['summary']:.3f}, "
                    f"deviation={a['pct_delta']:+.0%}"
                    for a in top_anomalies[:6]
                )
                + "."
            ),
            "evidence_refs": [f"record:{application_id}:{start_record}"],
        }
        kg_updates[event_id] = {
            "node_id": event_id,
            "node_type": "Event",
            "payload": event_payload,
            "edges": [],
        }

    # Phase 2 + 3: build findings and wrap into KG nodes
    try:
        metric_to_app = await asyncio.to_thread(build_metric_to_app_map)
    except Exception as exc:
        logger.warning("Analyze: could not build metric-to-app map: %s", exc)
        metric_to_app = {}

    common_events = [
        {
            "event_id": evt_id,
            "event_code": kg_updates[evt_id]["payload"]["event_code"],
            "severity": kg_updates[evt_id]["payload"]["severity"],
            "anomalies": kg_updates[evt_id]["payload"]["anomalies"],
        }
        for evt_id in event_ids
    ]

    raw_findings = build_findings_by_app_type(
        common_events, metric_to_app, application_id, event_code_defs
    )

    finding_ids: list[str] = []
    event_to_findings: dict[str, list[str]] = defaultdict(list)

    for f in raw_findings:
        finding_id = new_id("find", state["runId"])
        finding_ids.append(finding_id)

        finding_payload: FindingPayload = {
            "finding_id": finding_id,
            "run_id": state["runId"],
            "application_id": f["application_id"],
            "application_type": f["application_type"],
            "application_name": f["application_name"],
            "title": f"{f['application_name']} anomaly findings",
            "severity": f["severity"],
            "event_refs": f["event_ids"],
            "metric_summaries": f["metric_summaries"],
            "event_code_counts": f["event_code_counts"],
            "diagnosis_prompt": f["diagnosis_prompt"],
        }
        kg_updates[finding_id] = {
            "node_id": finding_id,
            "node_type": "Finding",
            "payload": finding_payload,
            "edges": [],
        }

        for evt_id in f["event_ids"]:
            event_to_findings[evt_id].append(finding_id)

    # Set Event edges to their Finding(s)
    for evt_id, f_ids in event_to_findings.items():
        kg_updates[evt_id]["edges"] = [
            {"type": "EVENT_SUPPORTS_FINDING", "to": f_id} for f_id in f_ids
        ]

    logger.info(
        "Analyze: created %d event nodes and %d finding nodes",
        len(event_ids),
        len(finding_ids),
    )
    return {"eventIds": event_ids, "findingIds": finding_ids, "kg": kg_updates}
