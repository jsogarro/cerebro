"""
Supervisor API Models - Request and Response schemas for Hierarchical Supervisor API

This module implements comprehensive data models for the Supervisor API endpoints,
following the "Talk Structurally, Act Hierarchically" research patterns for
hierarchical agent coordination.
"""

from typing import Dict, List, Optional, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
import uuid


# Enums for Supervisor API

class SupervisorType(str, Enum):
    """Available supervisor types based on domain specialization"""
    RESEARCH = "research"
    CONTENT = "content"
    ANALYTICS = "analytics"
    SERVICE = "service"
    GENERAL = "general"


class WorkerStatus(str, Enum):
    """Worker agent status in supervisor coordination"""
    IDLE = "idle"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class CoordinationMode(str, Enum):
    """Coordination modes for supervisor-worker interaction"""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    HIERARCHICAL = "hierarchical"
    ADAPTIVE = "adaptive"
    DEBATE = "debate"


class SupervisionStrategy(str, Enum):
    """Supervision strategies based on task requirements"""
    DIRECT = "direct"               # Simple direct supervision
    COLLABORATIVE = "collaborative" # Workers collaborate with supervisor guidance
    AUTONOMOUS = "autonomous"       # Workers operate independently
    ITERATIVE = "iterative"        # Multiple refinement rounds
    CONSENSUS = "consensus"        # Consensus-based decision making


class ConflictResolutionStrategy(str, Enum):
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
    supervision_strategy: Optional[SupervisionStrategy] = Field(
        SupervisionStrategy.COLLABORATIVE,
        description="Strategy for supervisor-worker interaction"
    )
    coordination_mode: Optional[CoordinationMode] = Field(
        CoordinationMode.HIERARCHICAL,
        description="Mode of worker coordination"
    )
    max_workers: Optional[int] = Field(5, ge=1, le=20, description="Maximum number of workers to allocate")
    quality_threshold: Optional[float] = Field(0.8, ge=0.0, le=1.0, description="Minimum quality threshold")
    timeout_seconds: Optional[int] = Field(120, ge=10, le=600, description="Execution timeout in seconds")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional parameters")
    context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Execution context")


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
    worker_types: List[str] = Field(..., description="Types of workers to coordinate")
    coordination_mode: CoordinationMode = Field(..., description="How to coordinate workers")
    refinement_rounds: Optional[int] = Field(1, ge=1, le=5, description="Number of refinement rounds")
    conflict_resolution: Optional[ConflictResolutionStrategy] = Field(
        ConflictResolutionStrategy.SUPERVISOR_OVERRIDE,
        description="Strategy for conflict resolution"
    )
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)


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
    supervisor_types: List[SupervisorType] = Field(..., description="Supervisors to involve")
    orchestration_strategy: Optional[str] = Field(
        "collaborative",
        description="How supervisors should work together"
    )
    synthesis_required: Optional[bool] = Field(
        True,
        description="Whether to synthesize results across supervisors"
    )
    priority_weights: Optional[Dict[str, float]] = Field(
        default_factory=dict,
        description="Priority weights for each supervisor's contribution"
    )
    timeout_seconds: Optional[int] = Field(180, ge=30, le=600)


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
    
    task_requirements: Dict[str, Any] = Field(..., description="Task requirements and constraints")
    available_workers: int = Field(..., ge=1, le=50, description="Number of available workers")
    optimization_goal: Literal["quality", "speed", "cost", "balanced"] = Field(
        "balanced",
        description="What to optimize for"
    )
    constraints: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional constraints")


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
    worker_outputs: List[Dict[str, Any]] = Field(..., description="Conflicting worker outputs")
    resolution_strategy: ConflictResolutionStrategy = Field(..., description="How to resolve the conflict")
    supervisor_guidance: Optional[str] = Field(None, description="Optional supervisor guidance")


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
    strategies_to_test: List[SupervisionStrategy] = Field(..., description="Strategies to compare")
    metrics_to_track: List[str] = Field(..., description="Metrics to measure")
    repetitions: Optional[int] = Field(1, ge=1, le=10, description="Number of test repetitions")


# Response Models

class WorkerInfo(BaseModel):
    """Information about a worker agent"""
    worker_id: str = Field(..., description="Unique worker identifier")
    worker_type: str = Field(..., description="Type of worker agent")
    status: WorkerStatus = Field(..., description="Current worker status")
    capabilities: List[str] = Field(default_factory=list, description="Worker capabilities")
    performance_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Performance rating")
    current_task: Optional[str] = Field(None, description="Currently assigned task")


class SupervisorInfo(BaseModel):
    """Information about a supervisor"""
    supervisor_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisor_type: SupervisorType
    status: str = Field("active", description="Supervisor status")
    capabilities: List[str] = Field(default_factory=list)
    worker_count: int = Field(0, description="Number of workers managed")
    active_tasks: int = Field(0, description="Number of active tasks")
    performance_metrics: Dict[str, float] = Field(default_factory=dict)


class SupervisorExecuteResponse(BaseModel):
    """Response from supervisor task execution"""
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisor_type: SupervisorType
    status: str = Field(..., description="Execution status")
    result: Optional[Any] = Field(None, description="Execution result")
    workers_used: List[str] = Field(default_factory=list, description="Workers that participated")
    coordination_mode: CoordinationMode
    quality_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    execution_time_ms: Optional[int] = Field(None, description="Execution time in milliseconds")
    refinement_rounds: int = Field(1, description="Number of refinement rounds performed")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkerCoordinationResponse(BaseModel):
    """Response from worker coordination"""
    coordination_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workers_assigned: List[WorkerInfo]
    coordination_plan: Dict[str, Any] = Field(..., description="Coordination execution plan")
    estimated_completion_time: Optional[int] = Field(None, description="Estimated time in seconds")
    status: str = Field("initiated", description="Coordination status")


class MultiSupervisorOrchestrationResponse(BaseModel):
    """Response from multi-supervisor orchestration"""
    orchestration_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisors_involved: List[SupervisorInfo]
    individual_results: Dict[str, Any] = Field(..., description="Results from each supervisor")
    synthesized_result: Optional[Any] = Field(None, description="Synthesized cross-domain result")
    orchestration_time_ms: int
    consensus_achieved: bool = Field(False, description="Whether supervisors reached consensus")
    quality_metrics: Dict[str, float] = Field(default_factory=dict)


class SupervisorStatsResponse(BaseModel):
    """Performance statistics for a supervisor"""
    supervisor_type: SupervisorType
    total_executions: int = Field(0, description="Total number of executions")
    success_rate: float = Field(0.0, ge=0.0, le=1.0, description="Success rate")
    average_execution_time_ms: float = Field(0.0, description="Average execution time")
    average_quality_score: float = Field(0.0, ge=0.0, le=1.0, description="Average quality")
    worker_utilization: float = Field(0.0, ge=0.0, le=1.0, description="Worker utilization rate")
    top_worker_types: List[str] = Field(default_factory=list, description="Most used worker types")
    recent_performance_trend: str = Field("stable", description="Performance trend")
    cost_metrics: Dict[str, float] = Field(default_factory=dict)


class SupervisorHealthResponse(BaseModel):
    """Health status of a supervisor"""
    supervisor_type: SupervisorType
    status: Literal["healthy", "degraded", "unhealthy"]
    health_score: float = Field(..., ge=0.0, le=1.0, description="Overall health score")
    active_workers: int = Field(0, description="Number of active workers")
    queue_depth: int = Field(0, description="Number of queued tasks")
    last_execution: Optional[datetime] = Field(None, description="Last execution timestamp")
    issues: List[str] = Field(default_factory=list, description="Current issues if any")
    recommendations: List[str] = Field(default_factory=list, description="Health recommendations")


class WorkerAllocationOptimizationResponse(BaseModel):
    """Response from worker allocation optimization"""
    optimization_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recommended_allocation: Dict[str, int] = Field(..., description="Recommended worker allocation")
    expected_performance: Dict[str, float] = Field(..., description="Expected performance metrics")
    optimization_score: float = Field(..., ge=0.0, le=1.0, description="Optimization quality")
    reasoning: str = Field(..., description="Explanation of allocation decision")
    alternative_allocations: Optional[List[Dict[str, Any]]] = Field(None)


class ConflictResolutionResponse(BaseModel):
    """Response from conflict resolution"""
    conflict_id: str
    resolution_strategy: ConflictResolutionStrategy
    resolved_output: Any = Field(..., description="Resolved output after conflict resolution")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in resolution")
    resolution_reasoning: str = Field(..., description="Explanation of resolution")
    worker_consensus: Optional[Dict[str, float]] = Field(None, description="Worker agreement levels")


class SupervisorComparisonResponse(BaseModel):
    """Response comparing supervisor performance"""
    comparison_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supervisors_compared: List[SupervisorType]
    performance_metrics: Dict[str, Dict[str, float]] = Field(
        ...,
        description="Performance metrics for each supervisor"
    )
    rankings: Dict[str, List[str]] = Field(..., description="Rankings by different criteria")
    recommendations: Dict[str, str] = Field(..., description="Usage recommendations")
    visualization_data: Optional[Dict[str, Any]] = Field(None, description="Data for visualization")


class ExperimentResponse(BaseModel):
    """Response from coordination strategy experiment"""
    experiment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategies_tested: List[SupervisionStrategy]
    results: Dict[str, Dict[str, float]] = Field(..., description="Results for each strategy")
    best_strategy: SupervisionStrategy = Field(..., description="Best performing strategy")
    statistical_significance: Optional[float] = Field(None, description="Statistical significance of results")
    recommendations: List[str] = Field(default_factory=list, description="Strategy recommendations")
    detailed_analysis: Optional[Dict[str, Any]] = Field(None, description="Detailed analysis data")


# List Response Models

class SupervisorListResponse(BaseModel):
    """Response listing all available supervisors"""
    supervisors: List[SupervisorInfo]
    total_count: int
    active_count: int
    available_count: int


class WorkerListResponse(BaseModel):
    """Response listing workers for a supervisor"""
    supervisor_type: SupervisorType
    workers: List[WorkerInfo]
    total_workers: int
    active_workers: int
    idle_workers: int


# WebSocket Event Models

class SupervisorWebSocketEvent(BaseModel):
    """WebSocket event for supervisor real-time updates"""
    event_type: Literal["status_update", "task_assigned", "task_completed", "worker_update", "performance_alert"]
    supervisor_type: SupervisorType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any]
    priority: Literal["low", "medium", "high"] = "medium"


class WorkerCoordinationProgressEvent(BaseModel):
    """WebSocket event for worker coordination progress"""
    coordination_id: str
    event_type: Literal["started", "worker_assigned", "progress", "conflict_detected", "completed"]
    progress_percentage: float = Field(0.0, ge=0.0, le=100.0)
    current_phase: str
    workers_active: int
    estimated_remaining_seconds: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


# Error Response Model

class SupervisorErrorResponse(BaseModel):
    """Error response for supervisor API"""
    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    supervisor_type: Optional[SupervisorType] = None
    request_id: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list, description="Suggestions to resolve the error")