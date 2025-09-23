"""
AI Brain Integration Module

Integration components that connect different AI Brain subsystems:
- MASR Router to Supervisor coordination
- Multi-domain supervisor orchestration
- Cross-system feedback and learning
"""

from .masr_supervisor_bridge import (
    MASRSupervisorBridge,
    RoutingDecisionTranslator,
    SupervisorExecutor,
    ResourcePool,
    SupervisorExecutionResult,
)

__all__ = [
    "MASRSupervisorBridge",
    "RoutingDecisionTranslator",
    "SupervisorExecutor", 
    "ResourcePool",
    "SupervisorExecutionResult",
]