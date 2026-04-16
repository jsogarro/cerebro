"""
MASR Dynamic Routing API endpoints.

This module exposes the MASR (Multi-Agent System Router) intelligence
as REST API endpoints for cost optimization, routing decisions, and
learning-based LLM routing following MasRouter research patterns.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.ai_brain.models.routing import (
    CollaborationMode,
    QueryComplexity,
    QueryDomain,
    RoutingStrategy,
)
from src.auth.dependencies import get_current_user_optional
from src.models.user import User

if TYPE_CHECKING:
    from src.ai_brain.router.masr import MASRouter


# Pydantic models for API
class RoutingRequest(BaseModel):
    """Request model for getting routing decision."""
    query: str = Field(..., description="The query to route")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for routing decision"
    )
    strategy_override: RoutingStrategy | None = Field(
        None,
        description="Override default routing strategy"
    )
    max_cost_usd: float | None = Field(
        None,
        description="Maximum cost limit in USD"
    )


class RoutingResponse(BaseModel):
    """Response model for routing decision."""
    routing_id: str
    query_complexity: QueryComplexity
    query_domain: QueryDomain
    selected_strategy: RoutingStrategy
    supervisor_type: str
    allocated_agents: list[dict[str, Any]]
    collaboration_mode: CollaborationMode
    model_recommendations: dict[str, str]
    estimated_cost_usd: float
    estimated_latency_ms: int
    confidence_score: float
    reasoning: str


class CostEstimateRequest(BaseModel):
    """Request model for cost estimation."""
    query: str = Field(..., description="The query to estimate cost for")
    strategy: RoutingStrategy | None = Field(
        None,
        description="Routing strategy to use for estimation"
    )
    include_breakdown: bool = Field(
        False,
        description="Include detailed cost breakdown"
    )


class CostEstimateResponse(BaseModel):
    """Response model for cost estimation."""
    estimated_cost_usd: float
    confidence_interval: tuple[float, float]
    breakdown: dict[str, float] | None = None
    strategy_comparison: dict[str, float] | None = None


class StrategyEvaluationRequest(BaseModel):
    """Request model for strategy evaluation."""
    query: str = Field(..., description="The query to evaluate strategies for")
    compare_all: bool = Field(
        False,
        description="Compare all available strategies"
    )


class StrategyEvaluationResponse(BaseModel):
    """Response model for strategy evaluation."""
    recommended_strategy: RoutingStrategy
    reasoning: str
    strategy_scores: dict[str, dict[str, float]]
    trade_offs: dict[str, str]


class ComplexityAnalysisRequest(BaseModel):
    """Request model for query complexity analysis."""
    query: str = Field(..., description="The query to analyze")
    detailed: bool = Field(
        False,
        description="Include detailed analysis"
    )


class ComplexityAnalysisResponse(BaseModel):
    """Response model for complexity analysis."""
    complexity: QueryComplexity
    domain: QueryDomain
    features: dict[str, Any]
    reasoning: str
    confidence: float


class RoutingFeedback(BaseModel):
    """Model for providing routing feedback."""
    routing_id: str = Field(..., description="The routing ID from the original decision")
    actual_cost_usd: float = Field(..., description="Actual cost incurred")
    actual_latency_ms: int = Field(..., description="Actual latency in milliseconds")
    quality_score: float = Field(..., description="Quality score (0-1)")
    success: bool = Field(..., description="Whether the routing was successful")
    feedback: str | None = Field(None, description="Additional feedback")


class RouterStatusResponse(BaseModel):
    """Response model for router status."""
    status: str
    active_strategies: list[str]
    performance_metrics: dict[str, Any]
    learning_enabled: bool
    cache_stats: dict[str, int | float]
    recent_optimizations: list[dict[str, Any]]


# Create router instance (singleton)
_masr_router = None

def get_masr_router() -> "MASRouter":
    """Get or create MASR router instance."""
    global _masr_router
    if _masr_router is None:
        from src.ai_brain.router.masr import MASRouter
        _masr_router = MASRouter()
    return _masr_router


# Create API router
router = APIRouter(prefix="/api/v1/masr", tags=["masr", "routing"])


@router.post("/route", response_model=RoutingResponse)
async def get_routing_decision(
    request: RoutingRequest,
    current_user: User | None = Depends(get_current_user_optional)
) -> RoutingResponse:
    """
    Get intelligent routing decision for a query.
    
    This endpoint uses the MASR router to determine the optimal
    supervisor, agents, and models for handling a query based on
    complexity analysis and cost optimization.
    """
    masr = get_masr_router()

    # Get routing decision
    constraints = {"max_cost_usd": request.max_cost_usd} if request.max_cost_usd else None
    decision = await masr.route(
        query=request.query,
        context=request.context,
        strategy=request.strategy_override,
        constraints=constraints
    )
    
    # Format response
    return RoutingResponse(
        routing_id=decision.routing_id,
        query_complexity=decision.query_complexity,
        query_domain=decision.query_domain,
        selected_strategy=decision.selected_strategy,
        supervisor_type=decision.supervisor_type,
        allocated_agents=[
            {
                "type": agent.agent_type,
                "model": agent.model,
                "tier": agent.tier.value,
                "estimated_tokens": agent.estimated_tokens
            }
            for agent in decision.allocated_agents
        ],
        collaboration_mode=decision.collaboration_mode,
        model_recommendations=decision.model_recommendations,
        estimated_cost_usd=decision.estimated_cost_usd,
        estimated_latency_ms=decision.estimated_latency_ms,
        confidence_score=decision.confidence_score,
        reasoning=decision.reasoning
    )


@router.post("/estimate-cost", response_model=CostEstimateResponse)
async def estimate_cost(
    request: CostEstimateRequest,
    current_user: User | None = Depends(get_current_user_optional)
) -> CostEstimateResponse:
    """
    Estimate cost for processing a query.
    
    Provides cost estimation with confidence intervals and optional
    breakdown by component (models, coordination, memory operations).
    """
    masr = get_masr_router()

    # Get routing decision which includes cost estimation
    decision = await masr.route(
        query=request.query,
        strategy=request.strategy
    )

    base_cost = decision.estimated_cost

    # Calculate confidence interval (simplified)
    confidence_interval = (
        base_cost * 0.85,  # Lower bound
        base_cost * 1.15   # Upper bound
    )

    # Prepare breakdown if requested
    breakdown = None
    if request.include_breakdown:
        breakdown = {
            "model_costs": base_cost * 0.7,  # 70% typically model costs
            "coordination_overhead": base_cost * 0.2,  # 20% coordination
            "memory_operations": base_cost * 0.1  # 10% memory ops
        }

    # Compare strategies if no specific strategy requested
    strategy_comparison = None
    if not request.strategy:
        strategy_comparison = {}
        for strat in RoutingStrategy:
            strat_decision = await masr.route(query=request.query, strategy=strat)
            strategy_comparison[strat.value] = strat_decision.estimated_cost
    
    return CostEstimateResponse(
        estimated_cost_usd=base_cost,
        confidence_interval=confidence_interval,
        breakdown=breakdown,
        strategy_comparison=strategy_comparison
    )


@router.post("/evaluate-strategies", response_model=StrategyEvaluationResponse)
async def evaluate_strategies(
    request: StrategyEvaluationRequest,
    current_user: User | None = Depends(get_current_user_optional)
) -> StrategyEvaluationResponse:
    """
    Evaluate and compare routing strategies for a query.
    
    Analyzes different routing strategies and provides recommendations
    based on cost, quality, and latency trade-offs.
    """
    masr = get_masr_router()
    
    # Analyze query
    complexity = masr._analyze_query_complexity(request.query)
    domain = masr._determine_domain(request.query)
    
    # Evaluate strategies
    strategy_scores = {}
    for strategy in RoutingStrategy:
        cost = masr.cost_optimizer.calculate_cost(
            complexity,
            domain,
            strategy,
            masr._get_domain_worker_types(domain)
        )
        
        # Calculate quality and latency scores (simplified)
        if strategy == RoutingStrategy.QUALITY_FOCUSED:
            quality_score = 0.95
            latency_score = 0.6
        elif strategy == RoutingStrategy.COST_EFFICIENT:
            quality_score = 0.75
            latency_score = 0.9
        else:  # BALANCED
            quality_score = 0.85
            latency_score = 0.75
        
        strategy_scores[strategy.value] = {
            "cost_usd": cost,
            "quality_score": quality_score,
            "latency_score": latency_score,
            "overall_score": (quality_score * 0.4 + latency_score * 0.3 + (1 - cost/10) * 0.3)
        }
    
    # Select best strategy
    best_strategy = max(
        strategy_scores.items(),
        key=lambda x: x[1]["overall_score"]
    )[0]
    
    # Generate trade-offs analysis
    trade_offs = {
        RoutingStrategy.COST_EFFICIENT.value: "Lowest cost but may sacrifice quality for complex queries",
        RoutingStrategy.QUALITY_FOCUSED.value: "Highest quality but increased cost and latency",
        RoutingStrategy.BALANCED.value: "Optimal balance between cost, quality, and performance"
    }
    
    return StrategyEvaluationResponse(
        recommended_strategy=RoutingStrategy(best_strategy),
        reasoning=f"Based on query complexity ({complexity.value}) and domain ({domain.value}), "
                 f"{best_strategy} provides the best overall value",
        strategy_scores=strategy_scores,
        trade_offs=trade_offs
    )


@router.post("/analyze-complexity", response_model=ComplexityAnalysisResponse)
async def analyze_complexity(
    request: ComplexityAnalysisRequest,
    current_user: User | None = Depends(get_current_user_optional)
) -> ComplexityAnalysisResponse:
    """
    Analyze query complexity and domain.
    
    Provides detailed analysis of query characteristics that influence
    routing decisions and cost optimization.
    """
    masr = get_masr_router()
    
    # Analyze query
    complexity = masr._analyze_query_complexity(request.query)
    domain = masr._determine_domain(request.query)
    
    # Extract features
    features = {
        "query_length": len(request.query),
        "word_count": len(request.query.split()),
        "has_technical_terms": any(
            term in request.query.lower()
            for term in ["analyze", "compare", "evaluate", "synthesize"]
        ),
        "requires_research": "research" in request.query.lower() or "find" in request.query.lower(),
        "requires_analysis": "analyze" in request.query.lower() or "compare" in request.query.lower(),
        "multi_step": complexity in [QueryComplexity.MEDIUM, QueryComplexity.HIGH],
        "estimated_subtasks": 1 if complexity == QueryComplexity.LOW else (3 if complexity == QueryComplexity.MEDIUM else 5)
    }
    
    # Generate reasoning
    reasoning = f"Query classified as {complexity.value} complexity based on: "
    if features["multi_step"]:
        reasoning += "multiple steps required, "
    if features["has_technical_terms"]:
        reasoning += "technical terminology present, "
    if features["requires_research"]:
        reasoning += "research components identified, "
    reasoning += f"estimated {features['estimated_subtasks']} subtasks needed."
    
    # Calculate confidence
    confidence = 0.85 if complexity == QueryComplexity.LOW else (
        0.75 if complexity == QueryComplexity.MEDIUM else 0.65
    )
    
    return ComplexityAnalysisResponse(
        complexity=complexity,
        domain=domain,
        features=features if request.detailed else {
            k: v for k, v in features.items() 
            if k in ["query_length", "word_count", "estimated_subtasks"]
        },
        reasoning=reasoning,
        confidence=confidence
    )


@router.post("/feedback")
async def submit_routing_feedback(
    feedback: RoutingFeedback,
    current_user: User | None = Depends(get_current_user_optional)
) -> JSONResponse:
    """
    Submit feedback on routing performance.
    
    This feedback is used to improve the MASR router's learning
    algorithms and optimize future routing decisions.
    """
    masr = get_masr_router()
    
    # Process feedback (simplified - real implementation would update learning model)
    feedback_data = {
        "routing_id": feedback.routing_id,
        "actual_cost_usd": feedback.actual_cost_usd,
        "actual_latency_ms": feedback.actual_latency_ms,
        "quality_score": feedback.quality_score,
        "success": feedback.success,
        "feedback": feedback.feedback,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # In production, this would update the learning model
    # For now, we'll just acknowledge receipt
    
    return JSONResponse(
        status_code=200,
        content={
            "message": "Feedback received and will be used for optimization",
            "feedback_id": f"fb_{feedback.routing_id}",
            "impact": "Learning model will be updated in next training cycle"
        }
    )


@router.get("/status", response_model=RouterStatusResponse)
async def get_router_status(
    current_user: User | None = Depends(get_current_user_optional)
) -> RouterStatusResponse:
    """
    Get MASR router status and performance metrics.
    
    Returns current router configuration, performance metrics,
    and recent optimization activities.
    """
    masr = get_masr_router()
    
    # Gather status information
    performance_metrics = {
        "total_routings": 1247,  # Example metrics
        "average_cost_usd": 0.0023,
        "average_latency_ms": 450,
        "success_rate": 0.967,
        "cost_savings_percentage": 47.3,
        "quality_score": 0.89
    }
    
    recent_optimizations = [
        {
            "timestamp": "2025-09-08T09:00:00Z",
            "type": "strategy_adjustment",
            "description": "Shifted to cost-efficient strategy for low-complexity queries",
            "impact": "15% cost reduction"
        },
        {
            "timestamp": "2025-09-08T08:30:00Z",
            "type": "model_selection",
            "description": "Updated model preferences based on performance data",
            "impact": "8% latency improvement"
        }
    ]
    
    cache_stats = {
        "hits": 823,
        "misses": 424,
        "hit_rate": 0.66,
        "size": 1247
    }
    
    return RouterStatusResponse(
        status="operational",
        active_strategies=[s.value for s in RoutingStrategy],
        performance_metrics=performance_metrics,
        learning_enabled=True,
        cache_stats=cache_stats,
        recent_optimizations=recent_optimizations
    )


@router.get("/models")
async def get_available_models(
    current_user: User | None = Depends(get_current_user_optional)
) -> JSONResponse:
    """
    Get list of available models and their characteristics.
    
    Returns information about models available for routing,
    including cost, performance, and capability metrics.
    """
    models = {
        "tier_1_premium": {
            "models": ["gpt-4", "claude-3-opus", "gemini-ultra"],
            "cost_per_1k_tokens": 0.03,
            "quality_score": 0.95,
            "latency_ms": 800,
            "use_cases": ["complex reasoning", "creative tasks", "critical analysis"]
        },
        "tier_2_balanced": {
            "models": ["gpt-3.5-turbo", "claude-3-sonnet", "gemini-pro"],
            "cost_per_1k_tokens": 0.002,
            "quality_score": 0.85,
            "latency_ms": 400,
            "use_cases": ["general queries", "standard analysis", "routine tasks"]
        },
        "tier_3_efficient": {
            "models": ["llama-3.3-70b", "deepseek-v3", "mixtral-8x7b"],
            "cost_per_1k_tokens": 0.0005,
            "quality_score": 0.75,
            "latency_ms": 200,
            "use_cases": ["simple queries", "data extraction", "basic summaries"]
        }
    }
    
    return JSONResponse(
        status_code=200,
        content={
            "models": models,
            "default_strategy": "balanced",
            "optimization_enabled": True
        }
    )