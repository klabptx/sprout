#!/usr/bin/env python3
"""Dump raw metric responses from stitch for each application.

Usage:
    STITCH_LOCAL_BASE_URL=http://localhost:8888 python scripts/dump_stitch_metrics.py
    STITCH_LOCAL_BASE_URL=http://localhost:8888 python scripts/dump_stitch_metrics.py --summary
"""

from __future__ import annotations

import json
import sys

import requests

from sprout.kg.utils import _stitch_base, get_json


def dump_metrics() -> None:
    base = _stitch_base()
    apps = get_json(f"{base}/local/applications?l=en-US")

    for app in apps:
        app_id = app.get("application_id", "")
        app_name = app.get("name", "")
        app_type = app.get("type", {})
        print(f"\n{'=' * 60}")
        print(f"Application: {app_name}  (id={app_id})")
        print(f"Type: {json.dumps(app_type)}")
        print(f"{'=' * 60}")

        try:
            payload = get_json(f"{base}/local/metrics/{app_id}")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        for m in payload.get("metrics", []):
            key = m.get("key", "?")
            name = m.get("name", "?")
            value = m.get("value")
            print(f"\n  key={key!r}  name={name!r}")
            print(f"  value={json.dumps(value, indent=4, default=str)}")


def dump_summary() -> None:
    base = _stitch_base()

    # Try several summary endpoint variants to see which ones stitch supports
    endpoints = [
        "/local/summary",
        "/local/summary/pass?start=0&limit=1",
        "/local/summary/field",
    ]

    # Also try per-application summary endpoints
    try:
        apps = get_json(f"{base}/local/applications?l=en-US")
        for app in apps:
            app_id = app.get("application_id", "")
            endpoints.append(f"/local/summary/{app_id}")
    except Exception:
        pass

    for ep in endpoints:
        print(f"\n{'=' * 60}", flush=True)
        print(f"GET {ep}", flush=True)
        print(f"{'=' * 60}", flush=True)
        try:
            resp = requests.get(f"{base}{ep}", timeout=10)
            if not resp.ok:
                print(f"  ERROR: {resp.status_code} {resp.text.strip()}", flush=True)
                continue
            payload = resp.json()
            print(json.dumps(payload, indent=2, default=str), flush=True)
        except requests.exceptions.Timeout:
            print("  TIMEOUT (10s)", flush=True)
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)


def main() -> None:
    if "--summary" in sys.argv:
        dump_summary()
    else:
        dump_metrics()


if __name__ == "__main__":
    main()
