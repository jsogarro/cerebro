"""
Supervisor API Models - Request and Response schemas for Hierarchical Supervisor API

This module implements comprehensive data models for the Supervisor API endpoints,
following the "Talk Structurally, Act Hierarchically" research patterns for
hierarchical agent coordination.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Enums for Supervisor API

class SupervisorType(StrEnum):
    """Available supervisor types based on domain specialization"""
    RESEARCH = "research"
    CONTENT = "content"
    ANALYTICS = "analytics"
    SERVICE = "service"
    GENERAL = "general"


class WorkerStatus(StrEnum):
    """Worker agent status in supervisor coordination"""
    IDLE = "idle"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class CoordinationMode(StrEnum):
    """Coordination modes for supervisor-worker interaction"""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    HIERARCHICAL = "hierarchical"
    ADAPTIVE = "adaptive"
    DEBATE = "debate"


class SupervisionStrategy(StrEnum):
    """Supervision strategies based on task requirements"""
    DIRECT = "direct"               # Simple direct supervision
    COLLABORATIVE = "collaborative" # Workers collaborate with supervisor guidance
    AUTONOMOUS = "autonomous"       # Workers operate independently
    ITERATIVE = "iterative"        # Multiple refinement rounds
    CONSENSUS = "consensus"        # Consensus-based decision making


class ConflictResolutionStrategy(StrEnum):
    """Strategies for resolving conflicts between workers"""
    SUPERVISOR_OVERRIDE = "supervisor_override"
    MAJORITY_VOTE = "majority_vote"
    WEIGHTED_CONSENSUS = "weighted_consensus"
    QUALITY_BASED = "quality_based"
    DEBATE_RESOLUTION = "debate_resolution"


# Request Models

class SupervisorExecuteRequest(BaseModel):
    """Request to execute a task through a supervisor"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "Analyze the impact of AI on employment",
            "supervision_strategy": "collaborative",
            "coordination_mode": "hierarchical",
            "max_workers": 5,
            "quality_threshold": 0.85,
            "timeout_seconds": 120
        }
    })
    
    query: str = Field(..., description="The task or query to execute")
    supervision_strategy: SupervisionStrategy | None = Field(
        SupervisionStrategy.COLLABORATIVE,
        description="Strategy for supervisor-worker interaction"
    )
    coordination_mode: CoordinationMode | None = Field(
        CoordinationMode.HIERARCHICAL,
        description="Mode of worker coordination"
    )
    max_workers: int | None = Field(5, ge=1, le=20, description="Maximum number of workers to allocate")
    quality_threshold: float | None = Field(0.8, ge=0.0, le=1.0, description="Minimum quality threshold")
    timeout_seconds: int | None = Field(120, ge=10, le=600, description="Execution timeout in seconds")
    parameters: dict[str, Any] | None = Field(default_factory=dict, description="Additional parameters")
    context: dict[str, Any] | None = Field(default_factory=dict, description="Execution context")


class WorkerCoordinationRequest(BaseModel):
    """Request to coordinate workers for a specific task"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "task": "Research climate change effects",
            "worker_types": ["literature-review", "data-analysis", "synthesis"],
            "coordination_mode": "parallel",
            "refinement_rounds": 2
        }
    })
    
    task: str = Field(..., description="Task to coordinate")
    worker_types: list[str] = Field(..., description="Types of workers to coordinate")
    coordination_mode: CoordinationMode = Field(..., description="How to coordinate workers")
    refinement_rounds: int | None = Field(1, ge=1, le=5, description="Number of refinement rounds")
    conflict_resolution: ConflictResolutionStrategy | None = Field(
        ConflictResolutionStrategy.SUPERVISOR_OVERRIDE,
        description="Strategy for conflict resolution"
    )
    parameters: dict[str, Any] | None = Field(default_factory=dict)


class MultiSupervisorOrchestrationRequest(BaseModel):
    """Request for cross-domain multi-supervisor orchestration"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "Create a business plan for an AI startup",
            "supervisor_types": ["research", "content", "analytics"],
            "orchestration_strategy": "collaborative",
            "synthesis_required": True
        }
    })
    
    query: str = Field(..., description="Complex query requiring multiple supervisors")
    supervisor_types: list[SupervisorType] = Field(..., description="Supervisors to involve")
    orchestration_strategy: str | None = Field(
        "collaborative",
        description="How supervisors should work together"
    )
    synthesis_required: bool | None = Field(
        True,
        description="Whether to synthesize results across supervisors"
    )
    priority_weights: dict[str, float] | None = Field(
        default_factory=dict,
        description="Priority weights for each supervisor's contribution"
    )
    timeout_seconds: int | None = Field(180, ge=30, le=600)


class WorkerAllocationOptimizationRequest(BaseModel):
    """Request to optimize worker allocation for efficiency"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "task_requirements": {
                "complexity": 0.8,
                "domains": ["ai", "ethics"],
                "quality_target": 0.9
            },
            "available_workers": 10,
            "optimization_goal": "quality"
        }
    })
    
    task_requirements: dict[str, Any] = Field(..., description="Task requirements and constraints")
    available_workers: int = Field(..., ge=1, le=50, description="Number of available workers")
    optimization_goal: Literal["quality", "speed", "cost", "balanced"] = Field(
        "balanced",
        description="What to optimize for"
    )
    constraints: dict[str, Any] | None = Field(default_factory=dict, description="Additional constraints")


class ConflictResolutionRequest(BaseModel):
    """Request to resolve conflicts between worker outputs"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "conflict_id": "conflict-123",
            "worker_outputs": [
                {"worker_id": "w1", "output": "Result A", "confidence": 0.85},
                {"worker_id": "w2", "output": "Result B", "confidence": 0.90}
            ],
            "resolution_strategy": "quality_based"
        }
    })
    
    conflict_id: str = Field(..., description="Unique conflict identifier")
    worker_outputs: list[dict[str, Any]] = Field(..., description="Conflicting worker outputs")
    resolution_strategy: ConflictResolutionStrategy = Field(..., description="How to resolve the conflict")
    supervisor_guidance: str | None = Field(None, description="Optional supervisor guidance")


class ExperimentRequest(BaseModel):
    """Request to experiment with different coordination strategies"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "Test query for experimentation",
            "strategies_to_test": ["direct", "collaborative", "iterative"],
            "metrics_to_track": ["quality", "speed", "cost"]
        }
    })
    
    query: str = Field(..., description="Query to test with")
    strategies_to_test: list[SupervisionStrategy] = Field(..., description="Strategies to compare")
    metrics_to_track: list[str] = Field(..., description="Metrics to measure")
    repetitions: int | None = Field(1, ge=1, le=10, description="Number of test repetitions")


# Response Models

class WorkerInfo(BaseModel):
    """Information about a worker agent"""
    worker_id: str = Field(..., description="Unique worker identifier")
    worker_type: str = Field(..., description="Type of worker agent")
    status: WorkerStatus = Field(..., description="Current worker status")
    capabilities: list[str] = Field(default_factory=list, description="Worker capabilities")
    performance_score: float | None = Field(None, ge=0.0, le=1.0, description="Performance rating")
    current_task: str | None = Field(None, description="Currently assigned task")


class SupervisorInfo(BaseModel):
    """Information about a supervisor"""
    supervisor_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisor_type: SupervisorType
    status: str = Field("active", description="Supervisor status")
    capabilities: list[str] = Field(default_factory=list)
    worker_count: int = Field(0, description="Number of workers managed")
    active_tasks: int = Field(0, description="Number of active tasks")
    performance_metrics: dict[str, float] = Field(default_factory=dict)


class SupervisorExecuteResponse(BaseModel):
    """Response from supervisor task execution"""
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisor_type: SupervisorType
    status: str = Field(..., description="Execution status")
    result: Any | None = Field(None, description="Execution result")
    workers_used: list[str] = Field(default_factory=list, description="Workers that participated")
    coordination_mode: CoordinationMode
    quality_score: float | None = Field(None, ge=0.0, le=1.0)
    execution_time_ms: int | None = Field(None, description="Execution time in milliseconds")
    refinement_rounds: int = Field(1, description="Number of refinement rounds performed")
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerCoordinationResponse(BaseModel):
    """Response from worker coordination"""
    coordination_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workers_assigned: list[WorkerInfo]
    coordination_plan: dict[str, Any] = Field(..., description="Coordination execution plan")
    estimated_completion_time: int | None = Field(None, description="Estimated time in seconds")
    status: str = Field("initiated", description="Coordination status")


class MultiSupervisorOrchestrationResponse(BaseModel):
    """Response from multi-supervisor orchestration"""
    orchestration_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisors_involved: list[SupervisorInfo]
    individual_results: dict[str, Any] = Field(..., description="Results from each supervisor")
    synthesized_result: Any | None = Field(None, description="Synthesized cross-domain result")
    orchestration_time_ms: int
    consensus_achieved: bool = Field(False, description="Whether supervisors reached consensus")
    quality_metrics: dict[str, float] = Field(default_factory=dict)


class SupervisorStatsResponse(BaseModel):
    """Performance statistics for a supervisor"""
    supervisor_type: SupervisorType
    total_executions: int = Field(0, description="Total number of executions")
    success_rate: float = Field(0.0, ge=0.0, le=1.0, description="Success rate")
    average_execution_time_ms: float = Field(0.0, description="Average execution time")
    average_quality_score: float = Field(0.0, ge=0.0, le=1.0, description="Average quality")
    worker_utilization: float = Field(0.0, ge=0.0, le=1.0, description="Worker utilization rate")
    top_worker_types: list[str] = Field(default_factory=list, description="Most used worker types")
    recent_performance_trend: str = Field("stable", description="Performance trend")
    cost_metrics: dict[str, float] = Field(default_factory=dict)


class SupervisorHealthResponse(BaseModel):
    """Health status of a supervisor"""
    supervisor_type: SupervisorType
    status: Literal["healthy", "degraded", "unhealthy"]
    health_score: float = Field(..., ge=0.0, le=1.0, description="Overall health score")
    active_workers: int = Field(0, description="Number of active workers")
    queue_depth: int = Field(0, description="Number of queued tasks")
    last_execution: datetime | None = Field(None, description="Last execution timestamp")
    issues: list[str] = Field(default_factory=list, description="Current issues if any")
    recommendations: list[str] = Field(default_factory=list, description="Health recommendations")


class WorkerAllocationOptimizationResponse(BaseModel):
    """Response from worker allocation optimization"""
    optimization_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recommended_allocation: dict[str, int] = Field(..., description="Recommended worker allocation")
    expected_performance: dict[str, float] = Field(..., description="Expected performance metrics")
    optimization_score: float = Field(..., ge=0.0, le=1.0, description="Optimization quality")
    reasoning: str = Field(..., description="Explanation of allocation decision")
    alternative_allocations: list[dict[str, Any]] | None = Field(None)


class ConflictResolutionResponse(BaseModel):
    """Response from conflict resolution"""
    conflict_id: str
    resolution_strategy: ConflictResolutionStrategy
    resolved_output: Any = Field(..., description="Resolved output after conflict resolution")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in resolution")
    resolution_reasoning: str = Field(..., description="Explanation of resolution")
    worker_consensus: dict[str, float] | None = Field(None, description="Worker agreement levels")


class SupervisorComparisonResponse(BaseModel):
    """Response comparing supervisor performance"""
    comparison_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisors_compared: list[SupervisorType]
    performance_metrics: dict[str, dict[str, float]] = Field(
        ...,
        description="Performance metrics for each supervisor"
    )
    rankings: dict[str, list[str]] = Field(..., description="Rankings by different criteria")
    recommendations: dict[str, str] = Field(..., description="Usage recommendations")
    visualization_data: dict[str, Any] | None = Field(None, description="Data for visualization")


class ExperimentResponse(BaseModel):
    """Response from coordination strategy experiment"""
    experiment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategies_tested: list[SupervisionStrategy]
    results: dict[str, dict[str, float]] = Field(..., description="Results for each strategy")
    best_strategy: SupervisionStrategy = Field(..., description="Best performing strategy")
    statistical_significance: float | None = Field(None, description="Statistical significance of results")
    recommendations: list[str] = Field(default_factory=list, description="Strategy recommendations")
    detailed_analysis: dict[str, Any] | None = Field(None, description="Detailed analysis data")


# List Response Models

class SupervisorListResponse(BaseModel):
    """Response listing all available supervisors"""
    supervisors: list[SupervisorInfo]
    total_count: int
    active_count: int
    available_count: int


class WorkerListResponse(BaseModel):
    """Response listing workers for a supervisor"""
    supervisor_type: SupervisorType
    workers: list[WorkerInfo]
    total_workers: int
    active_workers: int
    idle_workers: int


# WebSocket Event Models

class SupervisorWebSocketEvent(BaseModel):
    """WebSocket event for supervisor real-time updates"""
    event_type: Literal["status_update", "task_assigned", "task_completed", "worker_update", "performance_alert"]
    supervisor_type: SupervisorType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any]
    priority: Literal["low", "medium", "high"] = "medium"


class WorkerCoordinationProgressEvent(BaseModel):
    """WebSocket event for worker coordination progress"""
    coordination_id: str
    event_type: Literal["started", "worker_assigned", "progress", "conflict_detected", "completed"]
    progress_percentage: float = Field(0.0, ge=0.0, le=100.0)
    current_phase: str
    workers_active: int
    estimated_remaining_seconds: int | None = None
    details: dict[str, Any] | None = None


# Error Response Model

class SupervisorErrorResponse(BaseModel):
    """Error response for supervisor API"""
    error_code: str
    message: str
    details: dict[str, Any] | None = None
    supervisor_type: SupervisorType | None = None
    request_id: str | None = None
    suggestions: list[str] = Field(default_factory=list, description="Suggestions to resolve the error")