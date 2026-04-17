"""
Agent API Models

Request and response models for the Agent Framework API endpoints.
Implements research-validated patterns for direct agent interaction,
Chain-of-Agents, and Mixture-of-Agents execution.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentType(StrEnum):
    """Available agent types in Cerebro."""
    
    LITERATURE_REVIEW = "literature-review"
    CITATION = "citation"
    METHODOLOGY = "methodology"
    COMPARATIVE_ANALYSIS = "comparative-analysis"
    SYNTHESIS = "synthesis"


class ExecutionMode(StrEnum):
    """Agent execution modes following research patterns."""
    
    DIRECT = "direct"              # Single agent execution
    CHAIN = "chain"               # Chain-of-Agents (sequential)
    MIXTURE = "mixture"           # Mixture-of-Agents (parallel)
    HIERARCHICAL = "hierarchical" # Supervisor-coordinated


class AgentCapability(StrEnum):
    """Agent capabilities for discovery."""
    
    DATABASE_SEARCH = "database_search"
    SOURCE_EVALUATION = "source_evaluation"
    CITATION_FORMATTING = "citation_formatting"
    RESEARCH_DESIGN = "research_design"
    BIAS_DETECTION = "bias_detection"
    FRAMEWORK_COMPARISON = "framework_comparison"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"
    INTEGRATION = "integration"
    NARRATIVE_BUILDING = "narrative_building"


# Request Models

class AgentExecutionRequest(BaseModel):
    """Request model for direct agent execution."""
    
    query: str = Field(..., min_length=1, max_length=2000, description="Query for agent to process")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context for execution")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Agent-specific parameters")
    
    # Execution options
    timeout_seconds: int = Field(default=300, ge=30, le=1800, description="Execution timeout")
    quality_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Minimum quality threshold")
    enable_refinement: bool = Field(default=True, description="Enable TalkHier refinement")
    max_refinement_rounds: int = Field(default=3, ge=1, le=5, description="Maximum refinement rounds")
    
    # Metadata
    user_id: str | None = Field(None, description="User ID for tracking")
    session_id: str | None = Field(None, description="Session ID for context")


class ChainOfAgentsRequest(BaseModel):
    """Request model for Chain-of-Agents execution (sequential)."""
    
    query: str = Field(..., min_length=1, max_length=2000, description="Initial query")
    agent_chain: list[AgentType] = Field(..., min_length=2, max_length=5, description="Ordered list of agents")
    context: dict[str, Any] = Field(default_factory=dict, description="Execution context")
    
    # Chain configuration
    pass_intermediate_results: bool = Field(default=True, description="Pass results between agents")
    early_stopping: bool = Field(default=False, description="Stop on quality threshold")
    quality_threshold: float = Field(default=0.85, ge=0.0, le=1.0, description="Early stopping threshold")
    
    # Execution options
    timeout_per_agent_seconds: int = Field(default=180, ge=30, le=900, description="Timeout per agent")
    enable_validation: bool = Field(default=True, description="Enable result validation")


class MixtureOfAgentsRequest(BaseModel):
    """Request model for Mixture-of-Agents execution (parallel)."""
    
    query: str = Field(..., min_length=1, max_length=2000, description="Query for all agents")
    agent_types: list[AgentType] = Field(..., min_length=2, max_length=5, description="Agents to execute")
    context: dict[str, Any] = Field(default_factory=dict, description="Execution context")
    
    # Mixture configuration  
    aggregation_strategy: str = Field(default="consensus", description="Result aggregation method")
    weight_by_confidence: bool = Field(default=True, description="Weight results by agent confidence")
    consensus_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Consensus threshold")
    
    # Execution options
    timeout_seconds: int = Field(default=300, ge=60, le=1800, description="Total execution timeout")
    max_parallel: int = Field(default=3, ge=1, le=5, description="Maximum parallel agents")


class AgentValidationRequest(BaseModel):
    """Request model for agent input validation."""
    
    agent_type: AgentType = Field(..., description="Agent type to validate for")
    query: str = Field(..., min_length=1, max_length=2000, description="Query to validate")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Parameters to validate")


# Response Models

class AgentInfo(BaseModel):
    """Information about an available agent."""
    
    agent_type: AgentType
    name: str
    description: str
    capabilities: list[AgentCapability]
    
    # Performance characteristics
    average_execution_time_ms: int
    reliability_score: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    
    # Resource requirements
    complexity_handling: list[str]  # simple, moderate, complex
    optimal_domains: list[str]
    
    # API information
    version: str = "1.0.0"
    endpoints: list[str]


class AgentExecutionResponse(BaseModel):
    """Response model for agent execution."""
    
    execution_id: str
    agent_type: AgentType
    status: str  # pending, running, completed, failed
    
    # Results
    output: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    
    # Execution metadata
    execution_time_seconds: float
    tokens_used: int | None = None
    cost_estimate: float | None = None
    
    # Refinement information (if enabled)
    refinement_rounds: int = 0
    consensus_achieved: bool = False
    
    # Timestamps
    started_at: datetime
    completed_at: datetime | None = None
    
    # Error information
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChainOfAgentsResponse(BaseModel):
    """Response model for Chain-of-Agents execution."""
    
    execution_id: str
    status: str
    agent_chain: list[AgentType]
    
    # Chain results
    intermediate_results: list[dict[str, Any]]  # Results from each agent
    final_result: dict[str, Any]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    
    # Chain performance
    total_execution_time_seconds: float
    agent_execution_times: list[float]
    early_stopped: bool = False
    stopped_at_agent: AgentType | None = None
    
    # Quality metrics
    chain_quality_score: float = Field(ge=0.0, le=1.0)
    quality_improvement: float  # Improvement from first to last agent
    
    # Metadata
    started_at: datetime
    completed_at: datetime | None = None
    errors: list[str] = Field(default_factory=list)


class MixtureOfAgentsResponse(BaseModel):
    """Response model for Mixture-of-Agents execution."""
    
    execution_id: str
    status: str
    agent_types: list[AgentType]
    
    # Mixture results
    agent_results: dict[str, dict[str, Any]]  # Results keyed by agent type
    aggregated_result: dict[str, Any]
    consensus_score: float = Field(ge=0.0, le=1.0)
    
    # Aggregation metadata
    aggregation_strategy: str
    agent_weights: dict[str, float]  # Weights used for aggregation
    consensus_achieved: bool
    
    # Performance metrics
    total_execution_time_seconds: float
    parallel_efficiency: float  # Actual vs theoretical parallel speedup
    
    # Quality assessment
    mixture_quality_score: float = Field(ge=0.0, le=1.0)
    inter_agent_agreement: float = Field(ge=0.0, le=1.0)
    
    # Metadata
    started_at: datetime
    completed_at: datetime | None = None
    errors: list[str] = Field(default_factory=list)


class AgentValidationResponse(BaseModel):
    """Response model for agent input validation."""
    
    valid: bool
    agent_type: AgentType
    validation_score: float = Field(ge=0.0, le=1.0)
    
    # Validation details
    parameter_validation: dict[str, bool]
    query_suitability: float = Field(ge=0.0, le=1.0)
    estimated_quality: float = Field(ge=0.0, le=1.0)
    estimated_cost: float | None = None
    
    # Suggestions
    recommendations: list[str] = Field(default_factory=list)
    parameter_suggestions: dict[str, Any] = Field(default_factory=dict)
    
    # Issues
    validation_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentMetricsResponse(BaseModel):
    """Response model for agent performance metrics."""
    
    agent_type: AgentType
    
    # Performance metrics
    total_executions: int
    successful_executions: int
    success_rate: float = Field(ge=0.0, le=1.0)
    average_execution_time_ms: float
    average_quality_score: float = Field(ge=0.0, le=1.0)
    
    # Cost metrics
    average_cost_per_execution: float
    total_cost_last_30_days: float
    cost_efficiency_score: float = Field(ge=0.0, le=1.0)
    
    # Usage patterns
    peak_usage_hour: int = Field(ge=0, le=23)
    most_common_domains: list[str]
    complexity_distribution: dict[str, int]  # simple, moderate, complex
    
    # Quality trends
    quality_trend_7_days: float  # Positive = improving, negative = declining
    reliability_trend_7_days: float
    
    # Recent performance (last 24 hours)
    recent_executions: int
    recent_success_rate: float = Field(ge=0.0, le=1.0)
    recent_average_quality: float = Field(ge=0.0, le=1.0)
    
    # Metadata
    last_updated: datetime


class AgentListResponse(BaseModel):
    """Response model for listing available agents."""
    
    agents: list[AgentInfo]
    total_agents: int
    
    # System information
    system_version: str = "1.0.0"
    api_version: str = "v1"
    
    # Capabilities summary
    total_capabilities: int
    supported_domains: list[str]
    supported_execution_modes: list[ExecutionMode]
    
    # System metrics
    system_health: str  # healthy, degraded, unhealthy
    total_system_executions: int
    system_uptime_seconds: float


# Validation and utility models

class ExecutionProgress(BaseModel):
    """Real-time execution progress model."""
    
    execution_id: str
    status: str
    progress_percentage: float = Field(ge=0.0, le=100.0)
    current_phase: str
    
    # Phase details
    completed_phases: list[str]
    pending_phases: list[str]
    
    # Performance tracking
    elapsed_time_seconds: float
    estimated_remaining_seconds: float | None = None
    
    # Quality tracking
    current_quality_score: float | None = Field(None, ge=0.0, le=1.0)
    refinement_round: int = 0
    
    # Real-time metadata
    timestamp: datetime = Field(default_factory=datetime.now)


class AgentError(BaseModel):
    """Standardized agent error model."""
    
    error_code: str
    error_message: str
    agent_type: AgentType | None = None
    execution_id: str | None = None
    
    # Error details
    error_category: str  # validation, execution, timeout, resource, system
    severity: str  # low, medium, high, critical
    recoverable: bool
    
    # Context
    timestamp: datetime = Field(default_factory=datetime.now)
    context: dict[str, Any] = Field(default_factory=dict)
    
    # Suggestions
    suggested_action: str | None = None
    retry_recommended: bool = False


# Chain-of-Agents specific models

class ChainStep(BaseModel):
    """Individual step in Chain-of-Agents execution."""
    
    step_number: int
    agent_type: AgentType
    status: str  # pending, running, completed, failed, skipped
    
    # Step results
    input_data: dict[str, Any]
    output_data: dict[str, Any] | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    
    # Performance
    execution_time_seconds: float | None = None
    quality_score: float | None = Field(None, ge=0.0, le=1.0)
    
    # Metadata
    started_at: datetime | None = None
    completed_at: datetime | None = None
    errors: list[str] = Field(default_factory=list)


# Mixture-of-Agents specific models

class AgentContribution(BaseModel):
    """Individual agent contribution in Mixture-of-Agents."""
    
    agent_type: AgentType
    status: str
    
    # Contribution details
    output: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)  # Weight in final aggregation
    
    # Performance
    execution_time_seconds: float
    tokens_used: int | None = None
    
    # Metadata
    started_at: datetime
    completed_at: datetime | None = None


# Common utility models

class AgentHealthStatus(BaseModel):
    """Health status for an agent type."""
    
    agent_type: AgentType
    status: str  # healthy, degraded, unhealthy, unavailable
    
    # Health metrics
    success_rate_24h: float = Field(ge=0.0, le=1.0)
    average_response_time_ms: float
    error_rate: float = Field(ge=0.0, le=1.0)
    
    # Resource status
    resource_utilization: float = Field(ge=0.0, le=1.0)
    queue_length: int = Field(ge=0)
    
    # Issues
    current_issues: list[str] = Field(default_factory=list)
    last_health_check: datetime
    
    # Recovery information
    estimated_recovery_time: int | None = None  # seconds
    recovery_actions: list[str] = Field(default_factory=list)


__all__ = [
    "AgentCapability",
    "AgentContribution",
    "AgentError",
    # Request models
    "AgentExecutionRequest",
    # Response models
    "AgentExecutionResponse",
    "AgentHealthStatus",
    "AgentInfo",
    "AgentListResponse",
    "AgentMetricsResponse",
    # Enums
    "AgentType",
    "AgentValidationRequest",
    "AgentValidationResponse",
    "ChainOfAgentsRequest",
    "ChainOfAgentsResponse",
    "ChainStep",
    "ExecutionMode",
    # Utility models
    "ExecutionProgress",
    "MixtureOfAgentsRequest",
    "MixtureOfAgentsResponse",
]