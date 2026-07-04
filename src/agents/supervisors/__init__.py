"""
Supervisor Agents Package

Implements hierarchical supervisor agents that coordinate teams of specialized
worker agents using LangGraph orchestration and TalkHier communication protocols.

Supervisor Types:
- BaseSupervisor: Abstract base for all supervisor agents
- ResearchSupervisor: Coordinates research teams (literature, methodology, synthesis)
- ContentSupervisor: Coordinates content creation teams (strategy, writing, editing)
- AnalyticsSupervisor: Coordinates analytics teams (data collection, analysis, insights)

Integration:
- LangGraph: State management and workflow orchestration
- TalkHier: Multi-round refinement and consensus building
- MASR: Intelligent routing and resource allocation
"""

from typing import Any

from .base_supervisor import BaseSupervisor
from .research_supervisor import ResearchSupervisor

# AnalyticsSupervisor and ContentSupervisor are planned but not yet implemented.
# Use lazy imports to avoid crashing when their modules don't exist.


def __getattr__(name: str) -> Any:
    if name == "AnalyticsSupervisor":
        from .analytics_supervisor import AnalyticsSupervisor

        return AnalyticsSupervisor
    if name == "ContentSupervisor":
        from .content_supervisor import ContentSupervisor

        return ContentSupervisor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AnalyticsSupervisor",
    "BaseSupervisor",
    "ContentSupervisor",
    "ResearchSupervisor",
]
