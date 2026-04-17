"""
Result aggregation node for combining and reconciling agent outputs.

This node aggregates results from multiple agents, resolves conflicts,
and creates a unified research output.
"""

import logging
import statistics
from typing import Any

from src.orchestration.state import ResearchState

logger = logging.getLogger(__name__)


async def result_aggregation_node(state: ResearchState) -> ResearchState:
    """
    Aggregate and reconcile results from multiple agents.

    This node:
    1. Collects results from all executed agents
    2. Identifies and resolves conflicts
    3. Merges findings into a coherent whole
    4. Calculates confidence scores

    Args:
        state: Current workflow state

    Returns:
        Updated state with aggregated results
    """
    logger.info("Aggregating agent results")

    try:
        # Collect all agent results
        agent_results = state.agent_results

        if not agent_results:
            logger.warning("No agent results to aggregate")
            return state

        # Extract key components from results
        aggregated: dict[str, Any] = {
            "sources": aggregate_sources(agent_results),
            "findings": aggregate_findings(agent_results),
            "methodologies": aggregate_methodologies(agent_results),
            "comparisons": aggregate_comparisons(agent_results),
            "citations": aggregate_citations(agent_results),
            "insights": extract_insights(agent_results),
            "recommendations": extract_recommendations(agent_results),
            "limitations": extract_limitations(agent_results),
        }

        # Identify conflicts
        conflicts = identify_conflicts(agent_results)
        if conflicts:
            state.conflicts = conflicts
            logger.warning(f"Identified {len(conflicts)} conflicts in agent results")

            # Attempt to resolve conflicts
            resolutions = await resolve_conflicts(conflicts, agent_results)
            aggregated["conflict_resolutions"] = resolutions

        # Calculate aggregate metrics
        metrics = calculate_aggregate_metrics(agent_results)
        aggregated["metrics"] = metrics

        # Calculate confidence score
        confidence = calculate_confidence_score(agent_results, conflicts, metrics)
        aggregated["confidence_score"] = confidence

        # Store aggregated results
        state.context["aggregated_results"] = aggregated

        # Update quality score
        state.quality_score = calculate_quality_score(aggregated, state)

        logger.info(f"Aggregation complete. Quality score: {state.quality_score:.2f}")

    except Exception as e:
        logger.error(f"Error in result aggregation: {e}")
        state.validation_errors.append(f"Result aggregation failed: {e!s}")
        state.error_count += 1

    return state


def aggregate_sources(agent_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Aggregate sources from all agents.

    Args:
        agent_results: Results from all agents

    Returns:
        Unified list of sources
    """
    all_sources = []
    source_ids = set()

    for agent_type, result in agent_results.items():
        sources = result.data.get("sources", [])

        for source in sources:
            # Deduplicate by ID or title
            source_id = source.get("id") or source.get("title", "")

            if source_id and source_id not in source_ids:
                source_ids.add(source_id)

                # Add agent annotation
                source["discovered_by"] = agent_type
                all_sources.append(source)

    # Sort by relevance or date
    all_sources.sort(
        key=lambda x: (x.get("relevance_score", 0), x.get("year", 0)), reverse=True
    )

    return all_sources


def aggregate_findings(agent_results: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """
    Aggregate findings from all agents.

    Args:
        agent_results: Results from all agents

    Returns:
        Consolidated findings grouped by category
    """
    findings = []
    finding_hashes = set()

    for agent_type, result in agent_results.items():
        agent_findings = result.output.get("findings", [])

        for finding in agent_findings:
            # Create hash for deduplication
            finding_text = finding.get("text", "")
            finding_hash = hash(finding_text.lower().strip())

            if finding_hash not in finding_hashes:
                finding_hashes.add(finding_hash)

                findings.append(
                    {
                        "text": finding_text,
                        "agent": agent_type,
                        "confidence": finding.get("confidence", 0.5),
                        "sources": finding.get("sources", []),
                        "category": finding.get("category", "general"),
                    }
                )

    # Group findings by category
    categorized: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        category = finding["category"]
        if category not in categorized:
            categorized[category] = []
        categorized[category].append(finding)

    return categorized


def aggregate_methodologies(agent_results: dict[str, Any]) -> dict[str, Any]:
    """
    Aggregate methodology assessments.

    Args:
        agent_results: Results from all agents

    Returns:
        Consolidated methodology information
    """
    methodologies: dict[str, Any] = {
        "recommended_approaches": [],
        "identified_biases": [],
        "validity_assessments": [],
        "statistical_methods": [],
    }

    # Check if methodology agent was used
    if "methodology" in agent_results:
        method_data = agent_results["methodology"].output

        methodologies["recommended_approaches"] = method_data.get(
            "recommended_approaches", []
        )
        methodologies["identified_biases"] = method_data.get("identified_biases", [])
        methodologies["validity_assessments"] = method_data.get(
            "validity_assessments", []
        )
        methodologies["statistical_methods"] = method_data.get(
            "statistical_methods", []
        )

    return methodologies


def aggregate_comparisons(agent_results: dict[str, Any]) -> dict[str, Any]:
    """
    Aggregate comparative analysis results.

    Args:
        agent_results: Results from all agents

    Returns:
        Consolidated comparisons
    """
    comparisons: dict[str, Any] = {"frameworks": [], "metrics": {}, "visualizations": []}

    # Check if comparative analysis agent was used
    if "comparative_analysis" in agent_results:
        comp_data = agent_results["comparative_analysis"].output

        comparisons["frameworks"] = comp_data.get("comparison_frameworks", [])
        comparisons["metrics"] = comp_data.get("comparison_metrics", {})
        comparisons["visualizations"] = comp_data.get("visualizations", [])

    return comparisons


def aggregate_citations(agent_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Aggregate and deduplicate citations.

    Args:
        agent_results: Results from all agents

    Returns:
        Unified citation list
    """
    all_citations = []
    citation_keys = set()

    for _agent_type, result in agent_results.items():
        citations = result.data.get("citations", [])

        for citation in citations:
            # Create unique key
            key = f"{citation.get('author', '')}_{citation.get('year', '')}_{citation.get('title', '')}"

            if key not in citation_keys:
                citation_keys.add(key)
                all_citations.append(citation)

    # Sort alphabetically by author
    all_citations.sort(key=lambda x: x.get("author", ""))

    return all_citations


def extract_insights(agent_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract key insights from agent results.

    Args:
        agent_results: Results from all agents

    Returns:
        List of insights
    """
    insights = []

    for agent_type, result in agent_results.items():
        agent_insights = result.data.get("insights", [])

        for insight in agent_insights:
            insights.append(
                {
                    "text": insight.get("text", ""),
                    "importance": insight.get("importance", "medium"),
                    "agent": agent_type,
                    "supporting_evidence": insight.get("evidence", []),
                }
            )

    # Sort by importance
    importance_order = {"high": 3, "medium": 2, "low": 1}
    insights.sort(key=lambda x: importance_order.get(x["importance"], 0), reverse=True)

    return insights


def extract_recommendations(agent_results: dict[str, Any]) -> list[str]:
    """
    Extract recommendations from agent results.

    Args:
        agent_results: Results from all agents

    Returns:
        List of recommendations
    """
    recommendations = []

    for result in agent_results.values():
        agent_recs = result.data.get("recommendations", [])
        recommendations.extend(agent_recs)

    # Deduplicate
    return list(set(recommendations))


def extract_limitations(agent_results: dict[str, Any]) -> list[str]:
    """
    Extract identified limitations.

    Args:
        agent_results: Results from all agents

    Returns:
        List of limitations
    """
    limitations = []

    for result in agent_results.values():
        agent_limits = result.data.get("limitations", [])
        limitations.extend(agent_limits)

    # Deduplicate
    return list(set(limitations))


def identify_conflicts(agent_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Identify conflicts between agent results.

    Args:
        agent_results: Results from all agents

    Returns:
        List of identified conflicts
    """
    conflicts = []

    # Check for conflicting findings
    all_findings: dict[str, list[dict[str, Any]]] = {}
    for agent_type, result in agent_results.items():
        findings = result.output.get("findings", [])
        for finding in findings:
            topic = finding.get("topic", "general")
            if topic not in all_findings:
                all_findings[topic] = []
            all_findings[topic].append({"agent": agent_type, "finding": finding})

    # Identify topics with conflicting findings
    for topic, findings_list in all_findings.items():
        if len(findings_list) > 1:
            # Check for contradictions
            sentiments = []
            for item in findings_list:
                sentiment = item["finding"].get("sentiment", "neutral")
                sentiments.append(sentiment)

            # If sentiments differ significantly, flag as conflict
            if len(set(sentiments)) > 1:
                conflicts.append(
                    {
                        "type": "finding_conflict",
                        "topic": topic,
                        "agents": [item["agent"] for item in findings_list],
                        "details": findings_list,
                        "severity": "medium",
                    }
                )

    return conflicts


async def resolve_conflicts(
    conflicts: list[dict[str, Any]], agent_results: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Attempt to resolve identified conflicts.

    Args:
        conflicts: List of conflicts
        agent_results: Results from all agents

    Returns:
        List of resolutions
    """
    resolutions = []

    for conflict in conflicts:
        resolution: dict[str, Any] = {
            "conflict": conflict,
            "resolution_strategy": None,
            "resolved_value": None,
        }

        if conflict["type"] == "finding_conflict":
            # Use confidence-weighted resolution
            details = conflict["details"]

            # Calculate weighted consensus
            weighted_findings = []
            for detail in details:
                confidence = detail["finding"].get("confidence", 0.5)
                weighted_findings.append(
                    {"finding": detail["finding"], "weight": confidence}
                )

            # Select highest confidence finding
            best_finding = max(weighted_findings, key=lambda x: x["weight"])

            resolution["resolution_strategy"] = "confidence_weighted"
            resolution["resolved_value"] = best_finding["finding"]

        resolutions.append(resolution)

    return resolutions


def calculate_aggregate_metrics(agent_results: dict[str, Any]) -> dict[str, Any]:
    """
    Calculate aggregate metrics from agent results.

    Args:
        agent_results: Results from all agents

    Returns:
        Aggregate metrics
    """
    metrics = {
        "total_sources": 0,
        "total_findings": 0,
        "total_citations": 0,
        "average_confidence": 0.0,
        "coverage_score": 0.0,
    }

    # Count sources
    all_sources = set()
    for result in agent_results.values():
        sources = result.data.get("sources", [])
        for source in sources:
            source_id = source.get("id") or source.get("title", "")
            if source_id:
                all_sources.add(source_id)

    metrics["total_sources"] = len(all_sources)

    # Count findings
    total_findings = sum(
        len(result.data.get("findings", [])) for result in agent_results.values()
    )
    metrics["total_findings"] = total_findings

    # Count citations
    all_citations = set()
    for result in agent_results.values():
        citations = result.data.get("citations", [])
        for citation in citations:
            key = f"{citation.get('author', '')}_{citation.get('year', '')}"
            all_citations.add(key)

    metrics["total_citations"] = len(all_citations)

    # Calculate average confidence
    confidences = []
    for result in agent_results.values():
        if hasattr(result, "confidence"):
            confidences.append(result.confidence)

    if confidences:
        metrics["average_confidence"] = statistics.mean(confidences)

    # Calculate coverage score (percentage of planned agents that succeeded)
    total_agents = len(agent_results)
    successful_agents = sum(
        1 for result in agent_results.values() if result.status == "success"
    )

    metrics["coverage_score"] = (
        successful_agents / total_agents if total_agents > 0 else 0
    )

    return metrics


def calculate_confidence_score(
    agent_results: dict[str, Any],
    conflicts: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> float:
    """
    Calculate overall confidence score for aggregated results.

    Args:
        agent_results: Results from all agents
        conflicts: Identified conflicts
        metrics: Aggregate metrics

    Returns:
        Confidence score (0-1)
    """
    base_confidence = metrics.get("average_confidence", 0.5)

    # Penalize for conflicts
    conflict_penalty = len(conflicts) * 0.05

    # Boost for good coverage
    coverage_boost = metrics.get("coverage_score", 0) * 0.2

    # Boost for multiple sources
    source_boost = min(metrics.get("total_sources", 0) / 20, 0.2)

    # Calculate final confidence
    confidence = base_confidence - conflict_penalty + coverage_boost + source_boost

    # Clamp to [0, 1]
    final_confidence: float = max(0.0, min(1.0, confidence))
    return final_confidence


def calculate_quality_score(aggregated: dict[str, Any], state: ResearchState) -> float:
    """
    Calculate overall quality score for the research.

    Args:
        aggregated: Aggregated results
        state: Current workflow state

    Returns:
        Quality score (0-1)
    """
    scores = []

    # Source quality
    source_count = len(aggregated.get("sources", []))
    quality_targets = state.research_plan.get("quality_targets", {}) if state.research_plan is not None else {}
    min_sources = quality_targets.get("minimum_sources", 10)
    source_score = min(source_count / min_sources, 1.0)
    scores.append(source_score)

    # Finding quality
    findings = aggregated.get("findings", {})
    finding_count = sum(len(f) for f in findings.values())
    finding_score = min(finding_count / 10, 1.0)
    scores.append(finding_score)

    # Citation quality
    citation_count = len(aggregated.get("citations", []))
    citation_score = min(citation_count / 15, 1.0)
    scores.append(citation_score)

    # Confidence score
    confidence = aggregated.get("confidence_score", 0.5)
    scores.append(confidence)

    # Agent success rate
    if state.metadata is not None:
        success_rate = state.metadata.total_nodes_executed / max(len(state.agent_tasks), 1)
        scores.append(success_rate)

    # Calculate weighted average
    mean_score: float = statistics.mean(scores)
    return mean_score


__all__ = ["result_aggregation_node"]
