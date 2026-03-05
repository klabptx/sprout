#!/usr/bin/env python3
"""Hit a running Stitch server and print event code counts."""

from __future__ import annotations

import os
import sys
from collections import Counter

import requests

STITCH_BASE = os.getenv("STITCH_LOCAL_BASE_URL", "http://localhost:8888").rstrip("/")
LIMIT = int(os.getenv("EVENT_LIMIT", "500"))


def main() -> int:
    url = f"{STITCH_BASE}/local/events?start=0&limit={LIMIT}"
    resp = requests.get(url, timeout=30)
    if not resp.ok:
        print(f"Error {resp.status_code}: {resp.text.strip()}")
        return 1

    payload = resp.json()
    events = (
        payload
        if isinstance(payload, list)
        else (
            payload.get("items") or payload.get("events") or payload.get("data") or []
        )
    )

    counts: Counter[str] = Counter()
    for event in events:
        code = event.get("event_code") or event.get("code") or event.get("eventCode")
        if code is not None:
            counts[str(code)] += 1

    if not counts:
        print("No events found.")
        return 0

    print(f"Total events: {sum(counts.values())}")
    print(f"Unique codes: {len(counts)}")
    print()
    for code, count in counts.most_common():
        print(f"  {code:>6s}  {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
