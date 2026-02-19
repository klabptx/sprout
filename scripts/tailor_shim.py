#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import requests


STITCH_BASE = os.getenv("STITCH_LOCAL_BASE_URL", "http://localhost:8888").rstrip("/")
DROP_NULL_METRICS = os.getenv("TAILOR_SHIM_DROP_NULL_METRICS", "true").lower() in (
    "1",
    "true",
    "yes",
)


def _get_json(path: str, retries: int = 5) -> object:
    url = f"{STITCH_BASE}{path}"
    last_error = None
    for _ in range(retries):
        try:
            resp = requests.get(url, timeout=30)
        except Exception as exc:
            last_error = str(exc)
            continue
        if resp.ok:
            return resp.json()
        body = resp.text.strip()
        last_error = f"{resp.status_code} for {url}: {body}"
    raise RuntimeError(last_error or f"Request failed for {url}")


def _infer_app_type(app: dict) -> str:
    app_type = app.get("type") or {}
    if isinstance(app_type, dict):
        key = str(app_type.get("key") or "").lower()
        if key:
            return key
    name = str(app.get("name") or "").lower()
    key = str(app.get("key") or "").lower()
    joined = f"{name} {key}"
    if "seed" in joined or "plant" in joined:
        return "seeding"
    if "granular" in joined:
        return "granular"
    if "liquid" in joined or "spray" in joined:
        return "liquid"
    if "force" in joined or "downforce" in joined:
        return "force"
    if "pressure" in joined:
        return "pressure"
    if "harvest" in joined or "yield" in joined:
        return "harvest"
    return "local"


def _select_application(apps: list[dict]) -> dict:
    if not apps:
        return {"application_id": "local-app", "name": "local", "type": "local"}
    app = next((a for a in apps if a.get("default") is True), apps[0])
    return {
        "application_id": app.get("application_id") or "local-app",
        "name": app.get("name") or "local",
        "type": _infer_app_type(app),
    }


def _normalize_list(value: object) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "events", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


def _build_diagnostics(events: list[dict], org: str, stream_id: str) -> list[dict]:
    codes: dict[str, dict] = {}
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
    return [
        {
            "rowType": "EventCodeCount",
            "orgCode": org,
            "streamId": stream_id,
            "total": total,
            "codes": codes,
        }
    ]


def _build_event_details(events: list[dict], org: str, stream_id: str) -> list[dict]:
    details: list[dict] = []
    for event in events:
        code = event.get("event_code") or event.get("code") or event.get("eventCode")
        start_record = event.get("start_record") or event.get("startRecord")
        if code is None or start_record is None:
            continue
        details.append(
            {
                "rowType": "EventCode",
                "orgCode": org,
                "streamId": stream_id,
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


def _build_response(org: str, stream_id: str) -> dict:
    apps = _get_json("/local/applications")
    app_list = _normalize_list(apps)
    app = _select_application(app_list)

    summary_error = None
    summary: object = {}
    try:
        summary = _get_json("/local/summary")
    except Exception as exc:
        summary_error = str(exc)
        try:
            passes = _get_json("/local/summary/pass?start=0&limit=1")
            summary = _normalize_list(passes)[0] if _normalize_list(passes) else {}
        except Exception as exc2:
            summary_error = f"{summary_error}; {exc2}"
            summary = {}

    if not summary:
        try:
            summary = _get_json(f"/local/metrics/{app['application_id']}")
            summary_error = None
        except Exception as exc:
            summary_error = f"{summary_error}; metrics fallback: {exc}" if summary_error else str(exc)
            summary = {}

    if isinstance(summary, dict) and "metrics" in summary and isinstance(summary["metrics"], list):
        metrics_map: dict[str, float] = {}
        for metric in summary["metrics"]:
            key = metric.get("key") or metric.get("name")
            if not key:
                continue
            value = metric.get("value", {})
            if isinstance(value, dict) and "implement_average" in value:
                if value["implement_average"] is None and DROP_NULL_METRICS:
                    continue
                metrics_map[f"metrics.{key}"] = (
                    None if value["implement_average"] is None else float(value["implement_average"])
                )
        summary = {**summary, **metrics_map}
    try:
        events = _get_json("/local/events?start=0&limit=200")
    except Exception as exc:
        print(f"Tailor shim warning: failed to load events: {exc}")
        events = []
    summary_row = {
        **(summary if isinstance(summary, dict) else {"raw": summary}),
        "orgCode": org,
        "streamId": stream_id,
        "applicationId": app["application_id"],
        "name": app["name"],
        "type": app["type"],
    }
    if summary_error:
        summary_row["_summary_error"] = summary_error

    return {
        "currentStream": {
            "streamId": stream_id,
            "summaryData": [summary_row],
            "overview": {"streamId": stream_id, "orgCode": org},
            "weather": [],
            "hybridYield": [],
            "diagnostics": _build_diagnostics(_normalize_list(events), org, stream_id),
            "diagnosticEventDetails": _build_event_details(
                _normalize_list(events), org, stream_id
            ),
        },
        "contextStreams": [],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 4 and parts[0] == "tailor" and parts[2] == "streams":
            org = parts[1]
            stream_id = parts[3]
            try:
                payload = _build_response(org, stream_id)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))
                return
            except Exception as exc:
                print(f"Tailor shim error: {exc}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()


def main() -> None:
    host = os.getenv("TAILOR_SHIM_HOST", "0.0.0.0")
    port = int(os.getenv("TAILOR_SHIM_PORT", "9000"))
    server = HTTPServer((host, port), Handler)
    print(f"Tailor shim listening on http://{host}:{port}")
    print(f"Using Stitch fs at {STITCH_BASE}")
    server.serve_forever()


if __name__ == "__main__":
    main()
