"""
Agent Framework API Routes

REST API endpoints for direct agent interaction following research-validated
patterns from "LLMs Working in Harmony" and other foundational papers.

Implements:
- Direct agent execution
- Chain-of-Agents (sequential execution)
- Mixture-of-Agents (parallel execution with aggregation)
- Agent capability discovery and performance monitoring
"""

import logging
import statistics
from datetime import datetime
from typing import List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status, BackgroundTasks
from fastapi.responses import StreamingResponse
from structlog import get_logger

from ...models.agent_api_models import (
    AgentType,
    AgentExecutionRequest,
    AgentExecutionResponse,
    ChainOfAgentsRequest,
    ChainOfAgentsResponse,
    MixtureOfAgentsRequest,
    MixtureOfAgentsResponse,
    AgentValidationRequest,
    AgentValidationResponse,
    AgentInfo,
    AgentListResponse,
    AgentMetricsResponse,
    AgentHealthStatus,
    ExecutionMode,
)
from ..services.agent_execution_service import get_agent_execution_service

logger = get_logger()
router = APIRouter(prefix="/api/v1/agents")


@router.get("", response_model=AgentListResponse)
async def list_agents(
    include_metrics: bool = Query(False, description="Include performance metrics")
) -> AgentListResponse:
    """
    List all available agents with capabilities.
    
    Following research pattern: Agent capability discovery for optimal selection.
    """
    try:
        service = get_agent_execution_service()
        agents = await service.get_agent_list()
        
        # Calculate system metrics
        system_stats = await service.get_service_stats()
        system_health = system_stats.get("system_health", "unknown")
        
        response = AgentListResponse(
            agents=agents,
            total_agents=len(agents),
            total_capabilities=len(set(cap for agent in agents for cap in agent.capabilities)),
            supported_domains=list(set(domain for agent in agents for domain in agent.optimal_domains)),
            supported_execution_modes=[mode for mode in ExecutionMode],
            system_health=system_health,
            total_system_executions=sum(
                metrics["total_executions"] 
                for metrics in system_stats.get("agent_metrics", {}).values()
            ),
            system_uptime_seconds=0.0,  # Would be calculated from service start time
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent list"
        )


@router.get("/{agent_type}", response_model=AgentInfo)
async def get_agent_info(agent_type: AgentType) -> AgentInfo:
    """
    Get detailed information about a specific agent.
    
    Provides agent capabilities, performance characteristics, and API endpoints.
    """
    try:
        service = get_agent_execution_service()
        agents = await service.get_agent_list()
        
        agent_info = next((agent for agent in agents if agent.agent_type == agent_type), None)
        
        if not agent_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent type {agent_type.value} not found"
            )
        
        return agent_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent info for {agent_type.value}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent information"
        )


@router.post("/{agent_type}/execute", response_model=AgentExecutionResponse)
async def execute_agent(
    agent_type: AgentType,
    request: AgentExecutionRequest,
    background_tasks: BackgroundTasks,
) -> AgentExecutionResponse:
    """
    Execute single agent directly.
    
    Following research pattern: Direct agent interaction with performance tracking.
    Supports TalkHier refinement for quality assurance.
    """
    try:
        logger.info(f"Executing {agent_type.value} agent with query: {request.query[:100]}...")
        
        service = get_agent_execution_service()
        
        # Execute agent
        response = await service.execute_single_agent(agent_type, request)
        
        logger.info(
            f"Agent execution completed",
            agent_type=agent_type.value,
            execution_id=response.execution_id,
            status=response.status,
            confidence=response.confidence,
            execution_time=response.execution_time_seconds,
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Agent execution failed: {agent_type.value} - {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution failed: {str(e)}"
        )


@router.post("/chain", response_model=ChainOfAgentsResponse)
async def execute_chain_of_agents(request: ChainOfAgentsRequest) -> ChainOfAgentsResponse:
    """
    Execute Chain-of-Agents (sequential execution pattern).
    
    Based on "LLMs Working in Harmony" research: Sequential agent execution
    where each agent builds on the results of the previous agent.
    
    Example chain: Literature Review → Methodology → Analysis → Synthesis
    """
    try:
        logger.info(f"Starting Chain-of-Agents execution: {[a.value for a in request.agent_chain]}")
        
        service = get_agent_execution_service()
        
        # Execute chain
        response = await service.execute_chain_of_agents(request)
        
        logger.info(
            f"Chain-of-Agents completed",
            execution_id=response.execution_id,
            agent_chain=[a.value for a in response.agent_chain],
            status=response.status,
            overall_confidence=response.overall_confidence,
            total_time=response.total_execution_time_seconds,
            quality_improvement=response.quality_improvement,
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Chain-of-Agents execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chain execution failed: {str(e)}"
        )


@router.post("/mixture", response_model=MixtureOfAgentsResponse)
async def execute_mixture_of_agents(request: MixtureOfAgentsRequest) -> MixtureOfAgentsResponse:
    """
    Execute Mixture-of-Agents (parallel execution with aggregation).
    
    Based on "LLMs Working in Harmony" research: Parallel agent execution
    with intelligent result aggregation and consensus building.
    
    Example mixture: All agents process same query, results aggregated by consensus.
    """
    try:
        logger.info(f"Starting Mixture-of-Agents execution: {[a.value for a in request.agent_types]}")
        
        service = get_agent_execution_service()
        
        # Execute mixture
        response = await service.execute_mixture_of_agents(request)
        
        logger.info(
            f"Mixture-of-Agents completed",
            execution_id=response.execution_id,
            agent_types=[a.value for a in response.agent_types],
            status=response.status,
            consensus_score=response.consensus_score,
            parallel_efficiency=response.parallel_efficiency,
            total_time=response.total_execution_time_seconds,
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Mixture-of-Agents execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Mixture execution failed: {str(e)}"
        )


@router.post("/{agent_type}/validate", response_model=AgentValidationResponse)
async def validate_agent_input(
    agent_type: AgentType,
    request: AgentValidationRequest
) -> AgentValidationResponse:
    """
    Validate input for specific agent type.
    
    Checks query suitability, parameter validation, and provides
    optimization suggestions before execution.
    """
    try:
        logger.info(f"Validating input for {agent_type.value}")
        
        # Basic validation logic (would be enhanced with actual agent validation)
        query_length = len(request.query)
        query_suitability = min(1.0, max(0.3, (query_length - 10) / 100))  # Simple heuristic
        
        # Parameter validation (simplified)
        parameter_validation = {}
        for key, value in request.parameters.items():
            parameter_validation[key] = isinstance(value, (str, int, float, bool, list, dict))
        
        validation_score = (
            query_suitability * 0.6 +
            (sum(parameter_validation.values()) / max(len(parameter_validation), 1)) * 0.4
        )
        
        recommendations = []
        if query_length < 20:
            recommendations.append("Consider providing more detailed query for better results")
        if query_length > 1000:
            recommendations.append("Consider breaking down complex query into smaller parts")
        
        response = AgentValidationResponse(
            valid=validation_score >= 0.7,
            agent_type=agent_type,
            validation_score=validation_score,
            parameter_validation=parameter_validation,
            query_suitability=query_suitability,
            estimated_quality=min(validation_score + 0.1, 1.0),
            estimated_cost=0.01,  # Would be calculated from MASR cost estimation
            recommendations=recommendations,
            parameter_suggestions={},
            validation_issues=[] if validation_score >= 0.7 else ["Query or parameters need improvement"],
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Validation failed for {agent_type.value}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Validation failed"
        )


@router.get("/{agent_type}/metrics", response_model=AgentMetricsResponse)
async def get_agent_metrics(agent_type: AgentType) -> AgentMetricsResponse:
    """
    Get performance metrics for specific agent.
    
    Provides comprehensive performance analytics following Anthropic
    engineering approach for agent evaluation and optimization.
    """
    try:
        service = get_agent_execution_service()
        metrics = await service.get_agent_metrics(agent_type)
        
        logger.debug(f"Retrieved metrics for {agent_type.value}")
        
        return metrics
        
    except Exception as e:
        logger.error(f"Failed to get metrics for {agent_type.value}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent metrics"
        )


@router.get("/{agent_type}/health", response_model=AgentHealthStatus)
async def get_agent_health(agent_type: AgentType) -> AgentHealthStatus:
    """
    Get health status for specific agent.
    
    Provides real-time health monitoring with performance indicators
    and recovery information.
    """
    try:
        service = get_agent_execution_service()
        health = await service.get_agent_health(agent_type)
        
        logger.debug(f"Retrieved health for {agent_type.value}: {health.status}")
        
        return health
        
    except Exception as e:
        logger.error(f"Failed to get health for {agent_type.value}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent health"
        )


@router.get("/system/stats")
async def get_system_stats() -> Dict[str, Any]:
    """
    Get comprehensive system statistics.
    
    Provides system-wide performance metrics and health information
    for monitoring and optimization.
    """
    try:
        service = get_agent_execution_service()
        stats = await service.get_service_stats()
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get system stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve system statistics"
        )


@router.get("/executions/active")
async def get_active_executions() -> Dict[str, Any]:
    """
    Get information about currently active agent executions.
    
    Useful for monitoring system load and debugging performance issues.
    """
    try:
        service = get_agent_execution_service()
        
        active_executions = []
        for execution_id, execution_data in service.active_executions.items():
            active_executions.append({
                "execution_id": execution_id,
                "agent_type": execution_data.get("agent_type"),
                "status": execution_data.get("status"),
                "started_at": execution_data.get("started_at"),
                "duration_seconds": (
                    datetime.now() - execution_data["started_at"]
                ).total_seconds() if execution_data.get("started_at") else 0,
            })
        
        return {
            "active_executions": active_executions,
            "total_active": len(active_executions),
            "capacity_utilization": len(active_executions) / service.max_concurrent_executions,
            "timestamp": datetime.now().isoformat(),
        }
        
    except Exception as e:
        logger.error(f"Failed to get active executions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve active executions"
        )


# Agent-specific optimized endpoints

@router.post("/literature-review/search", response_model=AgentExecutionResponse)
async def literature_search(
    query: str = Query(..., min_length=1, max_length=500),
    max_sources: int = Query(25, ge=5, le=100),
    domains: List[str] = Query(default=[]),
) -> AgentExecutionResponse:
    """
    Optimized literature search endpoint.
    
    Specialized endpoint for literature review agent with common parameters
    exposed as query parameters for convenience.
    """
    request = AgentExecutionRequest(
        query=query,
        parameters={
            "max_sources": max_sources,
            "domains": domains,
        }
    )
    
    return await execute_agent(AgentType.LITERATURE_REVIEW, request, BackgroundTasks())


@router.post("/citation/format", response_model=AgentExecutionResponse)
async def format_citations(
    sources: List[str] = Query(..., min_items=1),
    style: str = Query("APA", regex="^(APA|MLA|Chicago)$"),
) -> AgentExecutionResponse:
    """
    Optimized citation formatting endpoint.
    
    Specialized endpoint for citation agent with style selection.
    """
    request = AgentExecutionRequest(
        query=f"Format citations in {style} style",
        parameters={
            "sources": sources,
            "citation_style": style,
        }
    )
    
    return await execute_agent(AgentType.CITATION, request, BackgroundTasks())


@router.post("/synthesis/combine", response_model=AgentExecutionResponse)
async def synthesize_findings(
    findings: List[Dict[str, Any]] = Query(..., min_items=2),
    synthesis_focus: str = Query("comprehensive", regex="^(comprehensive|comparative|thematic)$"),
) -> AgentExecutionResponse:
    """
    Optimized synthesis endpoint.
    
    Specialized endpoint for synthesis agent with focus selection.
    """
    request = AgentExecutionRequest(
        query=f"Synthesize findings with {synthesis_focus} focus",
        parameters={
            "findings": findings,
            "synthesis_focus": synthesis_focus,
        }
    )
    
    return await execute_agent(AgentType.SYNTHESIS, request, BackgroundTasks())


# Research workflow convenience endpoints

@router.post("/workflows/literature-analysis", response_model=ChainOfAgentsResponse)
async def literature_analysis_workflow(
    query: str = Query(..., min_length=10),
    domains: List[str] = Query(default=[]),
    max_sources: int = Query(25, ge=10, le=100),
) -> ChainOfAgentsResponse:
    """
    Convenience endpoint for literature analysis workflow.
    
    Implements Chain-of-Agents pattern: Literature Review → Citation → Synthesis
    """
    request = ChainOfAgentsRequest(
        query=query,
        agent_chain=[
            AgentType.LITERATURE_REVIEW,
            AgentType.CITATION,
            AgentType.SYNTHESIS,
        ],
        context={
            "domains": domains,
            "max_sources": max_sources,
            "workflow_type": "literature_analysis",
        },
        pass_intermediate_results=True,
        early_stopping=False,
    )
    
    return await execute_chain_of_agents(request)


@router.post("/workflows/comprehensive-research", response_model=MixtureOfAgentsResponse)
async def comprehensive_research_workflow(
    query: str = Query(..., min_length=10),
    domains: List[str] = Query(default=[]),
    analysis_depth: str = Query("comprehensive", regex="^(basic|comprehensive|exhaustive)$"),
) -> MixtureOfAgentsResponse:
    """
    Convenience endpoint for comprehensive research workflow.
    
    Implements Mixture-of-Agents pattern: All agents analyze query in parallel,
    results aggregated for comprehensive coverage.
    """
    # Select agents based on analysis depth
    agent_types = [AgentType.LITERATURE_REVIEW, AgentType.METHODOLOGY]
    
    if analysis_depth in ["comprehensive", "exhaustive"]:
        agent_types.extend([AgentType.COMPARATIVE_ANALYSIS, AgentType.SYNTHESIS])
    
    if analysis_depth == "exhaustive":
        agent_types.append(AgentType.CITATION)
    
    request = MixtureOfAgentsRequest(
        query=query,
        agent_types=agent_types,
        context={
            "domains": domains,
            "analysis_depth": analysis_depth,
            "workflow_type": "comprehensive_research",
        },
        aggregation_strategy="consensus",
        weight_by_confidence=True,
        consensus_threshold=0.8,
    )
    
    return await execute_mixture_of_agents(request)


# System monitoring endpoints

@router.get("/health/summary")
async def get_agents_health_summary() -> Dict[str, Any]:
    """Get health summary for all agents."""
    
    try:
        service = get_agent_execution_service()
        
        health_statuses = {}
        overall_health_scores = []
        
        for agent_type in AgentType:
            health = await service.get_agent_health(agent_type)
            health_statuses[agent_type.value] = {
                "status": health.status,
                "success_rate": health.success_rate_24h,
                "response_time": health.average_response_time_ms,
                "issues": health.current_issues,
            }
            
            # Convert status to numeric score for overall calculation
            status_scores = {"healthy": 1.0, "degraded": 0.7, "unhealthy": 0.3, "unavailable": 0.0}
            overall_health_scores.append(status_scores.get(health.status, 0.0))
        
        # Calculate overall system health
        overall_health_score = statistics.mean(overall_health_scores) if overall_health_scores else 0.0
        overall_status = "healthy" if overall_health_score >= 0.8 else "degraded" if overall_health_score >= 0.5 else "unhealthy"
        
        return {
            "overall_health": overall_status,
            "overall_health_score": overall_health_score,
            "agent_health": health_statuses,
            "total_agents": len(AgentType),
            "healthy_agents": sum(1 for health in health_statuses.values() if health["status"] == "healthy"),
            "timestamp": datetime.now().isoformat(),
        }
        
    except Exception as e:
        logger.error(f"Failed to get health summary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve health summary"
        )


# Performance analysis endpoints

@router.get("/performance/comparison")
async def compare_agent_performance(
    metric: str = Query("quality_score", regex="^(quality_score|execution_time|success_rate|cost_efficiency)$"),
    time_period_hours: int = Query(24, ge=1, le=168),  # 1 hour to 1 week
) -> Dict[str, Any]:
    """
    Compare performance across all agent types.
    
    Enables optimization and A/B testing by providing comparative
    performance analysis across the agent ecosystem.
    """
    try:
        service = get_agent_execution_service()
        
        comparison_data = {}
        
        for agent_type in AgentType:
            metrics = await service.get_agent_metrics(agent_type)
            
            if metric == "quality_score":
                value = metrics.recent_average_quality
            elif metric == "execution_time":
                value = metrics.average_execution_time_ms
            elif metric == "success_rate":
                value = metrics.recent_success_rate
            elif metric == "cost_efficiency":
                value = metrics.cost_efficiency_score
            else:
                value = 0.0
            
            comparison_data[agent_type.value] = {
                "value": value,
                "total_executions": metrics.total_executions,
                "recent_executions": metrics.recent_executions,
            }
        
        # Calculate rankings
        sorted_agents = sorted(
            comparison_data.items(),
            key=lambda x: x[1]["value"],
            reverse=metric != "execution_time"  # Lower is better for execution time
        )
        
        rankings = {
            agent_type: rank + 1 
            for rank, (agent_type, _) in enumerate(sorted_agents)
        }
        
        return {
            "metric": metric,
            "time_period_hours": time_period_hours,
            "comparison_data": comparison_data,
            "rankings": rankings,
            "best_performing": sorted_agents[0][0] if sorted_agents else None,
            "analysis_timestamp": datetime.now().isoformat(),
        }
        
    except Exception as e:
        logger.error(f"Performance comparison failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Performance comparison failed"
        )


__all__ = ["router"]