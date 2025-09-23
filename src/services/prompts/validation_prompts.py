"""
Validation and quality check prompt templates.

This module contains prompts for validating research outputs.
"""

from typing import Any

from src.services.prompts.base_prompts import (
    add_output_format,
    compose_prompt,
    create_system_prompt,
)


def generate_fact_checking_prompt(claims: list[str]) -> str:
    """Generate prompt for fact-checking claims."""
    system_prompt = create_system_prompt(
        role="Fact-checking Expert",
        context="You specialize in verifying claims against authoritative sources and evidence.",
        constraints=[
            "Verify claims against reliable sources",
            "Distinguish between facts and opinions",
            "Identify unsupported assertions",
            "Assess evidence quality and reliability",
            "Provide clear verification status",
        ],
    )

    claims_text = "\n".join(f"{i}. {claim}" for i, claim in enumerate(claims, 1))

    task_description = f"""
Please fact-check the following claims:

{claims_text}

For each claim, provide verification status, supporting evidence, and confidence level.
"""

    schema = {
        "fact_check_results": [
            {
                "claim_id": int,
                "claim": str,
                "verification_status": str,  # verified, disputed, unverified
                "evidence": [str],
                "confidence_level": float,
                "sources": [str],
            }
        ]
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_credibility_prompt(source: dict[str, Any]) -> str:
    """Generate prompt for assessing source credibility."""
    system_prompt = create_system_prompt(
        role="Source Credibility Assessor",
        context="You specialize in evaluating the credibility and reliability of academic and research sources.",
        constraints=[
            "Assess peer review status",
            "Evaluate journal reputation and impact factor",
            "Check author credentials and affiliations",
            "Consider publication recency and relevance",
            "Identify potential conflicts of interest",
        ],
    )

    source_info = f"""
Title: {source.get('title', 'Unknown')}
Author(s): {source.get('author', 'Unknown')}
Journal: {source.get('journal', 'Unknown')}
Year: {source.get('year', 'Unknown')}
DOI: {source.get('doi', 'Not provided')}
"""

    task_description = f"""
Assess the credibility of this source:

{source_info}

Provide a comprehensive credibility assessment with scoring and recommendations.
"""

    schema = {
        "credibility_assessment": {
            "overall_score": float,  # 0-1 scale
            "peer_review_status": str,
            "journal_reputation": str,
            "author_credentials": str,
            "recency_relevance": str,
            "potential_biases": [str],
            "recommendation": str,
            "confidence": float,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_consistency_prompt(findings: list[dict[str, Any]]) -> str:
    """Generate prompt for checking consistency across findings."""
    system_prompt = create_system_prompt(
        role="Research Consistency Analyst",
        context="You specialize in identifying inconsistencies and contradictions in research findings.",
        constraints=[
            "Compare findings systematically",
            "Identify direct contradictions",
            "Assess degree of consistency",
            "Consider methodological differences",
            "Provide reconciliation strategies",
        ],
    )

    findings_text = ""
    for i, finding in enumerate(findings, 1):
        claim = finding.get("claim", "")
        source = finding.get("source", f"Source {i}")
        findings_text += f"{i}. {claim} (from {source})\n"

    task_description = f"""
Check for consistency and contradictions among these findings:

{findings_text}

Identify any inconsistencies and suggest reconciliation strategies.
"""

    schema = {
        "consistency_analysis": {
            "consistency_score": float,  # 0-1 scale
            "contradictions": [
                {"finding_ids": [int], "description": str, "severity": str}
            ],
            "consistent_findings": [int],
            "reconciliation_strategies": [str],
            "confidence_assessment": str,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_hypothesis_validation_prompt(hypothesis: str, data: dict[str, Any]) -> str:
    """Generate prompt for validating research hypothesis."""
    system_prompt = create_system_prompt(
        role="Hypothesis Validation Expert",
        context="You specialize in evaluating research hypotheses against available evidence and data.",
        constraints=[
            "Assess hypothesis against available evidence",
            "Apply appropriate statistical reasoning",
            "Consider alternative explanations",
            "Evaluate data quality and sufficiency",
            "Provide confidence intervals where applicable",
        ],
    )

    data_summary = "\n".join(f"- {key}: {value}" for key, value in data.items())

    task_description = f"""
Validate the following hypothesis against the provided data:

Hypothesis: {hypothesis}

Available Data:
{data_summary}

Provide a comprehensive validation analysis with confidence assessment.
"""

    schema = {
        "hypothesis_validation": {
            "validation_result": str,  # supported, rejected, inconclusive
            "confidence_level": float,  # 0-1 scale
            "supporting_evidence": [str],
            "contradictory_evidence": [str],
            "data_sufficiency": str,
            "alternative_explanations": [str],
            "recommendations": [str],
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)
