"""Parse node: fetch application data and events from Stitch, build the Run KG node."""

from __future__ import annotations

import logging
from typing import Any

from sprout.config import get_settings
from sprout.exceptions import StitchAPIError
from sprout.graph_types import NodeEnvelope, RunPayload, Sample
from sprout.kg.utils import load_applications, load_events, load_summary_metrics
from sprout.state import GraphState, KG, make_run_id

logger = logging.getLogger(__name__)


def build_run_node(run_id: str, samples: list[Sample]) -> NodeEnvelope:
    payload: RunPayload = {
        "run_id": run_id,
        "sample_rate_hz": get_settings().sample_rate_hz,
        "start_time_s": samples[0]["t"] if samples else 0,
        "end_time_s": samples[-1]["t"] if samples else 0,
        "spatial_ref": None,
    }
    return {
        "node_id": run_id,
        "node_type": "Run",
        "payload": payload,
        "edges": [],
    }


def _select_default_app(apps: list[dict]) -> dict[str, Any]:
    """Pick the default (or first) application from the Stitch applications list."""
    if not apps:
        return {"application_id": "local-app", "name": "local", "type": "local"}
    app = next((a for a in apps if a.get("default") is True), apps[0])
    app_type = app.get("type", {})
    type_key = (
        app_type.get("key", "unknown") if isinstance(app_type, dict) else "unknown"
    )
    return {
        "application_id": app.get("application_id", "local-app"),
        "name": app.get("name", "local"),
        "type": type_key,
    }


def _build_diagnostics(events: list[dict]) -> list[dict]:
    """Aggregate event codes into a diagnostics summary."""
    codes: dict[str, dict[str, Any]] = {}
    total = 0
    for event in events:
        code = event.get("event_code") or event.get("code") or event.get("eventCode")
        if code is None:
            continue
        code_str = str(code)
        codes.setdefault(code_str, {"count": 0, "isActive": False})
        codes[code_str]["count"] += 1
        total += 1
    if total == 0:
        return []
    return [{"rowType": "EventCodeCount", "total": total, "codes": codes}]


def _normalize_events(events: list[dict]) -> list[dict]:
    """Normalize raw Stitch events into the event detail format used by analyze."""
    details: list[dict] = []
    for event in events:
        code = event.get("event_code") or event.get("code") or event.get("eventCode")
        start_record = event.get("start_record") or event.get("startRecord")
        if code is None or start_record is None:
            continue
        details.append(
            {
                "eventCode": int(code),
                "start_record": int(start_record),
                "end_record": event.get("end_record") or event.get("endRecord"),
                "start_time": event.get("start_time") or event.get("startTime"),
                "end_time": event.get("end_time") or event.get("endTime"),
                "location": event.get("location"),
                "modules": event.get("modules"),
            }
        )
    return details


async def parse(_: GraphState) -> dict:
    """Fetch application data and events from Stitch; build a Run node in the KG."""
    logger.info("Fetching data from Stitch")

    apps = load_applications()
    app = _select_default_app(apps)
    app_id = app["application_id"]

    summary_metrics: dict[str, float] = {}
    try:
        summary_metrics = load_summary_metrics(app_id)
    except StitchAPIError as exc:
        logger.warning("Failed to load summary metrics for %s: %s", app_id, exc)

    summary_row: dict[str, Any] = {
        "applicationId": app_id,
        "name": app["name"],
        "type": app["type"],
        **summary_metrics,
    }

    raw_events: list[dict] = []
    try:
        raw_events = load_events()
    except StitchAPIError as exc:
        logger.warning("Failed to load events: %s", exc)

    event_details = _normalize_events(raw_events)
    diagnostics = _build_diagnostics(raw_events)

    run_id = make_run_id()
    source_file = f"{run_id}.2020"

    if not summary_metrics:
        logger.warning("No summary metrics returned from Stitch (run_id=%s)", run_id)

    samples: list[Sample] = []
    run_node = build_run_node(run_id, samples)

    kg: KG = {run_node["node_id"]: run_node}
    return {
        "samples": samples,
        "runId": run_id,
        "sourceFile": source_file,
        "summaryData": [summary_row],
        "diagnostics": diagnostics,
        "eventDetails": event_details,
        "kg": kg,
        "dataQualityFlag": not bool(summary_metrics),
    }
