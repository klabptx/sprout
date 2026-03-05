#!/usr/bin/env python3
"""Probe all /local/ endpoints on stitch to find the 'task' field.

Hits every known endpoint and searches responses for 'task' or related
fields (implement type, application type, field_task, etc.).

Usage:
    STITCH_LOCAL_BASE_URL=http://localhost:8888 python scripts/dump_stitch_config.py
"""

from __future__ import annotations

import json
import sys

import requests

from sprout.kg.utils import _stitch_base


def probe(base: str, path: str, label: str | None = None) -> dict | list | None:
    """GET an endpoint, print the response, return parsed JSON or None."""
    url = f"{base}{path}"
    tag = label or path
    print(f"\n{'=' * 60}")
    print(f"  {tag}")
    print(f"  GET {path}")
    print(f"{'=' * 60}")
    try:
        resp = requests.get(url, timeout=10)
    except requests.exceptions.ConnectionError:
        print("  CONNECTION ERROR — is stitch running?")
        return None
    except requests.exceptions.Timeout:
        print("  TIMEOUT (10s)")
        return None

    if not resp.ok:
        print(f"  HTTP {resp.status_code}: {resp.text.strip()}")
        return None

    try:
        payload = resp.json()
    except ValueError:
        print(f"  (non-JSON) {resp.text[:500]}")
        return None

    print(json.dumps(payload, indent=2, default=str))
    return payload


def search_for_task(obj: object, path: str = "$") -> list[str]:
    """Recursively search a JSON object for keys/values related to 'task'."""
    hits: list[str] = []
    task_keys = {"task", "field_task", "fieldtask", "task_index", "taskindex"}
    task_values = {
        "plant",
        "harvest",
        "spray",
        "sidedress",
        "tillage",
        "planter",
        "combine",
        "sprayer",
        "plan",
    }

    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if kl in task_keys:
                hits.append(f"  FOUND key={k!r} value={v!r} at {path}.{k}")
            if isinstance(v, str) and v.lower() in task_values:
                hits.append(f"  MATCH value={v!r} at {path}.{k}")
            hits.extend(search_for_task(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hits.extend(search_for_task(item, f"{path}[{i}]"))
    return hits


def main() -> None:
    base = _stitch_base()
    print(f"Stitch base URL: {base}\n")

    all_hits: list[str] = []

    # --- 1. Config endpoints ---
    for path, label in [
        ("/local/config/equipment", "Config: Equipment"),
    ]:
        payload = probe(base, path, label)
        if payload:
            hits = search_for_task(payload)
            all_hits.extend(hits)

    # --- 2. Applications ---
    apps_payload = probe(base, "/local/applications?l=en-US", "Applications")
    app_ids = []
    if apps_payload:
        hits = search_for_task(apps_payload)
        all_hits.extend(hits)
        for app in apps_payload:
            app_id = app.get("application_id", "")
            if app_id:
                app_ids.append((app_id, app.get("name", "?")))

    # --- 3. Per-application metrics (first app only, to keep output short) ---
    for app_id, app_name in app_ids[:2]:
        payload = probe(
            base, f"/local/metrics/{app_id}", f"Metrics: {app_name} ({app_id})"
        )
        if payload:
            hits = search_for_task(payload)
            all_hits.extend(hits)

    # --- 4. Summary endpoints ---
    for path, label in [
        ("/local/summary", "Summary (base)"),
        ("/local/summary/pass?start=0&limit=1", "Summary: pass"),
        ("/local/summary/field", "Summary: field"),
    ]:
        payload = probe(base, path, label)
        if payload:
            hits = search_for_task(payload)
            all_hits.extend(hits)

    # Per-app summaries
    for app_id, app_name in app_ids[:2]:
        payload = probe(
            base, f"/local/summary/{app_id}", f"Summary: {app_name} ({app_id})"
        )
        if payload:
            hits = search_for_task(payload)
            all_hits.extend(hits)

    # --- 5. Other endpoints that might have state/task info ---
    for path, label in [
        ("/local/extents", "Extents"),
        ("/local/diagnostics", "Diagnostics"),
        ("/local/events?start=0&limit=5", "Events (first 5)"),
        ("/local/polygon", "Polygon"),
    ]:
        payload = probe(base, path, label)
        if payload:
            hits = search_for_task(payload)
            all_hits.extend(hits)

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print("  TASK-RELATED HITS ACROSS ALL ENDPOINTS")
    print(f"{'=' * 60}")
    if all_hits:
        for h in all_hits:
            print(h)
    else:
        print("  No direct 'task' fields found in any endpoint response.")
        print()
        print("  The C++ source shows task lives in StreamState (internal),")
        print("  not exposed via HTTP. Possible proxies:")
        print("    - /local/config/equipment → implements[].type (e.g. 'Planter')")
        print("    - /local/applications → [].type (e.g. 'harvest', 'seeding')")


if __name__ == "__main__":
    main()
