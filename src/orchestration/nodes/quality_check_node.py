"""
Quality check node for validating research output quality.

This node performs comprehensive quality checks on the aggregated research results,
ensuring they meet defined standards before report generation.
"""

from datetime import UTC, datetime
from typing import Any

from structlog import get_logger

from src.orchestration.state import ResearchState

logger = get_logger()


async def quality_check_node(state: ResearchState) -> ResearchState:
    """
    Perform quality checks on research results.

    This node:
    1. Validates completeness of research
    2. Checks accuracy and consistency
    3. Verifies citation quality
    4. Assesses coherence and logical flow
    5. Determines if quality standards are met

    Args:
        state: Current workflow state

    Returns:
        Updated state with quality assessment
    """
    logger.info("Performing quality checks")

    try:
        # Get quality criteria from context
        quality_criteria = state.context.get("quality_criteria", {})
        validation_rules = state.context.get("validation_rules", [])
        aggregated_results = state.context.get("aggregated_results", {})

        # Initialize quality report
        checks_performed: list[dict[str, Any]] = []
        issues_found: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        quality_report: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "checks_performed": checks_performed,
            "issues_found": issues_found,
            "warnings": warnings,
            "score": 0.0,
            "passed": False,
        }

        # Perform completeness check
        completeness_result = check_completeness(state, quality_criteria)
        checks_performed.append(completeness_result)
        if not completeness_result["passed"]:
            comp_issues = completeness_result.get("issues", [])
            if isinstance(comp_issues, list):
                issues_found.extend(comp_issues)

        # Perform accuracy check
        accuracy_result = check_accuracy(aggregated_results, quality_criteria)
        checks_performed.append(accuracy_result)
        if not accuracy_result["passed"]:
            acc_issues = accuracy_result.get("issues", [])
            if isinstance(acc_issues, list):
                issues_found.extend(acc_issues)

        # Perform depth check
        depth_result = check_depth(aggregated_results, quality_criteria)
        checks_performed.append(depth_result)
        if not depth_result["passed"]:
            depth_issues = depth_result.get("issues", [])
            if isinstance(depth_issues, list):
                issues_found.extend(depth_issues)

        # Perform coherence check
        coherence_result = check_coherence(aggregated_results, quality_criteria)
        checks_performed.append(coherence_result)
        if not coherence_result["passed"]:
            coh_issues = coherence_result.get("issues", [])
            if isinstance(coh_issues, list):
                issues_found.extend(coh_issues)

        # Validate against rules
        validation_result = validate_against_rules(state, validation_rules)
        checks_performed.append(validation_result)
        val_errors = validation_result.get("errors", [])
        val_warnings = validation_result.get("warnings", [])
        if isinstance(val_errors, list):
            issues_found.extend(val_errors)
        if isinstance(val_warnings, list):
            warnings.extend(val_warnings)

        # Check for plagiarism if required
        if quality_criteria.get("accuracy", {}).get("plagiarism_check", False):
            plagiarism_result = await check_plagiarism(aggregated_results)
            checks_performed.append(plagiarism_result)
            if plagiarism_result.get("plagiarism_detected"):
                issues_found.append(
                    {
                        "type": "plagiarism",
                        "severity": "error",
                        "message": "Potential plagiarism detected",
                    }
                )

        # Calculate overall quality score
        quality_score = calculate_final_quality_score(quality_report)
        quality_report["score"] = quality_score

        # Determine if quality standards are met
        min_quality_score = quality_criteria.get("minimum_quality_score", 0.7)
        error_issues = [i for i in issues_found if i.get("severity") == "error"]
        quality_report["passed"] = quality_score >= min_quality_score and len(error_issues) == 0

        # Update state
        state.quality_score = quality_score
        state.context["quality_report"] = quality_report

        # Add validation errors if quality check failed
        if not quality_report["passed"]:
            for issue in issues_found:
                if issue.get("severity") == "error":
                    msg = issue.get("message")
                    if isinstance(msg, str):
                        state.validation_errors.append(msg)

        logger.info(
            f"Quality check complete. Score: {quality_score:.2f}, Passed: {quality_report['passed']}"
        )

    except Exception as e:
        logger.error(f"Error in quality check: {e}")
        state.validation_errors.append(f"Quality check failed: {e!s}")
        state.error_count += 1

    return state


def check_completeness(
    state: ResearchState, criteria: dict[str, Any]
) -> dict[str, Any]:
    """
    Check research completeness.

    Args:
        state: Current workflow state
        criteria: Quality criteria

    Returns:
        Completeness check result
    """
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    result: dict[str, Any] = {"check_name": "completeness", "passed": True, "issues": issues, "metrics": metrics}

    completeness_criteria = criteria.get("completeness", {})

    # Check if all required agents executed
    if completeness_criteria.get("all_agents_executed") and state.research_plan is not None:
        planned_agents = set(state.research_plan.get("agents", []))
        completed_agents = state.completed_agents
        missing_agents = planned_agents - completed_agents

        if missing_agents:
            result["passed"] = False
            issues.append(
                {
                    "type": "incomplete_execution",
                    "severity": "error",
                    "message": f"Missing agents: {', '.join(missing_agents)}",
                }
            )

        metrics["agent_coverage"] = (
            len(completed_agents) / len(planned_agents) if planned_agents else 0
        )

    # Check minimum success rate
    min_success_rate = completeness_criteria.get("minimum_agent_success_rate", 0.8)
    total_agents = len(state.agent_tasks)
    successful_agents = len(state.completed_agents)
    success_rate = successful_agents / total_agents if total_agents > 0 else 0

    metrics["success_rate"] = success_rate

    if success_rate < min_success_rate:
        result["passed"] = False
        issues.append(
            {
                "type": "low_success_rate",
                "severity": "warning",
                "message": f"Agent success rate {success_rate:.2%} below minimum {min_success_rate:.2%}",
            }
        )

    return result


def check_accuracy(results: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """
    Check research accuracy.

    Args:
        results: Aggregated results
        criteria: Quality criteria

    Returns:
        Accuracy check result
    """
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    result: dict[str, Any] = {"check_name": "accuracy", "passed": True, "issues": issues, "metrics": metrics}

    accuracy_criteria = criteria.get("accuracy", {})

    # Check citation verification
    if accuracy_criteria.get("citation_verification_required"):
        citations = results.get("citations", [])
        verified_citations = [c for c in citations if c.get("verified", False)]
        verification_rate = len(verified_citations) / len(citations) if citations else 0

        metrics["citation_verification_rate"] = verification_rate

        if verification_rate < 0.8:
            result["passed"] = False
            issues.append(
                {
                    "type": "unverified_citations",
                    "severity": "warning",
                    "message": f"Only {verification_rate:.2%} of citations verified",
                }
            )

    # Check for conflicts
    conflicts = results.get("conflicts", [])
    unresolved_conflicts = [c for c in conflicts if not c.get("resolved", False)]

    metrics["unresolved_conflicts"] = len(unresolved_conflicts)

    if unresolved_conflicts:
        result["passed"] = False
        issues.append(
            {
                "type": "unresolved_conflicts",
                "severity": "warning",
                "message": f"{len(unresolved_conflicts)} conflicts remain unresolved",
            }
        )

    return result


def check_depth(results: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """
    Check research depth.

    Args:
        results: Aggregated results
        criteria: Quality criteria

    Returns:
        Depth check result
    """
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    result: dict[str, Any] = {"check_name": "depth", "passed": True, "issues": issues, "metrics": metrics}

    depth_criteria = criteria.get("depth", {})

    # Check minimum sources
    min_sources = depth_criteria.get("minimum_sources", 10)
    actual_sources = len(results.get("sources", []))

    metrics["source_count"] = actual_sources

    if actual_sources < min_sources:
        result["passed"] = False
        issues.append(
            {
                "type": "insufficient_sources",
                "severity": "error",
                "message": f"Only {actual_sources} sources found, minimum {min_sources} required",
            }
        )

    # Check analysis depth
    analysis_depth = depth_criteria.get("analysis_depth", "moderate")
    findings = results.get("findings", {})
    total_findings = sum(len(f) for f in findings.values())

    metrics["finding_count"] = total_findings

    min_findings = {"simple": 5, "moderate": 10, "complex": 20}.get(analysis_depth, 10)

    if total_findings < min_findings:
        result["passed"] = False
        issues.append(
            {
                "type": "shallow_analysis",
                "severity": "warning",
                "message": f"Only {total_findings} findings, expected at least {min_findings} for {analysis_depth} analysis",
            }
        )

    return result


def check_coherence(
    results: dict[str, Any], criteria: dict[str, Any]
) -> dict[str, Any]:
    """
    Check research coherence and logical flow.

    Args:
        results: Aggregated results
        criteria: Quality criteria

    Returns:
        Coherence check result
    """
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    result: dict[str, Any] = {"check_name": "coherence", "passed": True, "issues": issues, "metrics": metrics}

    coherence_criteria = criteria.get("coherence", {})

    # Check synthesis quality
    if coherence_criteria.get("synthesis_quality_check"):
        insights = results.get("insights", [])
        _recommendations = results.get("recommendations", [])

        # Check if insights are supported by evidence
        unsupported_insights = [i for i in insights if not i.get("supporting_evidence")]

        metrics["unsupported_insights"] = len(unsupported_insights)

        if unsupported_insights:
            result["passed"] = False
            issues.append(
                {
                    "type": "unsupported_claims",
                    "severity": "warning",
                    "message": f"{len(unsupported_insights)} insights lack supporting evidence",
                }
            )

    # Check logical flow
    if coherence_criteria.get("logical_flow_validation"):
        # Check if there's a clear progression from findings to insights to recommendations
        has_findings = bool(results.get("findings"))
        has_insights = bool(results.get("insights"))
        has_recommendations = bool(results.get("recommendations"))

        if has_recommendations and not has_insights:
            result["passed"] = False
            issues.append(
                {
                    "type": "logical_gap",
                    "severity": "warning",
                    "message": "Recommendations provided without supporting insights",
                }
            )

        if has_insights and not has_findings:
            result["passed"] = False
            issues.append(
                {
                    "type": "logical_gap",
                    "severity": "error",
                    "message": "Insights provided without supporting findings",
                }
            )

    return result


def validate_against_rules(
    state: ResearchState, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Validate research against defined rules.

    Args:
        state: Current workflow state
        rules: Validation rules

    Returns:
        Validation result
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    result: dict[str, Any] = {
        "check_name": "rule_validation",
        "passed": True,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }

    aggregated = state.context.get("aggregated_results", {})

    for rule in rules:
        rule_name = rule.get("name", "unknown")
        rule_type = rule.get("type", "unknown")
        severity = rule.get("severity", "warning")

        passed = False

        if rule_type == "count":
            field = rule.get("field", "")
            operator = rule.get("operator", ">=")
            value = rule.get("value", 0)

            actual_value = len(aggregated.get(field, []))

            if operator == ">=":
                passed = actual_value >= value
            elif operator == ">":
                passed = actual_value > value
            elif operator == "==":
                passed = actual_value == value
            elif operator == "<=":
                passed = actual_value <= value
            elif operator == "<":
                passed = actual_value < value

            if not passed:
                message = f"Rule '{rule_name}' failed: {field} count is {actual_value}, expected {operator} {value}"

                if severity == "error":
                    errors.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )
                    result["passed"] = False
                else:
                    warnings.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )

        elif rule_type == "completion":
            required_agents = rule.get("required_agents", [])
            min_success_rate = rule.get("minimum_success_rate", 0.8)

            completed = len([a for a in required_agents if a in state.completed_agents])
            success_rate = completed / len(required_agents) if required_agents else 0

            passed = success_rate >= min_success_rate

            if not passed:
                message = f"Rule '{rule_name}' failed: Only {success_rate:.2%} of required agents completed"

                if severity == "error":
                    errors.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )
                    result["passed"] = False
                else:
                    warnings.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )

        elif rule_type == "presence":
            field = rule.get("field", "")
            required = rule.get("required", True)

            field_present = bool(aggregated.get(field))
            passed = field_present if required else True

            if not passed:
                message = (
                    f"Rule '{rule_name}' failed: Required field '{field}' is missing"
                )

                if severity == "error":
                    errors.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )
                    result["passed"] = False
                else:
                    warnings.append(
                        {"rule": rule_name, "message": message, "severity": severity}
                    )

    metrics["rules_passed"] = len(rules) - len(errors) - len(warnings)
    metrics["total_rules"] = len(rules)

    return result


async def check_plagiarism(results: dict[str, Any]) -> dict[str, Any]:
    """
    Check for potential plagiarism in research output.

    Args:
        results: Aggregated results

    Returns:
        Plagiarism check result
    """
    flagged_sections: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "check_name": "plagiarism",
        "plagiarism_detected": False,
        "similarity_score": 0.0,
        "flagged_sections": flagged_sections,
    }

    # Check for overly long direct quotes without attribution
    findings = results.get("findings", {})

    for category, finding_list in findings.items():
        for finding in finding_list:
            text = finding.get("text", "")

            # Check for suspiciously long unattributed text
            if len(text) > 200 and not finding.get("sources"):
                flagged_sections.append(
                    {
                        "category": category,
                        "text_snippet": text[:100] + "...",
                        "issue": "Long text without attribution",
                    }
                )

    if flagged_sections:
        result["plagiarism_detected"] = True
        result["similarity_score"] = min(len(flagged_sections) * 0.1, 1.0)

    return result


def calculate_final_quality_score(quality_report: dict[str, Any]) -> float:
    """
    Calculate final quality score based on all checks.

    Args:
        quality_report: Quality check report

    Returns:
        Final quality score (0-1)
    """
    scores = []
    weights = {
        "completeness": 0.25,
        "accuracy": 0.25,
        "depth": 0.25,
        "coherence": 0.15,
        "rule_validation": 0.10,
    }

    for check in quality_report["checks_performed"]:
        check_name = check.get("check_name", "")
        weight = weights.get(check_name, 0.1)

        # Calculate score for this check
        if check.get("passed", False):
            score = 1.0
        else:
            # Partial credit based on severity of issues
            error_count = len(
                [i for i in check.get("issues", []) if i.get("severity") == "error"]
            )
            warning_count = len(
                [i for i in check.get("issues", []) if i.get("severity") == "warning"]
            )

            score = max(0, 1.0 - (error_count * 0.3) - (warning_count * 0.1))

        scores.append(score * weight)

    # Apply penalties for critical issues
    critical_issues = len(
        [i for i in quality_report["issues_found"] if i.get("severity") == "error"]
    )
    penalty = min(critical_issues * 0.1, 0.3)

    final_score = sum(scores) - penalty

    return max(0.0, min(1.0, final_score))


__all__ = ["quality_check_node"]
