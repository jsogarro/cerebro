"""
MASR Dynamic Routing API Models

Request and response models for MASR routing intelligence endpoints.
Based on "MasRouter: Learning to Route LLMs" research patterns.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict

from src.ai_brain.models.masr import (
    QueryDomain,
    QueryComplexity,
    RoutingStrategy,
    CollaborationMode,
    ModelTier
)


# Request Models

class RoutingRequest(BaseModel):
    """Request for intelligent routing decision"""
    model_config = ConfigDict(use_enum_values=True)
    
    query: str = Field(description="The user query to route")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context for routing")
    strategy: Optional[RoutingStrategy] = Field(default=None, description="Override routing strategy")
    max_cost: Optional[float] = Field(default=None, description="Maximum cost constraint")
    min_quality: Optional[float] = Field(default=None, description="Minimum quality requirement")
    timeout_ms: Optional[int] = Field(default=None, description="Timeout in milliseconds")


class CostEstimationRequest(BaseModel):
    """Request for cost estimation with breakdown"""
    model_config = ConfigDict(use_enum_values=True)
    
    query: str = Field(description="Query to estimate cost for")
    strategy: Optional[RoutingStrategy] = Field(default=None, description="Routing strategy to use")
    include_breakdown: bool = Field(default=True, description="Include detailed breakdown")
    include_confidence: bool = Field(default=True, description="Include confidence intervals")


class StrategyEvaluationRequest(BaseModel):
    """Request for strategy comparison and selection"""
    model_config = ConfigDict(use_enum_values=True)
    
    query: str = Field(description="Query to evaluate strategies for")
    strategies: Optional[List[RoutingStrategy]] = Field(
        default=None, 
        description="Strategies to evaluate (all if not specified)"
    )
    weights: Optional[Dict[str, float]] = Field(
        default=None,
        description="Custom weights for cost, quality, latency"
    )


class ComplexityAnalysisRequest(BaseModel):
    """Request for query complexity analysis"""
    query: str = Field(description="Query to analyze")
    include_features: bool = Field(default=True, description="Include feature breakdown")
    include_recommendations: bool = Field(default=True, description="Include routing recommendations")


class RoutingFeedback(BaseModel):
    """Feedback for routing decision learning"""
    routing_id: str = Field(description="ID of the routing decision")
    actual_cost: float = Field(description="Actual execution cost")
    actual_latency_ms: int = Field(description="Actual latency in milliseconds")
    quality_score: float = Field(ge=0, le=1, description="Quality score (0-1)")
    user_satisfaction: Optional[float] = Field(
        default=None, 
        ge=0, 
        le=1,
        description="User satisfaction score (0-1)"
    )
    error_occurred: bool = Field(default=False, description="Whether an error occurred")
    error_message: Optional[str] = Field(default=None, description="Error message if applicable")


# Response Models

class ModelInfo(BaseModel):
    """Information about an available model"""
    model_config = ConfigDict(use_enum_values=True)
    
    provider: str = Field(description="Model provider (e.g., 'deepseek', 'llama')")
    model_id: str = Field(description="Model identifier")
    tier: ModelTier = Field(description="Model tier classification")
    cost_per_token: float = Field(description="Cost per token in USD")
    max_tokens: int = Field(description="Maximum context tokens")
    capabilities: List[str] = Field(description="Model capabilities")
    average_latency_ms: int = Field(description="Average latency in milliseconds")
    quality_score: float = Field(ge=0, le=1, description="Quality score (0-1)")


class SupervisorAllocation(BaseModel):
    """Supervisor allocation details"""
    supervisor_type: str = Field(description="Type of supervisor allocated")
    worker_count: int = Field(description="Number of workers allocated")
    refinement_rounds: int = Field(description="Number of refinement rounds")
    estimated_latency_ms: int = Field(description="Estimated execution time")


class CostBreakdown(BaseModel):
    """Detailed cost breakdown"""
    model_costs: float = Field(description="Cost for model inference")
    coordination_overhead: float = Field(description="Cost for coordination")
    memory_operations: float = Field(description="Cost for memory operations")
    total_cost: float = Field(description="Total estimated cost")
    confidence_interval: Optional[tuple[float, float]] = Field(
        default=None,
        description="95% confidence interval for cost"
    )


class RoutingDecisionResponse(BaseModel):
    """Response with intelligent routing decision"""
    model_config = ConfigDict(use_enum_values=True)
    
    routing_id: str = Field(description="Unique routing decision ID")
    domain: QueryDomain = Field(description="Identified query domain")
    complexity: QueryComplexity = Field(description="Query complexity level")
    strategy: RoutingStrategy = Field(description="Selected routing strategy")
    collaboration_mode: CollaborationMode = Field(description="Collaboration mode")
    
    supervisor_allocations: List[SupervisorAllocation] = Field(
        description="Allocated supervisors and workers"
    )
    selected_models: List[ModelInfo] = Field(description="Selected models for execution")
    
    estimated_cost: float = Field(description="Estimated total cost in USD")
    estimated_latency_ms: int = Field(description="Estimated latency in milliseconds")
    confidence_score: float = Field(ge=0, le=1, description="Routing confidence (0-1)")
    
    reasoning: str = Field(description="Explanation of routing decision")
    alternatives: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Alternative routing options considered"
    )


class CostEstimationResponse(BaseModel):
    """Response with cost estimation and breakdown"""
    estimated_cost: float = Field(description="Total estimated cost in USD")
    breakdown: Optional[CostBreakdown] = Field(default=None, description="Cost breakdown")
    confidence_score: float = Field(ge=0, le=1, description="Estimation confidence (0-1)")
    cost_factors: Dict[str, float] = Field(description="Individual cost factors")
    recommendations: List[str] = Field(description="Cost optimization recommendations")


class StrategyComparison(BaseModel):
    """Comparison of a routing strategy"""
    model_config = ConfigDict(use_enum_values=True)
    
    strategy: RoutingStrategy = Field(description="Routing strategy")
    estimated_cost: float = Field(description="Estimated cost")
    estimated_quality: float = Field(ge=0, le=1, description="Estimated quality (0-1)")
    estimated_latency_ms: int = Field(description="Estimated latency")
    pros: List[str] = Field(description="Advantages of this strategy")
    cons: List[str] = Field(description="Disadvantages of this strategy")
    recommendation_score: float = Field(ge=0, le=1, description="Recommendation score (0-1)")


class StrategyEvaluationResponse(BaseModel):
    """Response with strategy evaluation results"""
    comparisons: List[StrategyComparison] = Field(description="Strategy comparisons")
    recommended_strategy: RoutingStrategy = Field(description="Recommended strategy")
    reasoning: str = Field(description="Reasoning for recommendation")
    trade_offs: Dict[str, str] = Field(description="Key trade-offs to consider")


class ComplexityFeatures(BaseModel):
    """Features contributing to complexity"""
    query_length: int = Field(description="Query length in tokens")
    domain_count: int = Field(description="Number of domains involved")
    reasoning_depth: int = Field(description="Reasoning depth required (1-5)")
    data_requirements: List[str] = Field(description="Data requirements identified")
    coordination_needs: str = Field(description="Coordination requirements")
    uncertainty_level: float = Field(ge=0, le=1, description="Uncertainty level (0-1)")


class ComplexityAnalysisResponse(BaseModel):
    """Response with complexity analysis results"""
    model_config = ConfigDict(use_enum_values=True)
    
    complexity: QueryComplexity = Field(description="Overall complexity level")
    complexity_score: float = Field(ge=0, le=1, description="Complexity score (0-1)")
    features: Optional[ComplexityFeatures] = Field(
        default=None,
        description="Detailed feature breakdown"
    )
    recommended_approach: str = Field(description="Recommended execution approach")
    routing_recommendations: List[str] = Field(description="Routing recommendations")


class AvailableStrategy(BaseModel):
    """Information about an available routing strategy"""
    model_config = ConfigDict(use_enum_values=True)
    
    strategy: RoutingStrategy = Field(description="Strategy identifier")
    name: str = Field(description="Human-readable name")
    description: str = Field(description="Strategy description")
    optimization_focus: str = Field(description="What this strategy optimizes for")
    use_cases: List[str] = Field(description="Recommended use cases")
    trade_offs: Dict[str, str] = Field(description="Key trade-offs")


class RouterStatus(BaseModel):
    """MASR router health and performance status"""
    status: str = Field(description="Overall status (healthy/degraded/unhealthy)")
    uptime_seconds: int = Field(description="Router uptime in seconds")
    total_routes: int = Field(description="Total routing decisions made")
    average_latency_ms: float = Field(description="Average routing latency")
    success_rate: float = Field(ge=0, le=1, description="Routing success rate (0-1)")
    active_supervisors: int = Field(description="Currently active supervisors")
    
    performance_metrics: Dict[str, float] = Field(
        description="Performance metrics by strategy"
    )
    model_availability: Dict[str, bool] = Field(
        description="Model provider availability"
    )
    learning_metrics: Dict[str, Any] = Field(
        description="Learning system metrics"
    )
    
    last_error: Optional[str] = Field(default=None, description="Last error message")
    last_error_time: Optional[datetime] = Field(default=None, description="Last error timestamp")


# List Response Models

class StrategiesListResponse(BaseModel):
    """Response with available routing strategies"""
    strategies: List[AvailableStrategy] = Field(description="Available strategies")
    default_strategy: RoutingStrategy = Field(description="Default strategy")
    total_count: int = Field(description="Total number of strategies")


class ModelsListResponse(BaseModel):
    """Response with available models and tiers"""
    models: List[ModelInfo] = Field(description="Available models")
    tiers: Dict[str, List[str]] = Field(description="Models grouped by tier")
    total_count: int = Field(description="Total number of models")
    providers: List[str] = Field(description="Available providers")


# Error Response

class MASRErrorResponse(BaseModel):
    """Error response from MASR router"""
    error: str = Field(description="Error message")
    error_code: str = Field(description="Error code")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error details")
    suggestions: Optional[List[str]] = Field(default=None, description="Suggestions to resolve")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Error timestamp")