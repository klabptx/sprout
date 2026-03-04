"""TypedDict definitions for all knowledge-graph node payloads.

Copied from src/demo/graph_types.py — this is the canonical location.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

NodeType = Literal[
    "Run",
    "Segment",
    "WindowFeature",
    "Event",
    "Finding",
    "Priority",
    "Augmentation",
    "Recommendation",
    "Report",
]

EdgeType = Literal[
    "RUN_HAS_SEGMENT",
    "SEGMENT_HAS_FEATURE",
    "FEATURE_SUPPORTS_EVENT",
    "EVENT_SUPPORTS_FINDING",
    "FINDING_HAS_PRIORITY",
    "FINDING_HAS_RECOMMENDATION",
    "REPORT_SUMMARIZES",
    "NODE_HAS_TS_REF",
    "NODE_HAS_SPATIAL_REF",
]


class Edge(TypedDict):
    # Relationship from this node to another node in the KG.
    type: EdgeType
    to: str


class NodeEnvelope(TypedDict):
    # Standard container for KG nodes with metadata, payload, and edges.
    node_id: str
    node_type: NodeType
    payload: Any
    edges: list[Edge]


class Sample(TypedDict):
    # One telemetry sample from the mocked run time-series.
    t: float
    speed_mps: float
    downforce_n: float
    singulation_pct: float
    vacuum_kpa: float
    lat: float | None
    lon: float | None


class RunPayload(TypedDict):
    # Run metadata for a single pass.
    run_id: str
    sample_rate_hz: int
    start_time_s: float
    end_time_s: float
    spatial_ref: None


class SegmentStats(TypedDict):
    # Aggregated stats computed per segment window.
    downforce_p95: float
    downforce_std: float
    singulation_p05: float
    singulation_std: float


class SegmentPayload(TypedDict):
    # Segment metadata describing a time window and its stats.
    segment_id: str
    run_id: str
    t0_s: float
    t1_s: float
    geom: None
    crs_epsg: int
    stats: SegmentStats


class WindowFeaturePayload(TypedDict):
    # Single feature derived from a segment window.
    feature_id: str
    segment_id: str
    name: Literal["downforce_p95", "downforce_std", "singulation_p05", "singulation_std"]
    value: float
    units: str


class EventPayload(TypedDict):
    # An anomalous event-code occurrence with record-level metric comparisons.
    event_id: str
    run_id: str
    event_code: int
    application_id: str
    start_record: int
    end_record: int | None
    event_length: int | None
    severity: float
    priority_score: float
    anomalies: list[dict[str, Any]]
    summary: str
    diagnosis_prompt: str
    evidence_refs: list[str]
    still_active: bool


class FindingPayload(TypedDict):
    # Finding aggregating anomalous metrics for a single application type.
    finding_id: str
    run_id: str
    application_id: str
    application_type: str
    application_name: str
    title: str
    severity: float
    event_refs: list[str]
    metric_summaries: list[dict[str, Any]]
    event_code_counts: dict[str, int]
    diagnosis_prompt: str


class PriorityPayload(TypedDict):
    # Priority score attached to a finding.
    priority_id: str
    finding_id: str
    score: float
    rationale: str


class AugmentationPayload(TypedDict):
    # RAG augmentation with snippets used to generate recommendations.
    augmentation_id: str
    query: str
    snippets: list[str]


class RecommendationPayload(TypedDict):
    # Recommended action derived from an augmentation snippet.
    recommendation_id: str
    finding_id: str
    text: str
    source_refs: list[str]


class ReportPayload(TypedDict):
    # Final report summarizing findings for the run.
    report_id: str
    run_id: str
    summary: str
    structured_summary: str
    operational_summary: str
    severity: float
    confidence: float
    finding_refs: list[str]
    priority_refs: list[str]
    recommendation_refs: list[str]
