"""Augment node: attach mock RAG snippets as Recommendation KG nodes."""

from __future__ import annotations

import logging

from sprout.graph_types import AugmentationPayload, NodeEnvelope, RecommendationPayload
from sprout.state import KG, GraphState, new_id, with_edges

logger = logging.getLogger(__name__)


def rag_stub(application_type: str) -> list[str]:
    if application_type == "seeding":
        return [
            "Check seed meter singulation belt wear.",
            "Inspect vacuum line for leaks or clogs.",
            "Calibrate singulation target for current seed lot.",
        ]
    if application_type == "row_cleaner":
        return [
            "Inspect gauge wheel contact and spring tension.",
            "Verify downforce sensor calibration at baseline.",
            "Reduce speed over rough terrain during spikes.",
        ]
    return [
        "Compare against historical norms for this operation and season.",
        "Check equipment configuration and seed/inputs setup.",
        "Inspect outlier rows in summary data for data quality issues.",
    ]


async def augment(state: GraphState) -> dict:
    """Attach mock RAG snippets as recommendations for the top finding."""
    if not state["top_finding_id"]:
        return {}
    finding_node = state["kg"].get(state["top_finding_id"])
    if not finding_node:
        return {}

    app_type = finding_node["payload"]["application_type"]
    snippets = rag_stub(app_type)
    augmentation_id = new_id("aug", state["run_id"])
    augmentation_payload: AugmentationPayload = {
        "augmentation_id": augmentation_id,
        "query": f"{app_type} planter troubleshooting",
        "snippets": snippets,
    }
    augmentation_node: NodeEnvelope = {
        "node_id": augmentation_id,
        "node_type": "Augmentation",
        "payload": augmentation_payload,
        "edges": [],
    }

    recommendation_ids: list[str] = []
    kg_updates: KG = {augmentation_id: augmentation_node}

    for snippet in snippets:
        recommendation_id = new_id("rec", state["run_id"])
        recommendation_payload: RecommendationPayload = {
            "recommendation_id": recommendation_id,
            "finding_id": finding_node["node_id"],
            "text": snippet,
            "source_refs": [augmentation_id],
        }
        recommendation_node: NodeEnvelope = {
            "node_id": recommendation_id,
            "node_type": "Recommendation",
            "payload": recommendation_payload,
            "edges": [],
        }
        recommendation_ids.append(recommendation_id)
        kg_updates[recommendation_id] = recommendation_node

    kg_updates[finding_node["node_id"]] = with_edges(
        finding_node,
        [
            {"type": "FINDING_HAS_RECOMMENDATION", "to": rec_id}
            for rec_id in recommendation_ids
        ],
    )

    logger.info(
        "Augment: created augmentation %s with %d recommendations",
        augmentation_id,
        len(recommendation_ids),
    )
    return {
        "augmentation_ids": [augmentation_id],
        "recommendation_ids": recommendation_ids,
        "kg": kg_updates,
    }
