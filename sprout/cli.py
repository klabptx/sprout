"""CLI entry point for the Sprout pipeline."""
from __future__ import annotations

import argparse
import asyncio
import sys

from sprout.exceptions import SproutError
from sprout.graph import build_graph
from sprout.logging_config import configure_logging
from sprout.state import default_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Sprout agronomic analysis pipeline.")
    parser.add_argument("--demo", action="store_true", help="Pause between DAG stages.")
    parser.add_argument(
        "--llm-backend",
        choices=("auto", "local", "vllm", "lambda", "openai"),
        default=None,
        help="LLM backend to use for synthesis (overrides LLM_BACKEND env var).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(verbose=args.verbose)

    state_overrides: dict = {"demo": args.demo}
    if args.llm_backend:
        state_overrides["llmBackend"] = args.llm_backend

    graph = build_graph()
    try:
        result = asyncio.run(
            graph.ainvoke({**default_state(), **state_overrides})
        )
    except SproutError as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        sys.exit(1)

    report_id = result.get("reportId")
    report_node = result.get("kg", {}).get(report_id) if report_id else None

    source_file = result.get("sourceFile", "") or result.get("runId", "")
    print(f"=== Sprout Report: {source_file} ===" if source_file else "=== Sprout Report ===")
    if report_node:
        print(report_node["payload"]["summary"])
    else:
        print("No report produced.")

    print()
    print("Artifacts created:")
    print(f"- Run: {result.get('runId', 'n/a')}")
    print(f"- Events: {len(result.get('eventIds', []))}")
    print(f"- Findings: {len(result.get('findingIds', []))}")
    print(f"- Priorities: {len(result.get('priorityIds', []))}")
    print(f"- Recommendations: {len(result.get('recommendationIds', []))}")
    print(f"- Report: {result.get('reportId', 'n/a')}")
    print(f"- Model: {result.get('llmModel', 'n/a')}")


if __name__ == "__main__":
    main()
