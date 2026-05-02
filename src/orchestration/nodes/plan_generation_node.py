"""
Plan generation node for creating research execution plans.

This node generates a comprehensive research plan based on the query analysis,
determining which agents to use, in what order, and with what parameters.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from src.orchestration.state import (
    AgentExecutionStatus,
    AgentTaskState,
    ResearchState,
)

logger = logging.getLogger(__name__)


async def plan_generation_node(state: ResearchState) -> ResearchState:
    """
    Generate a research plan based on query analysis.

    This node:
    1. Creates a structured research plan
    2. Determines which agents to activate
    3. Defines agent execution order and dependencies
    4. Sets quality criteria and validation rules

    Args:
        state: Current workflow state

    Returns:
        Updated state with research plan
    """
    logger.info("Generating research plan")

    try:
        # Get query analysis from context
        query_analysis = state.context.get("query_analysis", {})

        # Generate research plan based on analysis
        research_plan = create_research_plan(
            query=state.query,
            domains=state.domains,
            complexity=query_analysis.get("complexity", "moderate"),
            research_approach=query_analysis.get("research_approach", "comprehensive"),
            query_type=query_analysis.get("query_type", "exploratory"),
        )

        # Store plan in state
        state.research_plan = research_plan

        # Create agent tasks based on plan
        agent_tasks = create_agent_tasks(research_plan, state.project_id)

        # Add tasks to state
        for task in agent_tasks:
            state.add_agent_task(task)

        # Set quality criteria
        state.context["quality_criteria"] = define_quality_criteria(research_plan)

        # Set validation rules
        state.context["validation_rules"] = define_validation_rules(research_plan)

        logger.info(f"Research plan generated with {len(agent_tasks)} agent tasks")

    except Exception as e:
        logger.error(f"Error in plan generation: {e}")
        state.validation_errors.append(f"Plan generation failed: {e!s}")
        state.error_count += 1

    return state


def create_research_plan(
    query: str,
    domains: list[str],
    complexity: str,
    research_approach: str,
    query_type: str,
) -> dict[str, Any]:
    """
    Create a comprehensive research plan.

    Args:
        query: Research query
        domains: Research domains
        complexity: Query complexity
        research_approach: Selected approach
        query_type: Type of query

    Returns:
        Research plan dictionary
    """
    plan = {
        "plan_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "query": query,
        "domains": domains,
        "complexity": complexity,
        "research_approach": research_approach,
        "query_type": query_type,
        "phases": [],
        "agents": [],
        "dependencies": {},
        "estimated_duration": estimate_duration(complexity),
        "quality_targets": {
            "minimum_sources": get_minimum_sources(complexity),
            "confidence_threshold": 0.7,
            "validation_required": complexity in ["complex", "moderate"],
        },
    }

    # Define research phases based on approach
    if research_approach == "comprehensive":
        plan["phases"] = [
            {
                "name": "literature_review",
                "description": "Comprehensive literature review across domains",
                "agents": ["literature_review"],
                "parallel": False,
            },
            {
                "name": "analysis",
                "description": "Comparative and methodological analysis",
                "agents": ["comparative_analysis", "methodology"],
                "parallel": True,
            },
            {
                "name": "synthesis",
                "description": "Synthesize findings and generate insights",
                "agents": ["synthesis"],
                "parallel": False,
            },
            {
                "name": "validation",
                "description": "Citation verification and quality check",
                "agents": ["citation_verification"],
                "parallel": False,
            },
        ]
        plan["agents"] = [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation_verification",
        ]

    elif research_approach == "systematic_review":
        plan["phases"] = [
            {
                "name": "literature_review",
                "description": "Systematic literature review",
                "agents": ["literature_review"],
                "parallel": False,
            },
            {
                "name": "methodology",
                "description": "Methodological assessment",
                "agents": ["methodology"],
                "parallel": False,
            },
            {
                "name": "synthesis",
                "description": "Evidence synthesis",
                "agents": ["synthesis"],
                "parallel": False,
            },
            {
                "name": "validation",
                "description": "Quality and citation check",
                "agents": ["citation_verification"],
                "parallel": False,
            },
        ]
        plan["agents"] = [
            "literature_review",
            "methodology",
            "synthesis",
            "citation_verification",
        ]

    elif research_approach == "comparative_analysis":
        plan["phases"] = [
            {
                "name": "literature_review",
                "description": "Focused literature review",
                "agents": ["literature_review"],
                "parallel": False,
            },
            {
                "name": "comparison",
                "description": "Comparative analysis",
                "agents": ["comparative_analysis"],
                "parallel": False,
            },
            {
                "name": "synthesis",
                "description": "Synthesize comparisons",
                "agents": ["synthesis"],
                "parallel": False,
            },
        ]
        plan["agents"] = ["literature_review", "comparative_analysis", "synthesis"]

    else:  # focused_review
        plan["phases"] = [
            {
                "name": "literature_review",
                "description": "Focused literature review",
                "agents": ["literature_review"],
                "parallel": False,
            },
            {
                "name": "synthesis",
                "description": "Synthesize findings",
                "agents": ["synthesis"],
                "parallel": False,
            },
        ]
        plan["agents"] = ["literature_review", "synthesis"]

    # Define agent dependencies
    agents_list = plan.get("agents", [])
    if isinstance(agents_list, list):
        plan["dependencies"] = create_dependencies(agents_list)

    return plan


def create_agent_tasks(plan: dict[str, Any], project_id: str) -> list[AgentTaskState]:
    """
    Create agent tasks from the research plan.

    Args:
        plan: Research plan
        project_id: Project identifier

    Returns:
        List of agent tasks
    """
    tasks = []

    for agent_type in plan["agents"]:
        task = AgentTaskState(
            task_id=f"{project_id}-{agent_type}-{uuid.uuid4().hex[:8]}",
            agent_type=agent_type,
            status=AgentExecutionStatus.PENDING,
            input_data={
                "query": plan["query"],
                "domains": plan["domains"],
                "phase": get_agent_phase(agent_type),
                "requirements": get_agent_requirements(agent_type, plan),
            },
        )
        tasks.append(task)

    return tasks


def create_dependencies(agents: list[str]) -> dict[str, list[str]]:
    """
    Create agent dependency graph.

    Args:
        agents: List of agent types

    Returns:
        Dependency mapping
    """
    dependencies: dict[str, list[str]] = {}

    # Define standard dependencies
    if "literature_review" in agents:
        dependencies["literature_review"] = []

    if "comparative_analysis" in agents:
        dependencies["comparative_analysis"] = ["literature_review"]

    if "methodology" in agents:
        dependencies["methodology"] = ["literature_review"]

    if "synthesis" in agents:
        deps = ["literature_review"]
        if "comparative_analysis" in agents:
            deps.append("comparative_analysis")
        if "methodology" in agents:
            deps.append("methodology")
        dependencies["synthesis"] = deps

    if "citation_verification" in agents:
        dependencies["citation_verification"] = ["synthesis"]

    return dependencies


def get_agent_phase(agent_type: str) -> str:
    """
    Get the workflow phase for an agent type.

    Args:
        agent_type: Type of agent

    Returns:
        Phase name
    """
    phase_mapping = {
        "literature_review": "literature_review",
        "comparative_analysis": "analysis",
        "methodology": "analysis",
        "synthesis": "synthesis",
        "citation_verification": "validation",
    }

    return phase_mapping.get(agent_type, "processing")


def get_agent_requirements(agent_type: str, plan: dict[str, Any]) -> dict[str, Any]:
    """
    Get specific requirements for an agent based on the plan.

    Args:
        agent_type: Type of agent
        plan: Research plan

    Returns:
        Agent-specific requirements
    """
    requirements = {
        "quality_targets": plan.get("quality_targets", {}),
        "complexity": plan.get("complexity", "moderate"),
    }

    # Agent-specific requirements
    if agent_type == "literature_review":
        requirements.update(
            {
                "minimum_sources": plan["quality_targets"]["minimum_sources"],
                "search_depth": (
                    "comprehensive" if plan["complexity"] == "complex" else "standard"
                ),
                "include_grey_literature": plan["complexity"] == "complex",
            }
        )

    elif agent_type == "comparative_analysis":
        requirements.update(
            {
                "comparison_framework": "systematic",
                "include_metrics": True,
                "visualization_required": plan["complexity"] in ["complex", "moderate"],
            }
        )

    elif agent_type == "methodology":
        requirements.update(
            {
                "assess_bias": True,
                "evaluate_validity": True,
                "suggest_improvements": plan["research_approach"]
                == "systematic_review",
            }
        )

    elif agent_type == "synthesis":
        requirements.update(
            {
                "synthesis_approach": (
                    "thematic" if plan["query_type"] == "exploratory" else "narrative"
                ),
                "include_limitations": True,
                "generate_recommendations": True,
            }
        )

    elif agent_type == "citation_verification":
        requirements.update(
            {
                "verify_sources": True,
                "check_citations": True,
                "format_style": "APA",
                "check_plagiarism": plan["complexity"] == "complex",
            }
        )

    return requirements


def define_quality_criteria(plan: dict[str, Any]) -> dict[str, Any]:
    """
    Define quality criteria for the research.

    Args:
        plan: Research plan

    Returns:
        Quality criteria
    """
    return {
        "completeness": {
            "all_agents_executed": True,
            "minimum_agent_success_rate": 0.8,
        },
        "accuracy": {
            "citation_verification_required": True,
            "fact_checking_enabled": plan["complexity"] in ["complex", "moderate"],
        },
        "depth": {
            "minimum_sources": plan["quality_targets"]["minimum_sources"],
            "analysis_depth": plan["complexity"],
        },
        "coherence": {"synthesis_quality_check": True, "logical_flow_validation": True},
    }


def define_validation_rules(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Define validation rules for the research output.

    Args:
        plan: Research plan

    Returns:
        List of validation rules
    """
    rules = [
        {
            "name": "minimum_sources",
            "type": "count",
            "field": "sources",
            "operator": ">=",
            "value": plan["quality_targets"]["minimum_sources"],
            "severity": "error",
        },
        {
            "name": "agent_completion",
            "type": "completion",
            "required_agents": plan["agents"],
            "minimum_success_rate": 0.8,
            "severity": "error",
        },
        {
            "name": "citation_format",
            "type": "format",
            "field": "citations",
            "format": "APA",
            "severity": "warning",
        },
    ]

    if plan["complexity"] == "complex":
        rules.extend(
            [
                {
                    "name": "methodology_validation",
                    "type": "presence",
                    "field": "methodology_assessment",
                    "required": True,
                    "severity": "error",
                },
                {
                    "name": "comparative_analysis",
                    "type": "presence",
                    "field": "comparative_results",
                    "required": "comparative_analysis" in plan["agents"],
                    "severity": "warning",
                },
            ]
        )

    return rules


def estimate_duration(complexity: str) -> int:
    """
    Estimate research duration in minutes based on complexity.

    Args:
        complexity: Query complexity

    Returns:
        Estimated duration in minutes
    """
    duration_map = {"simple": 10, "moderate": 20, "complex": 30}

    return duration_map.get(complexity, 20)


def get_minimum_sources(complexity: str) -> int:
    """
    Get minimum number of sources based on complexity.

    Args:
        complexity: Query complexity

    Returns:
        Minimum number of sources
    """
    source_map = {"simple": 5, "moderate": 10, "complex": 20}

    return source_map.get(complexity, 10)


__all__ = ["plan_generation_node"]
