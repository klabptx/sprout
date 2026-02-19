"""Synthesize node: produce a Report KG node from findings via LLM."""
from __future__ import annotations

import logging

from sprout.config import get_settings
from sprout.graph_types import NodeEnvelope, ReportPayload
from sprout.state import GraphState, KG, new_id

logger = logging.getLogger(__name__)


def compute_confidence(
    data_quality_flag: bool,
    top_severity: float,
    severity_threshold: float,
) -> float:
    """Compute report confidence score as a pure function.

    Returns a value in [0.5, confidence_base_good].
    """
    s = get_settings()
    base = s.confidence_base_poor if data_quality_flag else s.confidence_base_good
    penalty = s.confidence_severity_penalty if top_severity < severity_threshold else 0.0
    return round(max(0.5, base - penalty), 2)


def _build_llm_prompt(
    findings: list[NodeEnvelope],
    event_summaries: list[str],
    recommendation_texts: list[str],
    run_id: str = "",
) -> str:
    """Build a prompt that presents top-N findings to the LLM."""
    source_line = f"Source file: {run_id}\n" if run_id else ""

    if not findings:
        return (
            f"{source_line}"
            "Write a concise report summary (1-3 sentences). "
            "No anomalous findings were detected in this run."
        )

    finding_sections: list[str] = []
    for i, fn in enumerate(findings, 1):
        p = fn["payload"]
        section = (
            f"Finding {i}: {p['title']}\n"
            f"  Application type: {p['application_type']}\n"
            f"  Severity: {p['severity']:.2f}\n"
            f"  Diagnosis:\n  {p['diagnosis_prompt']}"
        )
        finding_sections.append(section)

    evidence = "; ".join(event_summaries[:8]) if event_summaries else "none"
    recommendations = "; ".join(recommendation_texts) if recommendation_texts else "none"

    return (
        f"{source_line}"
        "Write a concise report summary (2-5 sentences) covering ALL findings below. "
        "Prioritize the highest-severity issues. "
        "Do not include numerical values, percentages, or event code numbers. "
        "Do not include file names or their .2020 extension. "
        "Do not use bullet points or JSON. Do not add new facts.\n\n"
        "Findings:\n"
        + "\n\n".join(finding_sections)
        + f"\n\nEvidence: {evidence}\n"
        f"Suggested checks: {recommendations}\n"
    )


async def synthesize(state: GraphState) -> dict:
    """Summarize ALL findings into a Report node (optionally via an LLM)."""
    from sprout.llm_backends import generate_llm_summary

    # Collect all findings, sorted by severity descending.
    finding_nodes: list[NodeEnvelope] = []
    for finding_id in state["findingIds"]:
        node = state["kg"].get(finding_id)
        if node:
            finding_nodes.append(node)
    finding_nodes.sort(key=lambda n: n["payload"]["severity"], reverse=True)

    # Slice to top-N if configured (default -1 means all).
    max_findings = state.get("synthesizeMaxFindings", -1)
    if max_findings > 0:
        finding_nodes = finding_nodes[:max_findings]

    # Gather event summaries across all selected findings (deduplicated).
    seen_event_ids: set[str] = set()
    event_summaries: list[str] = []
    for finding_node in finding_nodes:
        for event_id in finding_node["payload"]["event_refs"]:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            event_node = state["kg"].get(event_id)
            if event_node:
                event_summaries.append(event_node["payload"]["summary"])

    recommendation_texts = [
        state["kg"][rec_id]["payload"]["text"]
        for rec_id in state.get("recommendationIds", [])
        if rec_id in state["kg"]
    ]

    confidence = compute_confidence(
        state["dataQualityFlag"],
        state["topSeverity"],
        state["severityThreshold"],
    )

    # Build fallback report from all findings.
    report_lines: list[str] = []
    for fn in finding_nodes:
        report_lines.append(fn["payload"]["diagnosis_prompt"])
    if not report_lines:
        report_lines.append("No critical findings in this run.")
    report_lines.append(f"Severity {state['topSeverity']:.2f}.")
    if event_summaries:
        report_lines.append(f"Evidence: {'; '.join(event_summaries[:8])}.")
    if recommendation_texts:
        report_lines.append(f"Suggested checks: {' '.join(recommendation_texts)}")
    report_lines.append(f"Confidence {confidence:.2f}.")

    prompt = _build_llm_prompt(
        finding_nodes,
        event_summaries,
        recommendation_texts,
        run_id=state.get("sourceFile", ""),
    )
    backend = state.get("llmBackend", "auto")
    logger.info("Synthesize: invoking LLM backend=%s", backend)
    llm_summary, llm_error, llm_model = await generate_llm_summary(prompt, backend)

    if llm_error:
        logger.warning("Synthesize: LLM error (backend=%s): %s", backend, llm_error)
    logger.info("Synthesize: report produced via %s (confidence=%.2f)", llm_model, confidence)

    report_id = new_id("rpt")
    report_payload: ReportPayload = {
        "report_id": report_id,
        "run_id": state["runId"],
        "summary": llm_summary or " ".join(report_lines),
        "severity": state["topSeverity"],
        "confidence": confidence,
        "finding_refs": state["findingIds"],
        "priority_refs": state["priorityIds"],
        "recommendation_refs": state["recommendationIds"],
    }
    report_node: NodeEnvelope = {
        "node_id": report_id,
        "node_type": "Report",
        "payload": report_payload,
        "edges": [],
    }

    kg_updates: KG = {report_id: report_node}

    for f_id in state["findingIds"]:
        finding_node = dict(state["kg"][f_id])
        finding_node["edges"] = list(finding_node.get("edges", [])) + [
            {"type": "FINDING_INFORMS_REPORT", "to": report_id}
        ]
        kg_updates[f_id] = finding_node  # type: ignore[assignment]
    for p_id in state["priorityIds"]:
        prio_node = dict(state["kg"][p_id])
        prio_node["edges"] = list(prio_node.get("edges", [])) + [
            {"type": "PRIORITY_INFORMS_REPORT", "to": report_id}
        ]
        kg_updates[p_id] = prio_node  # type: ignore[assignment]

    return {
        "reportId": report_id,
        "kg": kg_updates,
        "llmModel": llm_model,
        "llmError": llm_error,
    }
