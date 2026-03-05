#!/usr/bin/env python3
"""Run the LangGraph demo pipeline once and emit a JSON record to stdout.

Expected env vars:
    STITCH_LOCAL_BASE_URL – Stitch fs endpoint (default http://localhost:8888)

Optional:
    LLM_BACKEND         – openai | local | vllm | lambda | auto (default openai)
    SEVERITY_THRESHOLD   – float (default 0.25)
"""

from __future__ import annotations

import asyncio
import json
import sys

from sprout.graph import build_graph
from sprout.state import default_state


def _extract_output(result: dict) -> dict:
    """Distil the full graph state into a JSON-serialisable record."""
    report_id = result.get("report_id")
    kg = result.get("kg", {})
    report_node = kg.get(report_id) if report_id else None

    findings = []
    for fid in result.get("finding_ids", []):
        fnode = kg.get(fid)
        if fnode:
            findings.append(fnode["payload"])

    priorities = []
    for pid in result.get("priority_ids", []):
        pnode = kg.get(pid)
        if pnode:
            priorities.append(pnode["payload"])

    return {
        "source_file": result.get("source_file", ""),
        "run_id": result.get("run_id", ""),
        "report": report_node["payload"] if report_node else None,
        "findings": findings,
        "priorities": priorities,
        "llm_model": result.get("llm_model", ""),
        "llm_error": result.get("llm_error"),
        "counts": {
            "events": len(result.get("event_ids", [])),
            "findings": len(result.get("finding_ids", [])),
            "priorities": len(result.get("priority_ids", [])),
            "recommendations": len(result.get("recommendation_ids", [])),
        },
    }


def main() -> int:
    graph = build_graph()
    result = asyncio.run(
        graph.ainvoke(
            {
                **default_state(),
                # default_state() already reads severity_threshold and llm_backend from settings;
                # override here only if you want to deviate from the configured defaults.
                # "exclude_event_codes": [100017], # noisy event codes
            }
        )
    )

    output = _extract_output(result)

    if output.get("llm_error"):
        print(f"WARNING: LLM error: {output['llm_error']}", file=sys.stderr)
    if not output.get("llm_model") or output.get("llm_model") == "none":
        print(
            "WARNING: No LLM summary produced — report contains raw fallback text.",
            file=sys.stderr,
        )

    json.dump(output, sys.stdout, default=str)
    print()  # trailing newline
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
