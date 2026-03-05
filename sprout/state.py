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
    run_id: str
    summary_data: list[dict]
    diagnostics: list[dict]
    event_details: list[dict]
    kg: Annotated[KG, merge_dict]
    segment_ids: Annotated[list[str], concat]
    feature_ids: Annotated[list[str], concat]
    event_ids: Annotated[list[str], concat]
    finding_ids: Annotated[list[str], concat]
    priority_ids: Annotated[list[str], concat]
    augmentation_ids: Annotated[list[str], concat]
    recommendation_ids: Annotated[list[str], concat]
    report_id: str
    source_file: str
    top_severity: float
    top_finding_id: str | None
    data_quality_flag: bool
    exclude_event_codes: list[int]
    exclude_metrics: list[str]
    severity_threshold: float
    demo: bool
    demo_controller: DemoController | None
    synthesize_max_findings: int
    llm_backend: str
    llm_model: str
    llm_error: str | None


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
        "run_id": "",
        "summary_data": [],
        "diagnostics": [],
        "event_details": [],
        "kg": {},
        "segment_ids": [],
        "feature_ids": [],
        "event_ids": [],
        "finding_ids": [],
        "priority_ids": [],
        "augmentation_ids": [],
        "recommendation_ids": [],
        "report_id": "",
        "source_file": "",
        "top_severity": 0.0,
        "top_finding_id": None,
        "data_quality_flag": False,
        "exclude_event_codes": s.excluded_event_codes(),
        "exclude_metrics": s.excluded_metrics(),
        "severity_threshold": s.severity_threshold,
        "demo": False,
        "demo_controller": None,
        "synthesize_max_findings": -1,
        "llm_backend": s.llm_backend,
        "llm_model": "",
        "llm_error": None,
    }
