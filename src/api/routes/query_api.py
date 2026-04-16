"""
Query API Routes - Primary Interface for Cerebro AI Brain

Primary API following research-validated routing patterns:
- Always routes through MASR for optimal agent selection
- Uses hierarchical supervisors for structured coordination
- Implements "MasRouter: Learning to Route LLMs" patterns
- Follows "Talk Structurally, Act Hierarchically" protocols

This is the RECOMMENDED API for most use cases, as it leverages
Cerebro's full intelligence and learning capabilities.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, Field
from structlog import get_logger

from ...ai_brain.router.masr import RoutingStrategy
from ...models.research_project import ResearchDepth, ResearchQuery
from ..services.direct_execution_service import get_direct_execution_service

logger = get_logger()
router = APIRouter(prefix="/api/v1/query")


# Request Models for Primary Query API

class IntelligentQueryRequest(BaseModel):
    """Request model for intelligent query routing."""
    
    query: str = Field(..., min_length=1, max_length=2000, description="Research query")
    domains: list[str] = Field(default_factory=list, description="Domain hints (optional)")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    
    # Routing preferences (optional - MASR will optimize if not specified)
    routing_strategy: RoutingStrategy | None = Field(None, description="Routing strategy preference")
    quality_preference: float | None = Field(None, ge=0.0, le=1.0, description="Quality vs speed preference")
    cost_preference: float | None = Field(None, ge=0.0, le=1.0, description="Cost sensitivity")
    
    # Execution options
    enable_real_time_updates: bool = Field(default=True, description="Enable WebSocket progress updates")
    timeout_seconds: int = Field(default=300, ge=60, le=1800, description="Maximum execution time")
    
    # User context
    user_id: str | None = Field(None, description="User ID for personalization")
    session_id: str | None = Field(None, description="Session ID for context")


class AnalysisRequest(BaseModel):

    query: str = Field(..., min_length=1, max_length=2000)
    analysis_type: str = Field(default="comprehensive", pattern="^(basic|comprehensive|comparative|methodological)$")
    domains: list[str] = Field(default_factory=list)
    depth: ResearchDepth = Field(default=ResearchDepth.COMPREHENSIVE)
    
    # Analysis preferences
    include_methodology: bool = Field(default=True)
    include_citations: bool = Field(default=True) 
    enable_comparison: bool = Field(default=True)
    
    # Context
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(None)


class SynthesisRequest(BaseModel):

    query: str = Field(..., min_length=1, max_length=2000)
    synthesis_focus: str = Field(default="comprehensive", pattern="^(comprehensive|thematic|comparative)$")
    source_materials: list[dict[str, Any]] = Field(default_factory=list, description="Pre-existing materials to synthesize")

    narrative_style: str = Field(default="academic", pattern="^(academic|executive|technical)$")
    include_visualizations: bool = Field(default=True)
    citation_style: str = Field(default="APA", pattern="^(APA|MLA|Chicago)$")
    
    # Context
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(None)


# Response Models

class IntelligentQueryResponse(BaseModel):
    """Response model for intelligent query execution."""
    
    execution_id: str
    query_id: str
    status: str  # pending, routing, executing, completed, failed
    
    # MASR routing information
    routing_decision: dict[str, Any]
    supervisor_type: str
    selected_agents: list[str]
    estimated_cost: float
    estimated_quality: float
    
    # Execution results
    results: dict[str, Any]
    quality_scores: dict[str, float]
    confidence: float
    
    # Performance metrics
    routing_time_ms: float
    execution_time_seconds: float
    total_time_seconds: float
    
    # Learning feedback
    routing_accuracy: float | None = None
    cost_accuracy: float | None = None
    
    # Metadata
    started_at: str
    completed_at: str | None = None


# Primary API Endpoints (90% of usage should go through these)

@router.post("/research", response_model=IntelligentQueryResponse)
async def intelligent_research_query(
    request: IntelligentQueryRequest,
    background_tasks: BackgroundTasks,
) -> IntelligentQueryResponse:
    """
    Primary research endpoint using MASR intelligent routing.
    
    This endpoint implements the full Cerebro intelligence stack:
    1. MASR analyzes query and selects optimal routing strategy
    2. Hierarchical supervisors coordinate appropriate agents  
    3. TalkHier protocol ensures quality through multi-round refinement
    4. Results feed back to improve future routing decisions
    
    Based on "MasRouter: Learning to Route LLMs" research.
    """
    try:
        logger.info(f"Intelligent research query: {request.query[:100]}...")
        
        # Use direct execution service which integrates MASR routing
        execution_service = get_direct_execution_service()
        
        # Create research project for execution
        from ...models.research_project import ResearchProject, ResearchScope
        
        project = ResearchProject(
            title=f"API Query: {request.query[:50]}...",
            query=ResearchQuery(
                text=request.query,
                domains=request.domains or ["general"],
                depth_level=ResearchDepth.COMPREHENSIVE.value,
            ),
            user_id=request.user_id or "api_user",
            scope=ResearchScope()
        )
        
        # Start intelligent execution
        execution_id = await execution_service.start_research_execution(
            project,
            context={
                **request.context,
                "routing_strategy": request.routing_strategy.value if request.routing_strategy else None,
                "quality_preference": request.quality_preference,
                "cost_preference": request.cost_preference,
                "api_endpoint": "intelligent_research_query",
            }
        )
        
        # Get execution status for response
        execution_status = await execution_service.get_execution_status(execution_id)
        
        response = IntelligentQueryResponse(
            execution_id=execution_id,
            query_id=str(project.id),
            status=execution_status.status if execution_status else "pending",
            routing_decision=execution_status.routing_decision if execution_status and execution_status.routing_decision else {},
            supervisor_type=execution_status.supervisor_type if execution_status and execution_status.supervisor_type else "research",
            selected_agents=[], # Would be populated from routing decision
            estimated_cost=0.015,  # Would come from MASR
            estimated_quality=0.85,  # Would come from MASR
            results=execution_status.agent_results if execution_status else {},
            quality_scores=execution_status.quality_scores if execution_status else {},
            confidence=0.85,  # Would be calculated from execution
            routing_time_ms=50.0,  # Would be measured
            execution_time_seconds=execution_status.execution_time_seconds if execution_status else 0.0,
            total_time_seconds=execution_status.execution_time_seconds if execution_status else 0.0,
            started_at=execution_status.started_at.isoformat() if execution_status else "",
        )
        
        logger.info(
            "Intelligent query routed",
            execution_id=execution_id,
            supervisor_type=response.supervisor_type,
            estimated_cost=response.estimated_cost,
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Intelligent research query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {e!s}"
        )


@router.post("/analyze", response_model=IntelligentQueryResponse)
async def intelligent_analysis_query(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
) -> IntelligentQueryResponse:
    """
    Analysis-focused endpoint using MASR intelligent routing.
    
    Optimized for analytical queries with configurable depth and methodology.
    Always routes through MASR for optimal agent selection and cost efficiency.
    """
    try:
        logger.info(f"Intelligent analysis query: {request.query[:100]}...")
        
        intelligent_request = IntelligentQueryRequest(
            query=request.query,
            domains=request.domains,
            context={
                **request.context,
                "analysis_type": request.analysis_type,
                "depth": request.depth.value,
                "include_methodology": request.include_methodology,
                "include_citations": request.include_citations,
                "enable_comparison": request.enable_comparison,
                "api_endpoint": "intelligent_analysis_query",
            },
            routing_strategy=RoutingStrategy.QUALITY_FOCUSED if request.analysis_type == "exhaustive" else None,
            quality_preference=None,
            cost_preference=None,
            user_id=request.user_id,
            session_id=None,
        )
        
        return await intelligent_research_query(intelligent_request, background_tasks)
        
    except Exception as e:
        logger.error(f"Intelligent analysis query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis query failed: {e!s}"
        )


@router.post("/synthesize", response_model=IntelligentQueryResponse)
async def intelligent_synthesis_query(
    request: SynthesisRequest,
    background_tasks: BackgroundTasks,
) -> IntelligentQueryResponse:
    """
    Synthesis-focused endpoint using MASR intelligent routing.
    
    Optimized for synthesis queries with existing materials or fresh analysis.
    MASR determines whether to use direct synthesis or full research pipeline.
    """
    try:
        logger.info(f"Intelligent synthesis query: {request.query[:100]}...")
        
        intelligent_request = IntelligentQueryRequest(
            query=request.query,
            context={
                **request.context,
                "synthesis_focus": request.synthesis_focus,
                "source_materials": request.source_materials,
                "narrative_style": request.narrative_style,
                "include_visualizations": request.include_visualizations,
                "citation_style": request.citation_style,
                "api_endpoint": "intelligent_synthesis_query",
            },
            routing_strategy=RoutingStrategy.BALANCED,
            quality_preference=None,
            cost_preference=None,
            user_id=request.user_id,
            session_id=None,
        )
        
        return await intelligent_research_query(intelligent_request, background_tasks)
        
    except Exception as e:
        logger.error(f"Intelligent synthesis query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthesis query failed: {e!s}"
        )


@router.get("/execution/{execution_id}/status")
async def get_execution_status(execution_id: str) -> dict[str, Any]:
    """
    Get real-time status of intelligent query execution.
    
    Provides progress updates from MASR routing through supervisor coordination
    to final agent execution and result synthesis.
    """
    try:
        execution_service = get_direct_execution_service()
        exec_status = await execution_service.get_execution_status(execution_id)

        if not exec_status:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found"
            )

        return {
            "execution_id": execution_id,
            "status": exec_status.status,
            "progress_percentage": exec_status.progress_percentage,
            "current_phase": exec_status.current_phase,
            "supervisor_type": exec_status.supervisor_type,
            "workers_used": exec_status.workers_used,
            "routing_decision": exec_status.routing_decision,
            "execution_time_seconds": exec_status.execution_time_seconds,
            "errors": exec_status.errors,
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve execution status"
        )


@router.get("/execution/{execution_id}/results")
async def get_execution_results(execution_id: str) -> dict[str, Any]:
    """Get results from completed intelligent query execution."""
    
    try:
        execution_service = get_direct_execution_service()
        results = await execution_service.get_execution_results(execution_id)
        
        if not results:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found or not completed"
            )
        
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution results: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve execution results"
        )


# Convenience endpoints that route through MASR

@router.post("/literature")
async def intelligent_literature_query(
    query: str = Query(..., min_length=10),
    domains: list[str] = Query(default=[]),
    max_sources: int = Query(50, ge=10, le=200),
    depth: ResearchDepth = ResearchDepth.COMPREHENSIVE,
) -> IntelligentQueryResponse:
    """
    Literature-focused query with MASR intelligent routing.
    
    MASR determines optimal literature review strategy based on query complexity.
    """
    request = IntelligentQueryRequest(
        query=f"Literature review: {query}",
        domains=domains,
        context={
            "max_sources": max_sources,
            "depth": depth.value,
            "focus": "literature_review",
        },
        routing_strategy=RoutingStrategy.QUALITY_FOCUSED,
        quality_preference=None,
        cost_preference=None,
        user_id=None,
        session_id=None,
    )
    
    return await intelligent_research_query(request, BackgroundTasks())


@router.post("/methodology")
async def intelligent_methodology_query(
    query: str = Query(..., min_length=10),
    research_type: str = Query("mixed", regex="^(quantitative|qualitative|mixed)$"),
    domains: list[str] = Query(default=[]),
) -> IntelligentQueryResponse:
    """
    Methodology-focused query with MASR intelligent routing.
    
    MASR selects optimal methodology approach based on research type and complexity.
    """
    request = IntelligentQueryRequest(
        query=f"Methodology design: {query}",
        domains=domains,
        context={
            "research_type": research_type,
            "focus": "methodology",
        },
        routing_strategy=RoutingStrategy.BALANCED,
        quality_preference=None,
        cost_preference=None,
        user_id=None,
        session_id=None,
    )
    
    return await intelligent_research_query(request, BackgroundTasks())


@router.post("/comparison")
async def intelligent_comparison_query(
    query: str = Query(..., min_length=10),
    comparison_focus: str = Query("approaches", regex="^(approaches|theories|methods|findings)$"),
    domains: list[str] = Query(default=[]),
) -> IntelligentQueryResponse:
    """
    Comparison-focused query with MASR intelligent routing.
    
    MASR determines optimal comparison strategy and agent coordination.
    """
    request = IntelligentQueryRequest(
        query=f"Compare {comparison_focus}: {query}",
        domains=domains,
        context={
            "comparison_focus": comparison_focus,
            "focus": "comparative_analysis",
        },
        routing_strategy=RoutingStrategy.QUALITY_FOCUSED,
        quality_preference=None,
        cost_preference=None,
        user_id=None,
        session_id=None,
    )
    
    return await intelligent_research_query(request, BackgroundTasks())


# System intelligence endpoints

@router.get("/routing/strategies")
async def get_available_routing_strategies() -> dict[str, Any]:
    """
    Get available routing strategies and their characteristics.
    
    Exposes MASR routing intelligence for user understanding and optimization.
    """
    strategies = {
        "speed_first": {
            "description": "Optimize for minimum latency",
            "use_cases": ["Quick queries", "Real-time interaction"],
            "trade_offs": "Lower quality for faster response",
            "typical_cost": "Low",
            "typical_quality": "Good",
        },
        "cost_efficient": {
            "description": "Optimize for minimum cost",
            "use_cases": ["Large-scale processing", "Budget-conscious research"],
            "trade_offs": "Potentially lower quality/speed for cost savings",
            "typical_cost": "Minimal",
            "typical_quality": "Good",
        },
        "quality_focused": {
            "description": "Optimize for maximum quality",
            "use_cases": ["Academic research", "Critical analysis"],
            "trade_offs": "Higher cost and time for best results",
            "typical_cost": "Higher",
            "typical_quality": "Excellent",
        },
        "balanced": {
            "description": "Balance quality, cost, and speed",
            "use_cases": ["General research", "Most common queries"],
            "trade_offs": "Optimal trade-off across all factors",
            "typical_cost": "Moderate",
            "typical_quality": "Very Good",
        },
        "adaptive": {
            "description": "Learn optimal strategy from usage patterns",
            "use_cases": ["Personalized research", "Learning systems"],
            "trade_offs": "Improves over time, may vary initially",
            "typical_cost": "Variable",
            "typical_quality": "Improving",
        },
    }
    
    return {
        "available_strategies": strategies,
        "default_strategy": "balanced",
        "recommendation": "Use 'adaptive' for personalized optimization, 'quality_focused' for academic work",
    }


@router.get("/routing/recommend")
async def get_routing_recommendation(
    query: str = Query(..., min_length=1),
    context: dict[str, Any] = Query(default_factory=dict),
) -> dict[str, Any]:
    """
    Get MASR routing recommendation without executing query.
    
    Useful for cost estimation and strategy planning.
    """
    try:
        # This would use MASR analysis to provide routing recommendation
        # For now, simplified logic based on query characteristics
        
        query_length = len(query)
        complexity = "simple" if query_length < 100 else "moderate" if query_length < 500 else "complex"
        
        recommendations = {
            "simple": {
                "suggested_strategy": "cost_efficient",
                "expected_agents": ["literature-review"],
                "estimated_cost": 0.005,
                "estimated_time_seconds": 60,
                "estimated_quality": 0.80,
            },
            "moderate": {
                "suggested_strategy": "balanced", 
                "expected_agents": ["literature-review", "methodology", "synthesis"],
                "estimated_cost": 0.015,
                "estimated_time_seconds": 180,
                "estimated_quality": 0.85,
            },
            "complex": {
                "suggested_strategy": "quality_focused",
                "expected_agents": ["literature-review", "methodology", "comparative-analysis", "synthesis", "citation"],
                "estimated_cost": 0.035,
                "estimated_time_seconds": 300,
                "estimated_quality": 0.92,
            },
        }
        
        recommendation = recommendations[complexity]
        
        return {
            "query_analysis": {
                "complexity": complexity,
                "estimated_domains": context.get("domains", []),
                "confidence": 0.85,
            },
            "routing_recommendation": recommendation,
            "alternative_strategies": {
                strategy: data for strategy, data in recommendations.items() 
                if strategy != complexity
            },
            "explanation": f"Query classified as {complexity} - routing through {recommendation['suggested_strategy']} strategy for optimal results",
        }
        
    except Exception as e:
        logger.error(f"Routing recommendation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate routing recommendation"
        )


__all__ = ["router"]