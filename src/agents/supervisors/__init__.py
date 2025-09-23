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

from .base_supervisor import BaseSupervisor
from .research_supervisor import ResearchSupervisor
from .content_supervisor import ContentSupervisor
from .analytics_supervisor import AnalyticsSupervisor

__all__ = [
    "BaseSupervisor",
    "ResearchSupervisor",
    "ContentSupervisor",
    "AnalyticsSupervisor",
]
