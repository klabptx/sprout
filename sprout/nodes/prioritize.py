"""Prioritize node: score findings and attach Priority KG nodes."""

from __future__ import annotations

import logging

from sprout.graph_types import NodeEnvelope, PriorityPayload
from sprout.state import KG, GraphState, new_id, with_edges

logger = logging.getLogger(__name__)


async def prioritize(state: GraphState) -> dict:
    """Score findings and attach Priority nodes; pick top severity."""
    priority_ids: list[str] = []
    top_severity = 0.0
    top_finding_id: str | None = None
    kg_updates: KG = {}

    for finding_id in state["finding_ids"]:
        finding_node = state["kg"].get(finding_id)
        if not finding_node:
            continue
        score = round(float(finding_node["payload"]["severity"]), 2)
        priority_id = new_id("prio", state["run_id"])
        priority_payload: PriorityPayload = {
            "priority_id": priority_id,
            "finding_id": finding_id,
            "score": score,
            "rationale": f"Severity {score} derived from prevalence and magnitude.",
        }
        priority_node: NodeEnvelope = {
            "node_id": priority_id,
            "node_type": "Priority",
            "payload": priority_payload,
            "edges": [],
        }
        priority_ids.append(priority_id)
        kg_updates[priority_id] = priority_node
        kg_updates[finding_id] = with_edges(
            finding_node,
            [{"type": "FINDING_HAS_PRIORITY", "to": priority_id}],
        )

        if score > top_severity:
            top_severity = score
            top_finding_id = finding_id

    logger.info(
        "Prioritize: %d priority nodes created, top_severity=%.2f",
        len(priority_ids),
        top_severity,
    )
    return {
        "priority_ids": priority_ids,
        "top_severity": top_severity,
        "top_finding_id": top_finding_id,
        "kg": kg_updates,
    }
