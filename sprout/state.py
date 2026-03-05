"""GraphState TypedDict, reducers, ID counter, and default_state()."""

from __future__ import annotations

import uuid
from typing import Annotated, Protocol, TypedDict

from sprout.config import get_settings
from sprout.graph_types import Edge, NodeEnvelope, Sample

KG = dict[str, NodeEnvelope]


def concat(left: list[str], right: list[str]) -> list[str]:
    return left + right


def merge_dict(
    left: dict[str, NodeEnvelope], right: dict[str, NodeEnvelope]
) -> dict[str, NodeEnvelope]:
    merged = dict(left)
    merged.update(right)
    return merged


class GraphState(TypedDict):
    samples: list[Sample]
    runId: str
    summaryData: list[dict]
    diagnostics: list[dict]
    eventDetails: list[dict]
    kg: Annotated[KG, merge_dict]
    segmentIds: Annotated[list[str], concat]
    featureIds: Annotated[list[str], concat]
    eventIds: Annotated[list[str], concat]
    findingIds: Annotated[list[str], concat]
    priorityIds: Annotated[list[str], concat]
    augmentationIds: Annotated[list[str], concat]
    recommendationIds: Annotated[list[str], concat]
    reportId: str
    sourceFile: str
    topSeverity: float
    topFindingId: str | None
    dataQualityFlag: bool
    excludeEventCodes: list[int]
    excludeMetrics: list[str]
    severityThreshold: float
    demo: bool
    demoController: "DemoController | None"
    synthesizeMaxFindings: int
    llmBackend: str
    llmModel: str
    llmError: str | None


class DemoController(Protocol):
    def on_stage(self, payload: dict) -> None: ...
    def wait_for_continue(self) -> None: ...


# Per-run monotonic ID counter — keyed by run_id so concurrent or
# sequential runs in the same process never collide.
_id_counters: dict[str, int] = {}


def make_run_id() -> str:
    """Generate a unique run ID using a short UUID prefix."""
    return f"run_{uuid.uuid4().hex[:8]}"


def new_id(prefix: str, run_id: str) -> str:
    """Return a namespaced sequential ID like ``evt_a1b2c3d4_0001``."""
    suffix = run_id.removeprefix("run_")
    _id_counters[run_id] = _id_counters.get(run_id, 0) + 1
    return f"{prefix}_{suffix}_{_id_counters[run_id]:04d}"


def dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[str] = set()
    unique: list[Edge] = []
    for edge in edges:
        key = f"{edge['type']}:{edge['to']}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def with_edges(node: NodeEnvelope, edges: list[Edge]) -> NodeEnvelope:
    node = dict(node)
    node["edges"] = dedupe_edges(node["edges"] + edges)
    return node  # type: ignore[return-value]


def default_state() -> GraphState:
    s = get_settings()
    return {
        "samples": [],
        "runId": "",
        "summaryData": [],
        "diagnostics": [],
        "eventDetails": [],
        "kg": {},
        "segmentIds": [],
        "featureIds": [],
        "eventIds": [],
        "findingIds": [],
        "priorityIds": [],
        "augmentationIds": [],
        "recommendationIds": [],
        "reportId": "",
        "sourceFile": "",
        "topSeverity": 0.0,
        "topFindingId": None,
        "dataQualityFlag": False,
        "excludeEventCodes": s.excluded_event_codes(),
        "excludeMetrics": s.excluded_metrics(),
        "severityThreshold": s.severity_threshold,
        "demo": False,
        "demoController": None,
        "synthesizeMaxFindings": -1,
        "llmBackend": s.llm_backend,
        "llmModel": "",
        "llmError": None,
    }
