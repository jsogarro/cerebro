"""
Agent-specific prompt templates.

This module contains prompts for different research agents.
"""

from typing import Any

from src.services.prompts.base_prompts import (
    add_output_format,
    compose_prompt,
    create_system_prompt,
)

AGENT_PROMPT_TEMPLATE_METADATA: dict[str, dict[str, str]] = {
    "literature_review": {
        "template": "generate_literature_agent_prompt",
        "version": "1.0.0",
    },
    "comparative_analysis": {
        "template": "generate_comparative_agent_prompt",
        "version": "1.0.0",
    },
    "methodology": {
        "template": "generate_methodology_agent_prompt",
        "version": "1.0.0",
    },
    "synthesis": {
        "template": "generate_synthesis_agent_prompt",
        "version": "1.0.0",
    },
    "citation": {
        "template": "generate_citation_agent_prompt",
        "version": "1.0.0",
    },
}


def get_agent_prompt_version(agent_type: str) -> str:
    """Return the tracked prompt version for an agent type."""

    metadata = AGENT_PROMPT_TEMPLATE_METADATA.get(agent_type)
    if metadata is None:
        return "unversioned"
    return metadata["version"]


def generate_literature_agent_prompt(task: dict[str, Any]) -> str:
    """Generate prompt for Literature Review Agent."""
    system_prompt = create_system_prompt(
        role="Literature Review Agent",
        context="You are specialized in conducting comprehensive literature reviews across academic databases.",
        constraints=[
            "Search systematically across multiple databases",
            "Apply rigorous inclusion/exclusion criteria",
            "Assess source quality and credibility",
            "Extract key findings and methodologies",
            "Identify research gaps and opportunities",
        ],
    )

    query = task.get("query", "")
    domains = task.get("domains", [])
    max_sources = task.get("max_sources", 50)

    task_description = f"""
Conduct a systematic literature review on: {query}
Research domains: {', '.join(domains)}
Target number of sources: {max_sources}

Please provide a comprehensive literature analysis following systematic review protocols.
"""

    schema = {
        "literature_analysis": {
            "search_strategy": str,
            "sources_found": [
                {"title": str, "authors": [str], "year": int, "relevance_score": float}
            ],
            "key_findings": [str],
            "methodologies_used": [str],
            "research_gaps": [str],
            "quality_assessment": str,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_comparative_agent_prompt(
    task: dict[str, Any] | list[Any],
    criteria: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Generate prompt for Comparative Analysis Agent."""
    system_prompt = create_system_prompt(
        role="Comparative Analysis Agent",
        context="You specialize in systematic comparison and evaluation of different approaches, theories, or solutions.",
        constraints=[
            "Use structured comparison frameworks",
            "Apply consistent evaluation criteria",
            "Provide objective assessments",
            "Identify strengths and weaknesses",
            "Support conclusions with evidence",
        ],
    )

    if isinstance(task, dict):
        items = task.get("items", [])
        criteria = task.get("criteria", criteria or [])
        context = task.get("context", context or {})
    else:
        items = task
        criteria = criteria or []
        context = context or {}

    items_text = "\n".join(f"- {item}" for item in items)
    criteria_text = ", ".join(criteria)

    task_description = f"""
Compare the following items using these criteria: {criteria_text}

Items to compare:
{items_text}

Provide a comprehensive comparative analysis with comparison matrix, strengths/weaknesses, and recommendations.
"""

    if context:
        task_description += f"\n\nContext: {context}"

    schema: dict[str, Any] = {
        "comparative_analysis": {
            "items_compared": [str],
            "comparison_criteria": [str],
            "comparison_matrix": {},
            "strengths_weaknesses": {},
            "recommendations": [str],
            "trade_offs": [str],
        }
    }

    if context:
        schema["comparative_analysis"]["context_considerations"] = [str]
        schema["comparative_analysis"]["contextual_recommendation"] = str

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_methodology_agent_prompt(
    research_question: str, context: dict[str, Any]
) -> str:
    """Generate prompt for Methodology Agent."""
    system_prompt = create_system_prompt(
        role="Research Methodology Expert",
        context="You specialize in designing appropriate research methodologies and approaches.",
        constraints=[
            "Select methodologies appropriate to research questions",
            "Consider validity and reliability requirements",
            "Account for practical constraints and resources",
            "Address potential biases and limitations",
            "Ensure ethical considerations are met",
        ],
    )

    research_type = context.get("type", "mixed")
    scope = context.get("scope", "general")

    task_description = f"""
Design an appropriate research methodology for: {research_question}
Research type: {research_type}
Scope: {scope}

Provide detailed methodological recommendations including design, data collection, and analysis approaches.
"""

    schema = {
        "methodology": {
            "research_design": str,
            "data_collection_methods": [str],
            "sampling_strategy": str,
            "analysis_approaches": [str],
            "validity_measures": [str],
            "ethical_considerations": [str],
            "limitations": [str],
            "timeline": str,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_synthesis_agent_prompt(agent_outputs: dict[str, Any]) -> str:
    """Generate prompt for Synthesis Agent."""
    system_prompt = create_system_prompt(
        role="Research Synthesis Agent",
        context="You specialize in integrating outputs from multiple research agents into coherent insights.",
        constraints=[
            "Integrate findings from all agents",
            "Resolve conflicts between different analyses",
            "Build coherent narratives from diverse inputs",
            "Maintain objectivity and evidence-based reasoning",
            "Identify meta-patterns and higher-order insights",
        ],
    )

    # Format agent outputs
    outputs_text = ""
    for agent, output in agent_outputs.items():
        outputs_text += f"\n{agent.upper()} AGENT OUTPUT:\n"
        if isinstance(output, dict):
            for key, value in output.items():
                outputs_text += f"- {key}: {value}\n"
        else:
            outputs_text += f"{output}\n"

    task_description = f"""
Synthesize the following outputs from different research agents:
{outputs_text}

Create a comprehensive synthesis that integrates all findings into a coherent research narrative.
"""

    schema = {
        "synthesis": {
            "integrated_findings": [str],
            "cross_agent_patterns": [str],
            "conflict_resolutions": [str],
            "meta_insights": [str],
            "comprehensive_narrative": str,
            "confidence_assessment": str,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)


def generate_citation_agent_prompt(
    sources: list[dict[str, Any]], style: str = "APA"
) -> str:
    """Generate prompt for Citation Agent."""
    system_prompt = create_system_prompt(
        role="Citation and Verification Agent",
        context="You specialize in proper academic citation formatting and source verification.",
        constraints=[
            f"Follow {style} citation style exactly",
            "Verify source information accuracy",
            "Check for proper attribution",
            "Identify potential plagiarism issues",
            "Ensure citation completeness",
        ],
    )

    sources_text = ""
    for i, source in enumerate(sources, 1):
        sources_text += f"{i}. Title: {source.get('title', 'Unknown')}\n"
        sources_text += f"   Author: {source.get('author', 'Unknown')}\n"
        sources_text += f"   Year: {source.get('year', 'Unknown')}\n"
        sources_text += f"   Journal: {source.get('journal', 'Unknown')}\n\n"

    task_description = f"""
Format the following sources according to {style} citation style:

{sources_text}

Provide properly formatted citations and verify source information.
"""

    schema = {
        "citations": {
            "formatted_citations": [{"source_id": int, "citation": str, "style": str}],
            "bibliography": [str],
            "verification_status": [
                {"source_id": int, "verified": bool, "issues": [str]}
            ],
            "completeness_check": str,
        }
    }

    prompt = compose_prompt([system_prompt, task_description])
    return add_output_format(prompt, schema)
