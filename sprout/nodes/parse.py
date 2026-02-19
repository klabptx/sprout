"""Parse node: fetch Tailor stream data and build the Run KG node."""
from __future__ import annotations

import logging

import requests

from sprout.config import get_settings
from sprout.exceptions import TailorAPIError
from sprout.graph_types import NodeEnvelope, RunPayload, Sample
from sprout.state import GraphState, KG, new_id

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


def _fetch_tailor_stream() -> dict:
    url = get_settings().tailor_stream_url_resolved()
    logger.info("Fetching Tailor stream: %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise TailorAPIError(
            f"Tailor stream fetch failed: {exc}",
            url=url,
            status_code=exc.response.status_code if exc.response is not None else None,
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise TailorAPIError(f"Tailor stream request error: {exc}", url=url) from exc
    logger.debug("Tailor stream fetched successfully")
    return resp.json()


async def parse(_: GraphState) -> dict:
    """Fetch Tailor stream data and build a Run node in the KG."""
    payload = _fetch_tailor_stream()
    current = payload.get("currentStream", {})
    overview = current.get("overview")
    summary_data = current.get("summaryData", []) or []
    diagnostics = current.get("diagnostics", []) or []
    event_details = current.get("diagnosticEventDetails", []) or []
    run_id = current.get("streamId") or (overview.get("streamId") if overview else None)
    run_id = run_id or new_id("run")
    source_file = f"{run_id}.2020"

    if not summary_data:
        logger.warning("No summary data returned from Tailor stream (run_id=%s)", run_id)

    samples: list[Sample] = []
    run_node = build_run_node(run_id, samples)

    kg: KG = {run_node["node_id"]: run_node}
    return {
        "samples": samples,
        "runId": run_id,
        "sourceFile": source_file,
        "tailorSummary": summary_data,
        "tailorDiagnostics": diagnostics,
        "tailorEventDetails": event_details,
        "tailorOverview": overview,
        "tailorContext": payload.get("contextStreams", []) or [],
        "kg": kg,
        "dataQualityFlag": False if summary_data else True,
    }
