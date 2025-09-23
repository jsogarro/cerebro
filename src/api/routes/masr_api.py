"""
MASR Dynamic Routing API Routes

REST API endpoints for MASR routing intelligence, following
"MasRouter: Learning to Route LLMs" research patterns.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from typing import Dict, Any

from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import (
    RoutingRequest,
    CostEstimationRequest,
    StrategyEvaluationRequest,
    ComplexityAnalysisRequest,
    RoutingFeedback,
    RoutingDecisionResponse,
    CostEstimationResponse,
    StrategyEvaluationResponse,
    ComplexityAnalysisResponse,
    StrategiesListResponse,
    ModelsListResponse,
    RouterStatus,
    MASRErrorResponse
)

# Create router with prefix and tags
router = APIRouter(
    prefix="/api/v1/masr",
    tags=["MASR Routing Intelligence"],
    responses={
        404: {"description": "Not found"},
        500: {"description": "Internal server error"}
    }
)

# Service instance (in production, use dependency injection)
routing_service = MASRRoutingService()


@router.post(
    "/route",
    response_model=RoutingDecisionResponse,
    summary="Get intelligent routing decision",
    description=(
        "Get an intelligent routing decision for a query based on MASR analysis. "
        "Returns optimal supervisor allocation, model selection, and cost estimation."
    ),
    responses={
        200: {
            "description": "Successful routing decision",
            "model": RoutingDecisionResponse
        },
        400: {
            "description": "Invalid request",
            "model": MASRErrorResponse
        }
    }
)
async def get_routing_decision(request: RoutingRequest) -> RoutingDecisionResponse:
    """
    Get intelligent routing decision for a query.
    
    This endpoint analyzes the query complexity, selects the optimal routing strategy,
    allocates supervisors and workers, and provides cost/latency estimates.
    """
    try:
        response = await routing_service.get_routing_decision(request)
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=MASRErrorResponse(
                error=str(e),
                error_code="INVALID_REQUEST",
                details={"request": request.dict()},
                suggestions=["Check query format", "Verify strategy is valid"]
            ).dict()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Internal routing error",
                error_code="ROUTING_ERROR",
                details={"error": str(e)},
                suggestions=["Retry request", "Contact support if issue persists"]
            ).dict()
        )


@router.post(
    "/estimate-cost",
    response_model=CostEstimationResponse,
    summary="Estimate execution cost",
    description=(
        "Estimate the cost of executing a query with detailed breakdown. "
        "Includes model costs, coordination overhead, and confidence intervals."
    )
)
async def estimate_cost(request: CostEstimationRequest) -> CostEstimationResponse:
    """
    Estimate execution cost with breakdown.
    
    Provides detailed cost analysis including:
    - Model inference costs
    - Coordination overhead
    - Memory operations
    - Confidence intervals
    - Optimization recommendations
    """
    try:
        response = await routing_service.estimate_cost(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Cost estimation failed",
                error_code="ESTIMATION_ERROR",
                details={"error": str(e)},
                suggestions=["Try simpler query", "Check strategy selection"]
            ).dict()
        )


@router.post(
    "/evaluate-strategies",
    response_model=StrategyEvaluationResponse,
    summary="Evaluate routing strategies",
    description=(
        "Compare multiple routing strategies for a query. "
        "Returns pros, cons, and recommendations for each strategy."
    )
)
async def evaluate_strategies(
    request: StrategyEvaluationRequest
) -> StrategyEvaluationResponse:
    """
    Evaluate and compare routing strategies.
    
    Compares strategies based on:
    - Cost efficiency
    - Output quality
    - Execution latency
    - Custom weight preferences
    """
    try:
        response = await routing_service.evaluate_strategies(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Strategy evaluation failed",
                error_code="EVALUATION_ERROR",
                details={"error": str(e)},
                suggestions=["Reduce number of strategies", "Simplify query"]
            ).dict()
        )


@router.post(
    "/analyze-complexity",
    response_model=ComplexityAnalysisResponse,
    summary="Analyze query complexity",
    description=(
        "Analyze the complexity of a query with detailed feature breakdown. "
        "Provides routing recommendations based on complexity analysis."
    )
)
async def analyze_complexity(
    request: ComplexityAnalysisRequest
) -> ComplexityAnalysisResponse:
    """
    Analyze query complexity with features.
    
    Returns:
    - Complexity level and score
    - Feature breakdown (reasoning depth, data requirements, etc.)
    - Recommended execution approach
    - Routing recommendations
    """
    try:
        response = await routing_service.analyze_complexity(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Complexity analysis failed",
                error_code="ANALYSIS_ERROR",
                details={"error": str(e)},
                suggestions=["Simplify query", "Break into sub-queries"]
            ).dict()
        )


@router.get(
    "/strategies",
    response_model=StrategiesListResponse,
    summary="List available strategies",
    description="Get list of available routing strategies with their characteristics."
)
async def get_strategies() -> StrategiesListResponse:
    """
    Get available routing strategies.
    
    Returns information about each strategy including:
    - Optimization focus
    - Use cases
    - Trade-offs
    - Recommendations
    """
    try:
        response = await routing_service.get_available_strategies()
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Failed to retrieve strategies",
                error_code="STRATEGY_LIST_ERROR",
                details={"error": str(e)},
                suggestions=["Retry request"]
            ).dict()
        )


@router.get(
    "/models",
    response_model=ModelsListResponse,
    summary="List available models",
    description="Get list of available models and their tier classifications."
)
async def get_models() -> ModelsListResponse:
    """
    Get available models and tiers.
    
    Returns:
    - Model specifications
    - Tier classifications
    - Provider information
    - Capabilities and costs
    """
    try:
        response = await routing_service.get_available_models()
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Failed to retrieve models",
                error_code="MODEL_LIST_ERROR",
                details={"error": str(e)},
                suggestions=["Retry request"]
            ).dict()
        )


@router.post(
    "/feedback",
    response_model=Dict[str, Any],
    summary="Submit routing feedback",
    description=(
        "Submit feedback on routing decision performance. "
        "Used for continuous learning and optimization."
    )
)
async def submit_feedback(feedback: RoutingFeedback) -> Dict[str, Any]:
    """
    Submit feedback for routing learning.
    
    Feedback is used to:
    - Improve cost predictions
    - Optimize routing strategies
    - Refine quality estimates
    - Enhance overall performance
    """
    try:
        response = await routing_service.submit_feedback(feedback)
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=MASRErrorResponse(
                error=str(e),
                error_code="INVALID_FEEDBACK",
                details={"feedback": feedback.dict()},
                suggestions=["Verify routing_id exists", "Check feedback format"]
            ).dict()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Feedback submission failed",
                error_code="FEEDBACK_ERROR",
                details={"error": str(e)},
                suggestions=["Retry submission"]
            ).dict()
        )


@router.get(
    "/status",
    response_model=RouterStatus,
    summary="Get router status",
    description=(
        "Get MASR router health and performance status. "
        "Includes metrics, model availability, and learning statistics."
    )
)
async def get_status() -> RouterStatus:
    """
    Get router health and performance.
    
    Returns:
    - Overall health status
    - Performance metrics by strategy
    - Model availability
    - Learning system metrics
    - Active supervisor count
    """
    try:
        response = await routing_service.get_router_status()
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MASRErrorResponse(
                error="Failed to retrieve status",
                error_code="STATUS_ERROR",
                details={"error": str(e)},
                suggestions=["Check service health"]
            ).dict()
        )


# WebSocket endpoint for real-time routing updates (future enhancement)
# @router.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     """
#     WebSocket for real-time routing updates.
#     
#     Future enhancement for:
#     - Live routing decisions
#     - Performance metrics streaming
#     - Learning updates
#     """
#     pass