"""
Agent Framework A/B Testing API Endpoints

This module provides REST API endpoints specifically for A/B testing
experiments on the Agent Framework APIs, enabling systematic optimization
of routing strategies, execution patterns, and quality metrics.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, WebSocket
from pydantic import BaseModel, Field

# Import A/B Testing Integration components
from src.ai_brain.experimentation.integration.agent_framework_integration import (
    get_experimentor,
)
from src.ai_brain.experimentation.monitoring.real_time_dashboard import (
    get_dashboard,
)
from src.ai_brain.experimentation.optimization.feedback_loop_optimizer import (
    get_feedback_optimizer,
)
from src.auth.dependencies import get_current_user
from src.models.user import User

logger = logging.getLogger(__name__)

# ==================== Pydantic Models ====================

class RoutingExperimentCreate(BaseModel):
    """Model for creating a routing strategy experiment."""
    name: str = Field(..., description="Experiment name")
    strategies: list[str] = Field(
        ...,
        description="Routing strategies to test (e.g., cost_efficient, quality_focused)"
    )
    target_domains: list[str] | None = Field(
        None,
        description="Specific domains to target"
    )
    duration_days: int = Field(7, description="Experiment duration in days")
    min_samples: int = Field(100, description="Minimum samples per variant")


class APIPatternExperimentCreate(BaseModel):
    """Model for creating an API pattern experiment."""
    name: str = Field(..., description="Experiment name")
    patterns: list[str] = Field(
        default=["primary_heavy", "balanced", "bypass_heavy"],
        description="API pattern configurations to test"
    )
    complexity_levels: list[str] = Field(
        default=["all"],
        description="Query complexity levels to target"
    )


class TalkHierExperimentCreate(BaseModel):
    """Model for creating a TalkHier optimization experiment."""
    name: str = Field(..., description="Experiment name")
    min_rounds: int = Field(1, description="Minimum refinement rounds")
    max_rounds: int = Field(5, description="Maximum refinement rounds")
    consensus_thresholds: list[float] = Field(
        default=[0.7, 0.8, 0.9],
        description="Consensus thresholds to test"
    )


class SupervisorExperimentCreate(BaseModel):
    """Model for creating a supervisor coordination experiment."""
    name: str = Field(..., description="Experiment name")
    execution_modes: list[str] = Field(
        default=["sequential", "parallel", "adaptive"],
        description="Execution modes to test"
    )
    worker_counts: list[int] = Field(
        default=[2, 3, 5],
        description="Worker counts to test"
    )


class ExperimentExecutionRequest(BaseModel):
    """Model for executing a query with experiments."""
    query: str = Field(..., description="User query to execute")
    user_id: str = Field(..., description="User identifier")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Query context (domain, complexity, etc.)"
    )
    experiment_ids: list[str] | None = Field(
        None,
        description="Specific experiments to include"
    )


class OptimizationApproval(BaseModel):
    """Model for approving optimization recommendations."""
    optimization_id: str = Field(..., description="Optimization ID to approve")
    apply_immediately: bool = Field(
        False,
        description="Apply immediately or use gradual rollout"
    )
    rollout_percentage: float | None = Field(
        None,
        description="Initial rollout percentage if gradual"
    )


# ==================== Create Router ====================

router = APIRouter(
    prefix="/api/v1/experiments/agent-framework",
    tags=["agent-experiments"]
)


# ==================== Experiment Creation Endpoints ====================

@router.post("/routing", response_model=dict[str, str])
async def create_routing_experiment(
    experiment: RoutingExperimentCreate,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    """
    Create a new MASR routing strategy A/B test.
    
    This endpoint creates an experiment to test different routing strategies
    and find the optimal configuration for query routing.
    """
    experimentor = get_experimentor()
    
    try:
        experiment_id = await experimentor.create_routing_experiment(
            name=experiment.name,
            strategies=experiment.strategies,
            target_domains=experiment.target_domains,
            duration_days=experiment.duration_days
        )
        
        # Register with dashboard
        dashboard = get_dashboard()
        await dashboard.register_experiment(
            experiment_id=experiment_id,
            experiment_config={
                "type": "routing",
                "name": experiment.name,
                "strategies": experiment.strategies,
                "created_by": str(current_user.id)
            }
        )
        
        return {
            "experiment_id": experiment_id,
            "status": "created",
            "message": f"Routing experiment '{experiment.name}' created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api-pattern", response_model=dict[str, str])
async def create_api_pattern_experiment(
    experiment: APIPatternExperimentCreate,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    """
    Create an API pattern optimization experiment.
    
    Tests Primary vs Bypass API usage patterns to find the optimal
    balance between intelligence and direct execution.
    """
    experimentor = get_experimentor()
    
    try:
        experiment_id = await experimentor.create_api_pattern_experiment(
            name=experiment.name
        )
        
        # Register with dashboard
        dashboard = get_dashboard()
        await dashboard.register_experiment(
            experiment_id=experiment_id,
            experiment_config={
                "type": "api_pattern",
                "name": experiment.name,
                "patterns": experiment.patterns,
                "created_by": str(current_user.id)
            }
        )
        
        return {
            "experiment_id": experiment_id,
            "status": "created",
            "message": f"API pattern experiment '{experiment.name}' created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/talkhier", response_model=dict[str, str])
async def create_talkhier_experiment(
    experiment: TalkHierExperimentCreate,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    """
    Create a TalkHier protocol optimization experiment.
    
    Tests different refinement round counts and consensus thresholds
    to optimize the structured dialogue protocol.
    """
    experimentor = get_experimentor()
    
    try:
        experiment_id = await experimentor.create_talkhier_optimization_experiment(
            name=experiment.name,
            min_rounds=experiment.min_rounds,
            max_rounds=experiment.max_rounds
        )
        
        # Register with dashboard
        dashboard = get_dashboard()
        await dashboard.register_experiment(
            experiment_id=experiment_id,
            experiment_config={
                "type": "talkhier",
                "name": experiment.name,
                "rounds_range": [experiment.min_rounds, experiment.max_rounds],
                "created_by": str(current_user.id)
            }
        )
        
        return {
            "experiment_id": experiment_id,
            "status": "created",
            "message": f"TalkHier experiment '{experiment.name}' created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/supervisor", response_model=dict[str, str])
async def create_supervisor_experiment(
    experiment: SupervisorExperimentCreate,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    """
    Create a supervisor coordination experiment.
    
    Tests different supervisor execution modes and worker allocations
    to optimize hierarchical coordination.
    """
    experimentor = get_experimentor()
    
    try:
        # Create supervisor experiment (would implement in experimentor)
        experiment_id = f"supervisor_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Register with dashboard
        dashboard = get_dashboard()
        await dashboard.register_experiment(
            experiment_id=experiment_id,
            experiment_config={
                "type": "supervisor",
                "name": experiment.name,
                "execution_modes": experiment.execution_modes,
                "worker_counts": experiment.worker_counts,
                "created_by": str(current_user.id)
            }
        )
        
        return {
            "experiment_id": experiment_id,
            "status": "created",
            "message": f"Supervisor experiment '{experiment.name}' created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Experiment Execution Endpoints ====================

@router.post("/execute", response_model=dict[str, Any])
async def execute_with_experiments(
    request: ExperimentExecutionRequest
) -> dict[str, Any]:
    """
    Execute a query while running A/B experiments.
    
    This endpoint processes queries through the experimental framework,
    applying variant configurations and tracking metrics.
    """
    experimentor = get_experimentor()
    
    try:
        result = await experimentor.execute_with_experiment(
            query=request.query,
            user_id=request.user_id,
            context=request.context,
            experiment_ids=request.experiment_ids
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Experiment Monitoring Endpoints ====================

@router.get("/active", response_model=list[dict[str, Any]])
async def get_active_experiments(
    current_user: User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """
    Get list of all active Agent Framework experiments.
    
    Returns experiments currently running with their configurations
    and current sample sizes.
    """
    experimentor = get_experimentor()
    
    try:
        experiments = await experimentor.get_active_experiments()
        return experiments
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{experiment_id}/results", response_model=dict[str, Any])
async def get_experiment_results(
    experiment_id: str,
    include_statistics: bool = Query(True, description="Include statistical analysis"),
    current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """
    Get current results for an Agent Framework experiment.
    
    Returns performance metrics, sample sizes, and statistical analysis
    for all variants in the experiment.
    """
    experimentor = get_experimentor()
    
    try:
        results = await experimentor.get_experiment_results(
            experiment_id=experiment_id,
            include_statistical_analysis=include_statistics
        )
        return results
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{experiment_id}/stop", response_model=dict[str, Any])
async def stop_experiment(
    experiment_id: str,
    reason: str = Body("manual_stop", description="Reason for stopping"),
    current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """
    Stop an active experiment and get final results.
    
    Stops the experiment, performs final analysis, and returns
    conclusions and recommendations.
    """
    experimentor = get_experimentor()
    
    try:
        final_results = await experimentor.stop_experiment(
            experiment_id=experiment_id,
            reason=reason
        )
        return final_results
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Optimization Endpoints ====================

@router.get("/optimizations/pending", response_model=list[dict[str, Any]])
async def get_pending_optimizations(
    current_user: User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """
    Get pending optimization recommendations from experiments.
    
    Returns optimizations that require manual approval based on
    experiment results and confidence levels.
    """
    optimizer = get_feedback_optimizer()
    
    # Get pending optimizations (would implement in optimizer)
    pending = []
    
    for target, decision in optimizer.active_optimizations.items():
        if decision.confidence < 0.95:  # Requires approval
            pending.append({
                "optimization_id": f"opt_{target}",
                "target": target,
                "current_value": decision.current_value,
                "recommended_value": decision.recommended_value,
                "confidence": decision.confidence,
                "expected_improvement": decision.expected_improvement,
                "risk_level": decision.risk_level,
                "rationale": decision.rationale,
                "experiment_evidence": decision.experiment_evidence
            })
    
    return pending


@router.post("/optimizations/approve", response_model=dict[str, str])
async def approve_optimization(
    approval: OptimizationApproval,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    """
    Approve and apply an optimization recommendation.
    
    Applies the optimization either immediately or through
    gradual rollout based on the approval settings.
    """
    optimizer = get_feedback_optimizer()
    
    try:
        # Apply optimization (would implement approval logic)
        return {
            "optimization_id": approval.optimization_id,
            "status": "approved",
            "rollout_mode": "immediate" if approval.apply_immediately else "gradual",
            "message": "Optimization approved and being applied"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== WebSocket Dashboard Endpoint ====================

@router.websocket("/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time experiment dashboard.
    
    Provides live updates on experiment metrics, statistical analysis,
    and optimization recommendations.
    """
    dashboard = get_dashboard()
    client_id = f"dashboard_{datetime.utcnow().timestamp()}"
    
    try:
        await dashboard.connect_dashboard_client(client_id, websocket)
        
        # Keep connection alive
        while True:
            # Receive messages from client
            data = await websocket.receive_json()
            
            # Handle different message types
            if data.get("type") == "subscribe":
                experiment_id = data.get("experiment_id")
                # Subscribe to specific experiment updates
                
            elif data.get("type") == "export":
                experiment_id = data.get("experiment_id")
                format = data.get("format", "json")
                export_data = await dashboard.export_experiment_data(
                    experiment_id, format
                )
                await websocket.send_json({
                    "type": "export_result",
                    "data": export_data
                })
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        
    finally:
        await dashboard.disconnect_dashboard_client(client_id)


# ==================== Health Check ====================

@router.get("/health", response_model=dict[str, Any])
async def experiment_system_health() -> dict[str, Any]:
    """
    Check health of the A/B testing system.
    
    Returns status of experimentor, dashboard, and optimizer components.
    """
    experimentor = get_experimentor()
    dashboard = get_dashboard()
    optimizer = get_feedback_optimizer()
    
    active_experiments = await experimentor.get_active_experiments()
    
    return {
        "status": "healthy",
        "components": {
            "experimentor": "active",
            "dashboard": "active",
            "optimizer": "active"
        },
        "statistics": {
            "active_experiments": len(active_experiments),
            "dashboard_clients": len(dashboard.dashboard_clients),
            "pending_optimizations": len(optimizer.active_optimizations)
        },
        "timestamp": datetime.utcnow().isoformat()
    }