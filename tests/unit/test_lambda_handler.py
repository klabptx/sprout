"""Unit tests for the AWS Lambda handler."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

# ------------------------------------------------------------------ #
# Parameter extraction
# ------------------------------------------------------------------ #


def test_missing_params_returns_400():
    from sprout.lambda_handler import handler

    result = handler({"queryStringParameters": {}}, None)
    assert result["statusCode"] == 400
    assert "org_code" in result["body"]


def test_missing_org_code_returns_400():
    from sprout.lambda_handler import handler

    result = handler({"queryStringParameters": {"stream_id": "s1"}}, None)
    assert result["statusCode"] == 400


def test_missing_stream_id_returns_400():
    from sprout.lambda_handler import handler

    result = handler({"queryStringParameters": {"org_code": "ACME"}}, None)
    assert result["statusCode"] == 400


# ------------------------------------------------------------------ #
# Successful invocation (mocked pipeline)
# ------------------------------------------------------------------ #

_MOCK_RESULT = {
    "report_id": "rpt_0001",
    "kg": {
        "rpt_0001": {
            "node_type": "Report",
            "payload": {"summary": "Test report text"},
            "edges": [],
        }
    },
}


@patch("sprout.lambda_handler.build_graph")
def test_handler_returns_report(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value=_MOCK_RESULT)
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    result = handler(
        {"queryStringParameters": {"org_code": "ACME", "stream_id": "s1"}},
        None,
    )
    assert result["statusCode"] == 200
    assert result["body"] == "Test report text"


@patch("sprout.lambda_handler.build_graph")
def test_handler_sets_stitch_url(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value=_MOCK_RESULT)
    mock_build.return_value = mock_compiled

    import os

    from sprout.lambda_handler import handler

    handler(
        {"queryStringParameters": {"org_code": "ORG1", "stream_id": "STREAM2"}},
        None,
    )
    assert os.environ["STITCH_LOCAL_BASE_URL"] == "https://stitch.test/ORG1/STREAM2"


# ------------------------------------------------------------------ #
# POST body extraction
# ------------------------------------------------------------------ #


@patch("sprout.lambda_handler.build_graph")
def test_handler_accepts_json_body(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value=_MOCK_RESULT)
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    result = handler(
        {
            "body": json.dumps({"org_code": "ACME", "stream_id": "s1"}),
            "isBase64Encoded": False,
        },
        None,
    )
    assert result["statusCode"] == 200
    assert result["body"] == "Test report text"


@patch("sprout.lambda_handler.build_graph")
def test_handler_accepts_base64_body(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value=_MOCK_RESULT)
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    raw = json.dumps({"org_code": "ACME", "stream_id": "s1"})
    encoded = base64.b64encode(raw.encode()).decode()

    result = handler({"body": encoded, "isBase64Encoded": True}, None)
    assert result["statusCode"] == 200


# ------------------------------------------------------------------ #
# Error handling
# ------------------------------------------------------------------ #


@patch("sprout.lambda_handler.build_graph")
def test_pipeline_error_returns_500(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    from sprout.exceptions import SproutError

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(side_effect=SproutError("boom"))
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    result = handler(
        {"queryStringParameters": {"org_code": "ACME", "stream_id": "s1"}},
        None,
    )
    assert result["statusCode"] == 500
    assert "boom" in result["body"]


@patch("sprout.lambda_handler.build_graph")
def test_unexpected_error_returns_500(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(side_effect=RuntimeError("unexpected"))
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    result = handler(
        {"queryStringParameters": {"org_code": "ACME", "stream_id": "s1"}},
        None,
    )
    assert result["statusCode"] == 500
    assert "RuntimeError" in result["body"]


# ------------------------------------------------------------------ #
# No report produced
# ------------------------------------------------------------------ #


@patch("sprout.lambda_handler.build_graph")
def test_handler_no_report(mock_build, monkeypatch):
    monkeypatch.setenv(
        "STITCH_URL_TEMPLATE", "https://stitch.test/{org_code}/{stream_id}"
    )

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value={"report_id": "", "kg": {}})
    mock_build.return_value = mock_compiled

    from sprout.lambda_handler import handler

    result = handler(
        {"queryStringParameters": {"org_code": "ACME", "stream_id": "s1"}},
        None,
    )
    assert result["statusCode"] == 200
    assert result["body"] == "No report produced."
