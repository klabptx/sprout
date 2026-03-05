"""AWS Lambda handler for the Sprout pipeline.

Designed for use with a Lambda Function URL.  Accepts ``org_code`` and
``stream_id`` via query-string parameters (GET) or a JSON body (POST),
constructs the Stitch API base URL from a template, runs the full
LangGraph pipeline, and returns the report as plain text.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

from sprout.config import reset_settings
from sprout.kg.utils import reset_stitch_session
from sprout.logging_config import configure_logging

from sprout.exceptions import SproutError
from sprout.graph import build_graph
from sprout.state import default_state

logger = logging.getLogger(__name__)


def handler(event: dict, context: object) -> dict:
    """Lambda Function URL entry point."""
    params = _extract_params(event)
    org_code = params.get("org_code")
    stream_id = params.get("stream_id")

    if not org_code or not stream_id:
        return _response(400, "Missing required parameters: org_code and stream_id")

    # Build Stitch base URL from template env var.
    template = os.environ.get("STITCH_URL_TEMPLATE", "{org_code}/{stream_id}")
    stitch_url = template.format(org_code=org_code, stream_id=stream_id)
    os.environ["STITCH_LOCAL_BASE_URL"] = stitch_url

    reset_settings()
    reset_stitch_session()

    configure_logging(verbose=False)

    # Run the pipeline.

    graph = build_graph()
    try:
        result = asyncio.run(graph.ainvoke(default_state()))
    except SproutError as exc:
        logger.error("Pipeline error: %s", exc)
        return _response(500, f"Pipeline error: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error")
        return _response(500, f"Internal error: {type(exc).__name__}")

    # Extract report text (mirrors cli.py logic).
    report_id = result.get("reportId")
    report_node = result.get("kg", {}).get(report_id) if report_id else None
    if report_node:
        report_text = report_node["payload"]["summary"]
    else:
        report_text = "No report produced."

    return _response(200, report_text)


def _extract_params(event: dict) -> dict:
    """Extract parameters from a Function URL event (v2 payload format)."""
    qs = event.get("queryStringParameters") or {}
    if qs.get("org_code") and qs.get("stream_id"):
        return qs

    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if body:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            pass

    return qs


def _response(status_code: int, body: str) -> dict:
    """Build a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/plain; charset=utf-8"},
        "body": body,
    }
