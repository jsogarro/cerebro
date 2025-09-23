"""
Research-specific prompt templates.

This module contains prompts for various research tasks.
"""

from typing import Any

from src.services.prompts.base_prompts import (
    add_output_format,
    compose_prompt,
    create_research_context,
    create_system_prompt,
)


def generate_query_decomposition_prompt(query: Any) -> str:
    """
    Generate prompt for decomposing research query into sub-questions.

    Pure function that creates query decomposition prompt.

    Args:
        query: Research query object or dict

    Returns:
        Formatted prompt for query decomposition
    """
    # Extract query parameters
    if hasattr(query, "text"):
        query_text = query.text
        domains = query.domains
        depth = (
            query.depth_level.value
            if hasattr(query.depth_level, "value")
            else query.depth_level
        )
        scope = getattr(query, "scope", None)
    else:
        query_text = query.get("text", "")
        domains = query.get("domains", [])
        depth = query.get("depth_level", "comprehensive")
        scope = query.get("scope")

    # Create system prompt
    system_prompt = create_system_prompt(
        role="expert research planner",
        context="You specialize in breaking down complex research questions into manageable, structured research plans.",
        constraints=[
            "Generate comprehensive sub-questions that cover all aspects of the main question",
            "Identify the most appropriate research methodology for each component",
            "Suggest realistic timelines and resource requirements",
            "Consider interdisciplinary connections and potential biases",
            f"Tailor the depth and scope to {depth} level research",
        ],
    )

    # Create research context
    context = create_research_context(
        query=query_text,
        domains=domains,
        depth=depth,
        scope=scope.model_dump() if hasattr(scope, "model_dump") else scope,
    )

    # Main task description
    task = f"""
Please decompose the research question into a structured research plan.

{context}

Your task is to create a comprehensive research plan that:
1. Breaks down the main question into specific, answerable sub-questions
2. Identifies the research methodology for each component
3. Suggests the sequence of research activities
4. Estimates time and resource requirements
5. Identifies potential challenges and mitigation strategies
"""

    # Define output schema
    schema = {
        "research_plan": {
            "main_question": str,
            "sub_questions": [str],
            "phases": [str],
            "methodology": str,
            "estimated_time": int,  # in seconds
            "resource_requirements": [str],
            "potential_challenges": [str],
            "success_criteria": [str],
        }
    }

    # Compose final prompt
    prompt_parts = [system_prompt, task]
    prompt = compose_prompt(prompt_parts)

    return add_output_format(prompt, schema)


def generate_literature_review_prompt(sources: list[str], focus: str = None) -> str:
    """
    Generate prompt for literature review analysis.

    Pure function that creates literature review prompt.

    Args:
        sources: List of source titles or abstracts
        focus: Optional focus area for the review

    Returns:
        Formatted prompt for literature review
    """
    system_prompt = create_system_prompt(
        role="expert literature reviewer",
        context="You specialize in systematic literature reviews and meta-analysis of academic sources.",
        constraints=[
            "Identify key themes and patterns across sources",
            "Highlight contradictions and gaps in the literature",
            "Assess the quality and credibility of sources",
            "Synthesize findings into coherent insights",
            "Use academic writing standards",
        ],
    )

    # Format sources
    sources_text = "\n".join(f"- {source}" for source in sources)

    focus_instruction = f"\nFocus Area: {focus}\n" if focus else ""

    task = f"""
Please conduct a comprehensive literature review of the following sources:

{sources_text}
{focus_instruction}
Your analysis should include:
1. Key themes and findings across sources
2. Areas of consensus and disagreement
3. Methodological approaches used
4. Research gaps and limitations
5. Quality assessment of sources
6. Synthesis of main insights
"""

    schema = {
        "literature_review": {
            "key_themes": [str],
            "main_findings": [str],
            "consensus_areas": [str],
            "disagreements": [str],
            "methodologies": [str],
            "research_gaps": [str],
            "quality_assessment": str,
            "synthesis": str,
            "recommendations": [str],
        }
    }

    prompt = compose_prompt([system_prompt, task])
    return add_output_format(prompt, schema)


def generate_synthesis_prompt(findings: list[dict[str, Any]]) -> str:
    """
    Generate prompt for synthesizing research findings.

    Pure function that creates synthesis prompt.

    Args:
        findings: List of findings with sources

    Returns:
        Formatted prompt for synthesis
    """
    system_prompt = create_system_prompt(
        role="expert research synthesizer",
        context="You specialize in integrating diverse research findings into coherent insights and conclusions.",
        constraints=[
            "Identify patterns and relationships across findings",
            "Resolve contradictions through critical analysis",
            "Build coherent arguments from diverse evidence",
            "Highlight uncertainties and limitations",
            "Provide actionable insights and recommendations",
        ],
    )

    # Format findings
    findings_text = ""
    for i, finding in enumerate(findings, 1):
        finding_text = finding.get("finding", "")
        source = finding.get("source", f"Source {i}")
        findings_text += f"{i}. {finding_text} (Source: {source})\n"

    task = f"""
Please synthesize the following research findings into a coherent analysis:

{findings_text}

Your synthesis should:
1. Identify overarching patterns and themes
2. Integrate findings into a coherent narrative
3. Address any contradictions or inconsistencies
4. Draw evidence-based conclusions
5. Identify implications and future directions
6. Acknowledge limitations and uncertainties
"""

    schema = {
        "synthesis": {
            "main_patterns": [str],
            "integrated_narrative": str,
            "contradictions_analysis": str,
            "conclusions": [str],
            "implications": [str],
            "future_directions": [str],
            "limitations": [str],
            "confidence_level": str,
        }
    }

    prompt = compose_prompt([system_prompt, task])
    return add_output_format(prompt, schema)


def generate_conclusion_prompt(synthesis: dict[str, Any]) -> str:
    """
    Generate prompt for drawing research conclusions.

    Pure function that creates conclusion prompt.

    Args:
        synthesis: Synthesis results

    Returns:
        Formatted prompt for conclusions
    """
    system_prompt = create_system_prompt(
        role="expert research analyst",
        context="You specialize in drawing evidence-based conclusions and actionable recommendations from research.",
        constraints=[
            "Base conclusions strictly on available evidence",
            "Clearly distinguish between findings and interpretations",
            "Acknowledge limitations and uncertainties",
            "Provide specific, actionable recommendations",
            "Consider practical implications and feasibility",
        ],
    )

    # Extract synthesis components
    main_findings = synthesis.get("main_findings", [])
    patterns = synthesis.get("patterns", [])
    gaps = synthesis.get("gaps", [])

    findings_text = "\n".join(f"- {finding}" for finding in main_findings)
    patterns_text = "\n".join(f"- {pattern}" for pattern in patterns)
    gaps_text = "\n".join(f"- {gap}" for gap in gaps)

    task = f"""
Based on the research synthesis, please draw comprehensive conclusions and recommendations.

Main Findings:
{findings_text}

Key Patterns:
{patterns_text}

Research Gaps:
{gaps_text}

Please provide:
1. Clear, evidence-based conclusions
2. Practical recommendations
3. Implementation considerations
4. Future research directions
5. Limitations and caveats
"""

    schema = {
        "conclusions": {
            "main_conclusions": [str],
            "recommendations": [str],
            "implementation_steps": [str],
            "future_research": [str],
            "limitations": [str],
            "practical_implications": [str],
            "confidence_assessment": str,
        }
    }

    prompt = compose_prompt([system_prompt, task])
    return add_output_format(prompt, schema)
