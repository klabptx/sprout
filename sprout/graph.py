"""LangGraph pipeline: graph wiring, demo wrapper, and routing."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Awaitable, Callable, Protocol

from langgraph.graph import END, START, StateGraph

from sprout.nodes.analyze import analyze_event_records
from sprout.nodes.augment import augment
from sprout.nodes.parse import parse
from sprout.nodes.prioritize import prioritize
from sprout.nodes.synthesize import synthesize
from sprout.state import GraphState, default_state

logger = logging.getLogger(__name__)

# Re-export default_state so callers that do `from sprout.graph import default_state` work.
__all__ = ["build_graph", "default_state"]


# --------------------------------------------------------------------------- #
# Demo presentation layer
# --------------------------------------------------------------------------- #

DEMO_STAGE_INFO: dict[str, str] = {
    "parse": "Fetches Tailor stream data and builds the Run node.",
    "analyze_event_records": "Compares record-level metrics against summary for diagnostic events; generates findings for anomalous records.",
    "prioritize": "Creates Priority nodes and ranks findings.",
    "augment": "Adds Augmentation + Recommendation nodes.",
    "synthesize": "Creates Report node summarizing findings.",
}


class DemoController(Protocol):
    def on_stage(self, payload: dict) -> None: ...
    def wait_for_continue(self) -> None: ...


def _build_demo_payload(stage: str, state: GraphState, result: dict) -> dict:
    kg_updates = result.get("kg", {})
    nodes = list(kg_updates.values()) if isinstance(kg_updates, dict) else []
    node_counts = Counter(node.get("node_type") for node in nodes if "node_type" in node)
    edge_counts: Counter[str] = Counter()
    targets: set[str] = set()
    for node in nodes:
        for edge in node.get("edges", []):
            edge_counts[edge.get("type", "")] += 1
            if edge.get("to"):
                targets.add(edge["to"])

    lines = [
        f"Stage: {stage}",
        DEMO_STAGE_INFO.get(stage, "No stage summary available."),
    ]
    if node_counts:
        lines.append(f"KG nodes created: {', '.join(f'{k} x{v}' for k, v in node_counts.items())}")
    else:
        lines.append("KG nodes created: none")
    if edge_counts:
        lines.append(f"KG edges created: {', '.join(f'{k} x{v}' for k, v in edge_counts.items() if k)}")
    else:
        lines.append("KG edges created: none")
    sample_targets = sorted(list(targets))[:6]
    if sample_targets:
        suffix = " ..." if len(targets) > 6 else ""
        lines.append(f"KG node references: {', '.join(sample_targets)}{suffix}")

    data_preview: dict[str, object] = {}
    if stage == "parse":
        data_preview = {
            "run_id": result.get("runId"),
            "sample_count": len(result.get("samples", [])),
            "tailor_summary_count": len(result.get("tailorSummary", [])),
            "tailor_diagnostic_count": len(result.get("tailorDiagnostics", [])),
        }
    elif stage == "analyze_event_records":
        event_node = next(
            (n for n in (result.get("kg", {}) or {}).values() if n.get("node_type") == "Event"),
            None,
        )
        finding_node = next(
            (n for n in (result.get("kg", {}) or {}).values() if n.get("node_type") == "Finding"),
            None,
        )
        data_preview = {
            "event_count": len(result.get("eventIds", [])),
            "finding_count": len(result.get("findingIds", [])),
            "event_example": event_node["payload"] if event_node else None,
            "finding_example": finding_node["payload"] if finding_node else None,
        }
    elif stage == "prioritize":
        top_finding = state["kg"].get(result.get("topFindingId") or "")
        data_preview = {
            "top_severity": result.get("topSeverity"),
            "top_finding_id": result.get("topFindingId"),
            "priority_count": len(result.get("priorityIds", [])),
            "top_finding": top_finding["payload"] if top_finding else None,
        }
    elif stage == "augment":
        rec_texts = [
            state["kg"][rec_id]["payload"]["text"]
            for rec_id in result.get("recommendationIds", [])
            if rec_id in state["kg"]
        ]
        data_preview = {
            "augmentation_ids": result.get("augmentationIds", []),
            "recommendation_ids": result.get("recommendationIds", []),
            "recommendation_texts": rec_texts,
        }
    elif stage == "synthesize":
        report_id = result.get("reportId")
        report_node = result.get("kg", {}).get(report_id) if report_id else None
        data_preview = {
            "report_id": report_id,
            "summary": report_node["payload"]["summary"] if report_node else None,
            "llm_error": result.get("llmError") or state.get("llmError"),
        }

    return {
        "type": "stage",
        "stage": stage,
        "summary_lines": lines,
        "node_counts": dict(node_counts),
        "edge_counts": dict(edge_counts),
        "target_sample": sample_targets,
        "target_count": len(targets),
        "data_preview": data_preview,
    }


def _await_demo_continue() -> None:
    input("Press Enter to advance to the next stage...")


def with_demo(
    stage: str, fn: Callable[[GraphState], Awaitable[dict]]
) -> Callable[[GraphState], Awaitable[dict]]:
    async def wrapper(state: GraphState) -> dict:
        result = await fn(state)
        logger.info("Stage complete: %s", stage)
        controller = state.get("demoController")
        if controller is not None:
            controller.on_stage(_build_demo_payload(stage, state, result))
            controller.wait_for_continue()
        elif state.get("demo"):
            print("")
            for line in _build_demo_payload(stage, state, result)["summary_lines"]:
                print(line)
            _await_demo_continue()
        return result

    return wrapper


# --------------------------------------------------------------------------- #
# Graph construction
# --------------------------------------------------------------------------- #


def route_after_prioritize(state: GraphState) -> str:
    return "augment" if state["topSeverity"] >= state["severityThreshold"] else "synthesize"


def build_graph() -> Callable[[GraphState], GraphState]:
    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("parse", with_demo("parse", parse))
    builder.add_node("analyze_event_records", with_demo("analyze_event_records", analyze_event_records))
    builder.add_node("prioritize", with_demo("prioritize", prioritize))
    builder.add_node("augment", with_demo("augment", augment))
    builder.add_node("synthesize", with_demo("synthesize", synthesize))

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "analyze_event_records")
    builder.add_edge("analyze_event_records", "prioritize")
    builder.add_conditional_edges("prioritize", route_after_prioritize)
    builder.add_edge("augment", "synthesize")
    builder.add_edge("synthesize", END)

    return builder.compile()
