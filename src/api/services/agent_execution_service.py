"""
Agent Execution Service

Service layer for direct agent execution following research-validated patterns:
- Chain-of-Agents (sequential execution)
- Mixture-of-Agents (parallel execution with aggregation)
- Direct agent interaction with performance tracking

Based on academic research:
- "LLMs Working in Harmony" - Chain and Mixture patterns
- "Talk Structurally, Act Hierarchically" - Quality assurance
- Anthropic Engineering approach - Built-in evaluation
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import statistics

from tenacity import retry, stop_after_attempt, wait_exponential

from ...agents.factory import AgentFactory
from ...agents.base import BaseAgent
from ...agents.models import AgentTask, AgentResult
from ...models.agent_api_models import (
    AgentType, 
    AgentExecutionRequest, 
    AgentExecutionResponse,
    ChainOfAgentsRequest,
    ChainOfAgentsResponse,
    MixtureOfAgentsRequest, 
    MixtureOfAgentsResponse,
    ExecutionMode,
    ChainStep,
    AgentContribution,
    AgentInfo,
    AgentCapability,
    AgentMetricsResponse,
    AgentHealthStatus,
)

logger = logging.getLogger(__name__)


class AgentExecutionService:
    """
    Service for executing agents using research-validated patterns.
    
    Implements Chain-of-Agents, Mixture-of-Agents, and direct execution
    with built-in performance tracking and quality assurance.
    """
    
    def __init__(self, agent_factory: Optional[AgentFactory] = None):
        """Initialize agent execution service."""
        
        # Agent management
        self.agent_factory = agent_factory or AgentFactory()
        
        # Execution tracking
        self.active_executions: Dict[str, Dict[str, Any]] = {}
        self.execution_history: Dict[str, AgentExecutionResponse] = {}
        
        # Performance tracking per agent type
        self.agent_metrics: Dict[AgentType, Dict[str, Any]] = {
            agent_type: {
                "total_executions": 0,
                "successful_executions": 0, 
                "failed_executions": 0,
                "total_execution_time": 0.0,
                "total_quality_score": 0.0,
                "total_cost": 0.0,
                "last_execution": None,
                "quality_history": [],  # Last 100 executions
                "performance_history": [],  # Last 100 executions
            }
            for agent_type in AgentType
        }
        
        # Service configuration
        self.max_concurrent_executions = 100
        self.default_timeout_seconds = 300
        self.enable_performance_tracking = True
        
        # Agent type to class mapping
        self.agent_type_mapping = {
            AgentType.LITERATURE_REVIEW: "LiteratureReviewAgent",
            AgentType.CITATION: "CitationAgent", 
            AgentType.METHODOLOGY: "MethodologyAgent",
            AgentType.COMPARATIVE_ANALYSIS: "ComparativeAnalysisAgent",
            AgentType.SYNTHESIS: "SynthesisAgent",
        }
    
    async def execute_single_agent(
        self, 
        agent_type: AgentType,
        request: AgentExecutionRequest
    ) -> AgentExecutionResponse:
        """
        Execute single agent following direct interaction pattern.
        
        Args:
            agent_type: Type of agent to execute
            request: Execution request with query and parameters
            
        Returns:
            Agent execution response with results and metrics
        """
        
        execution_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        # Check capacity
        if len(self.active_executions) >= self.max_concurrent_executions:
            raise RuntimeError(f"Maximum concurrent executions ({self.max_concurrent_executions}) reached")
        
        # Track execution
        execution_context = {
            "execution_id": execution_id,
            "agent_type": agent_type.value,
            "status": "pending",
            "started_at": start_time,
            "request": request.dict(),
        }
        self.active_executions[execution_id] = execution_context
        
        try:
            execution_context["status"] = "running"
            
            # Get agent instance
            agent = await self._get_agent_instance(agent_type)
            
            # Create agent task
            task = AgentTask(
                id=f"api_{execution_id}",
                agent_type=agent_type.value,
                input_data={
                    "query": request.query,
                    "context": request.context,
                    "parameters": request.parameters,
                    "quality_threshold": request.quality_threshold,
                    "enable_refinement": request.enable_refinement,
                    "max_refinement_rounds": request.max_refinement_rounds,
                }
            )
            
            # Execute with timeout
            agent_result = await asyncio.wait_for(
                agent.execute(task),
                timeout=request.timeout_seconds
            )
            
            # Build response
            execution_time = (datetime.now() - start_time).total_seconds()
            
            response = AgentExecutionResponse(
                execution_id=execution_id,
                agent_type=agent_type,
                status=agent_result.status,
                output=agent_result.output if isinstance(agent_result.output, dict) else {"result": agent_result.output},
                confidence=agent_result.confidence,
                quality_score=agent_result.confidence,  # Use confidence as quality proxy
                execution_time_seconds=execution_time,
                started_at=start_time,
                completed_at=datetime.now(),
                refinement_rounds=0,  # Would be extracted from agent metadata
                consensus_achieved=True,  # Single agent always achieves "consensus"
            )
            
            # Update metrics
            await self._update_agent_metrics(agent_type, response, success=True)
            
            # Store in history
            self.execution_history[execution_id] = response
            
            logger.info(f"Single agent execution completed: {agent_type.value} in {execution_time:.2f}s")
            
            return response
            
        except asyncio.TimeoutError:
            logger.error(f"Agent execution timed out: {agent_type.value} after {request.timeout_seconds}s")
            
            response = AgentExecutionResponse(
                execution_id=execution_id,
                agent_type=agent_type,
                status="failed",
                output={"error": "Execution timed out"},
                confidence=0.0,
                quality_score=0.0,
                execution_time_seconds=(datetime.now() - start_time).total_seconds(),
                started_at=start_time,
                completed_at=datetime.now(),
                errors=[f"Execution timed out after {request.timeout_seconds} seconds"],
            )
            
            await self._update_agent_metrics(agent_type, response, success=False)
            
            return response
            
        except Exception as e:
            logger.error(f"Agent execution failed: {agent_type.value} - {e}")
            
            response = AgentExecutionResponse(
                execution_id=execution_id,
                agent_type=agent_type,
                status="failed",
                output={"error": str(e)},
                confidence=0.0,
                quality_score=0.0,
                execution_time_seconds=(datetime.now() - start_time).total_seconds(),
                started_at=start_time,
                completed_at=datetime.now(),
                errors=[str(e)],
            )
            
            await self._update_agent_metrics(agent_type, response, success=False)
            
            return response
        
        finally:
            # Clean up tracking
            if execution_id in self.active_executions:
                del self.active_executions[execution_id]
    
    async def execute_chain_of_agents(
        self, 
        request: ChainOfAgentsRequest
    ) -> ChainOfAgentsResponse:
        """
        Execute Chain-of-Agents following sequential pattern.
        
        Based on "LLMs Working in Harmony" research - agents execute
        sequentially with each agent building on the previous results.
        
        Args:
            request: Chain execution request
            
        Returns:
            Chain execution response with intermediate and final results
        """
        
        execution_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        logger.info(f"Starting Chain-of-Agents execution: {[a.value for a in request.agent_chain]}")
        
        # Initialize chain response
        response = ChainOfAgentsResponse(
            execution_id=execution_id,
            status="running",
            agent_chain=request.agent_chain,
            intermediate_results=[],
            final_result={},
            overall_confidence=0.0,
            total_execution_time_seconds=0.0,
            agent_execution_times=[],
            chain_quality_score=0.0,
            quality_improvement=0.0,
            started_at=start_time,
        )
        
        try:
            accumulated_context = request.context.copy()
            quality_scores = []
            confidence_scores = []
            
            # Execute agents sequentially
            for step_number, agent_type in enumerate(request.agent_chain):
                step_start = datetime.now()
                
                # Create agent execution request
                agent_request = AgentExecutionRequest(
                    query=request.query,
                    context=accumulated_context,
                    parameters={},
                    timeout_seconds=request.timeout_per_agent_seconds,
                    enable_refinement=False,  # No refinement in chain steps
                )
                
                # Execute agent
                agent_response = await self.execute_single_agent(agent_type, agent_request)
                step_time = (datetime.now() - step_start).total_seconds()
                
                # Record step results
                response.intermediate_results.append(agent_response.output)
                response.agent_execution_times.append(step_time)
                quality_scores.append(agent_response.quality_score)
                confidence_scores.append(agent_response.confidence)
                
                # Check for early stopping
                if (request.early_stopping and 
                    agent_response.quality_score < request.quality_threshold):
                    
                    response.early_stopped = True
                    response.stopped_at_agent = agent_type
                    logger.info(f"Chain early stopped at {agent_type.value} due to low quality")
                    break
                
                # Accumulate context for next agent (if enabled)
                if request.pass_intermediate_results:
                    accumulated_context[f"previous_{agent_type.value}_result"] = agent_response.output
                    accumulated_context["chain_step"] = step_number + 1
            
            # Calculate final results
            response.final_result = response.intermediate_results[-1] if response.intermediate_results else {}
            response.overall_confidence = statistics.mean(confidence_scores) if confidence_scores else 0.0
            response.chain_quality_score = statistics.mean(quality_scores) if quality_scores else 0.0
            
            # Calculate quality improvement
            if len(quality_scores) > 1:
                response.quality_improvement = quality_scores[-1] - quality_scores[0]
            
            response.status = "completed"
            response.completed_at = datetime.now()
            response.total_execution_time_seconds = (
                response.completed_at - response.started_at
            ).total_seconds()
            
            logger.info(f"Chain-of-Agents completed: {len(request.agent_chain)} agents in {response.total_execution_time_seconds:.2f}s")
            
            return response
            
        except Exception as e:
            logger.error(f"Chain-of-Agents execution failed: {e}")
            
            response.status = "failed"
            response.errors = [str(e)]
            response.completed_at = datetime.now()
            response.total_execution_time_seconds = (
                response.completed_at - response.started_at
            ).total_seconds()
            
            return response
    
    async def execute_mixture_of_agents(
        self,
        request: MixtureOfAgentsRequest
    ) -> MixtureOfAgentsResponse:
        """
        Execute Mixture-of-Agents following parallel pattern.
        
        Based on "LLMs Working in Harmony" research - agents execute
        in parallel and results are intelligently aggregated.
        
        Args:
            request: Mixture execution request
            
        Returns:
            Mixture execution response with aggregated results
        """
        
        execution_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        logger.info(f"Starting Mixture-of-Agents execution: {[a.value for a in request.agent_types]}")
        
        # Initialize response
        response = MixtureOfAgentsResponse(
            execution_id=execution_id,
            status="running",
            agent_types=request.agent_types,
            agent_results={},
            aggregated_result={},
            consensus_score=0.0,
            aggregation_strategy=request.aggregation_strategy,
            agent_weights={},
            consensus_achieved=False,
            total_execution_time_seconds=0.0,
            parallel_efficiency=0.0,
            mixture_quality_score=0.0,
            inter_agent_agreement=0.0,
            started_at=start_time,
        )
        
        try:
            # Create agent execution tasks
            agent_tasks = []
            
            for agent_type in request.agent_types:
                agent_request = AgentExecutionRequest(
                    query=request.query,
                    context=request.context,
                    timeout_seconds=min(request.timeout_seconds, 600),
                    enable_refinement=False,  # No refinement in mixture
                )
                
                task = asyncio.create_task(
                    self.execute_single_agent(agent_type, agent_request),
                    name=f"mixture_agent_{agent_type.value}"
                )
                agent_tasks.append((agent_type, task))
            
            # Execute agents in parallel (with concurrency limit)
            semaphore = asyncio.Semaphore(request.max_parallel)
            
            async def execute_with_limit(agent_type, task) -> AgentExecutionResponse:
                async with semaphore:
                    return await task
            
            # Wait for all agents to complete
            agent_results = {}
            execution_times = {}
            
            for agent_type, task in agent_tasks:
                try:
                    agent_response = await execute_with_limit(agent_type, task)
                    agent_results[agent_type.value] = agent_response
                    execution_times[agent_type.value] = agent_response.execution_time_seconds
                except Exception as e:
                    logger.error(f"Mixture agent {agent_type.value} failed: {e}")
                    # Continue with other agents
                    continue
            
            # Aggregate results using specified strategy
            aggregated_result = await self._aggregate_mixture_results(
                agent_results,
                request.aggregation_strategy,
                request.weight_by_confidence,
                request.consensus_threshold
            )
            
            # Build final response
            response.agent_results = {
                agent_type: result.output for agent_type, result in agent_results.items()
            }
            response.aggregated_result = aggregated_result["result"]
            response.consensus_score = aggregated_result["consensus_score"]
            response.consensus_achieved = aggregated_result["consensus_achieved"]
            response.agent_weights = aggregated_result["weights"]
            
            # Calculate performance metrics
            if execution_times:
                response.total_execution_time_seconds = max(execution_times.values())  # Parallel execution
                theoretical_sequential_time = sum(execution_times.values())
                response.parallel_efficiency = theoretical_sequential_time / response.total_execution_time_seconds
            
            # Calculate quality metrics
            quality_scores = [result.quality_score for result in agent_results.values()]
            if quality_scores:
                response.mixture_quality_score = statistics.mean(quality_scores)
                response.inter_agent_agreement = 1.0 - statistics.stdev(quality_scores) if len(quality_scores) > 1 else 1.0
            
            response.status = "completed"
            response.completed_at = datetime.now()
            
            logger.info(f"Mixture-of-Agents completed: {len(request.agent_types)} agents, consensus: {response.consensus_score:.3f}")
            
            return response
            
        except Exception as e:
            logger.error(f"Mixture-of-Agents execution failed: {e}")
            
            response.status = "failed"
            response.errors = [str(e)]
            response.completed_at = datetime.now()
            response.total_execution_time_seconds = (
                response.completed_at - response.started_at
            ).total_seconds()
            
            return response
    
    async def _aggregate_mixture_results(
        self,
        agent_results: Dict[str, AgentExecutionResponse],
        strategy: str,
        weight_by_confidence: bool,
        consensus_threshold: float
    ) -> Dict[str, Any]:
        """Aggregate results from mixture of agents."""
        
        if not agent_results:
            return {
                "result": {},
                "consensus_score": 0.0,
                "consensus_achieved": False,
                "weights": {},
            }
        
        # Calculate weights
        if weight_by_confidence:
            total_confidence = sum(result.confidence for result in agent_results.values())
            weights = {
                agent_type: result.confidence / total_confidence
                for agent_type, result in agent_results.items()
            } if total_confidence > 0 else {
                agent_type: 1.0 / len(agent_results)
                for agent_type in agent_results.keys()
            }
        else:
            weights = {
                agent_type: 1.0 / len(agent_results)
                for agent_type in agent_results.keys()
            }
        
        # Aggregate based on strategy
        if strategy == "consensus":
            aggregated = await self._consensus_aggregation(agent_results, weights)
        elif strategy == "weighted_average":
            aggregated = await self._weighted_average_aggregation(agent_results, weights)
        elif strategy == "best_quality":
            aggregated = await self._best_quality_aggregation(agent_results)
        else:
            # Default to consensus
            aggregated = await self._consensus_aggregation(agent_results, weights)
        
        # Calculate consensus score
        confidence_scores = [result.confidence for result in agent_results.values()]
        consensus_score = 1.0 - statistics.stdev(confidence_scores) if len(confidence_scores) > 1 else 1.0
        consensus_achieved = consensus_score >= consensus_threshold
        
        return {
            "result": aggregated,
            "consensus_score": consensus_score,
            "consensus_achieved": consensus_achieved,
            "weights": weights,
        }
    
    async def _consensus_aggregation(
        self, 
        agent_results: Dict[str, AgentExecutionResponse],
        weights: Dict[str, float]
    ) -> Dict[str, Any]:
        """Aggregate results using consensus method."""
        
        # Simplified consensus: combine all outputs weighted by confidence
        aggregated = {
            "consensus_method": "weighted_combination",
            "agent_contributions": {},
            "synthesized_findings": [],
            "confidence_weighted_summary": "",
        }
        
        # Add each agent's contribution
        for agent_type, result in agent_results.items():
            weight = weights.get(agent_type, 0.0)
            
            aggregated["agent_contributions"][agent_type] = {
                "output": result.output,
                "confidence": result.confidence,
                "weight": weight,
            }
            
            # Extract key findings for synthesis
            if isinstance(result.output, dict):
                findings = result.output.get("findings", result.output.get("analysis", []))
                if isinstance(findings, list):
                    aggregated["synthesized_findings"].extend(findings)
        
        return aggregated
    
    async def _weighted_average_aggregation(
        self,
        agent_results: Dict[str, AgentExecutionResponse], 
        weights: Dict[str, float]
    ) -> Dict[str, Any]:
        """Aggregate results using weighted average method."""
        
        aggregated = {
            "aggregation_method": "weighted_average",
            "weighted_results": {},
            "overall_confidence": 0.0,
        }
        
        total_weighted_confidence = 0.0
        
        for agent_type, result in agent_results.items():
            weight = weights.get(agent_type, 0.0)
            weighted_confidence = result.confidence * weight
            total_weighted_confidence += weighted_confidence
            
            aggregated["weighted_results"][agent_type] = {
                "output": result.output,
                "confidence": result.confidence,
                "weight": weight,
                "weighted_contribution": weighted_confidence,
            }
        
        aggregated["overall_confidence"] = total_weighted_confidence
        
        return aggregated
    
    async def _best_quality_aggregation(
        self, agent_results: Dict[str, AgentExecutionResponse]
    ) -> Dict[str, Any]:
        """Aggregate by selecting best quality result."""
        
        # Find agent with highest quality score
        best_agent = max(
            agent_results.items(),
            key=lambda x: x[1].quality_score
        )
        
        best_agent_type, best_result = best_agent
        
        aggregated = {
            "aggregation_method": "best_quality",
            "selected_agent": best_agent_type,
            "primary_result": best_result.output,
            "quality_score": best_result.quality_score,
            "alternative_results": {
                agent_type: result.output
                for agent_type, result in agent_results.items()
                if agent_type != best_agent_type
            },
        }
        
        return aggregated
    
    async def get_agent_list(self) -> List[AgentInfo]:
        """Get list of available agents with capabilities."""
        
        agents = []
        
        # Define agent information (would be populated from agent registry)
        agent_info_map = {
            AgentType.LITERATURE_REVIEW: {
                "name": "Literature Review Agent",
                "description": "Searches and analyzes academic literature from multiple databases",
                "capabilities": [
                    AgentCapability.DATABASE_SEARCH,
                    AgentCapability.SOURCE_EVALUATION,
                ],
                "complexity_handling": ["simple", "moderate", "complex"],
                "optimal_domains": ["research", "academic", "literature"],
                "average_execution_time_ms": 45000,
                "reliability_score": 0.95,
                "quality_score": 0.90,
            },
            AgentType.CITATION: {
                "name": "Citation & Verification Agent",
                "description": "Verifies sources and formats citations in multiple styles",
                "capabilities": [
                    AgentCapability.CITATION_FORMATTING,
                    AgentCapability.SOURCE_EVALUATION,
                ],
                "complexity_handling": ["simple", "moderate"],
                "optimal_domains": ["citation", "verification", "academic"],
                "average_execution_time_ms": 20000,
                "reliability_score": 0.98,
                "quality_score": 0.85,
            },
            AgentType.METHODOLOGY: {
                "name": "Methodology Agent", 
                "description": "Designs research methodology and identifies potential biases",
                "capabilities": [
                    AgentCapability.RESEARCH_DESIGN,
                    AgentCapability.BIAS_DETECTION,
                ],
                "complexity_handling": ["moderate", "complex"],
                "optimal_domains": ["methodology", "research_design", "validation"],
                "average_execution_time_ms": 30000,
                "reliability_score": 0.92,
                "quality_score": 0.88,
            },
            AgentType.COMPARATIVE_ANALYSIS: {
                "name": "Comparative Analysis Agent",
                "description": "Compares different approaches and synthesizes contrasting viewpoints",
                "capabilities": [
                    AgentCapability.FRAMEWORK_COMPARISON,
                    AgentCapability.EVIDENCE_SYNTHESIS,
                ],
                "complexity_handling": ["moderate", "complex"],
                "optimal_domains": ["comparison", "analysis", "evaluation"],
                "average_execution_time_ms": 35000,
                "reliability_score": 0.90,
                "quality_score": 0.87,
            },
            AgentType.SYNTHESIS: {
                "name": "Synthesis Agent",
                "description": "Integrates findings and creates coherent narratives",
                "capabilities": [
                    AgentCapability.INTEGRATION,
                    AgentCapability.NARRATIVE_BUILDING,
                ],
                "complexity_handling": ["moderate", "complex"],
                "optimal_domains": ["synthesis", "integration", "narrative"],
                "average_execution_time_ms": 40000,
                "reliability_score": 0.93,
                "quality_score": 0.91,
            },
        }
        
        for agent_type, info in agent_info_map.items():
            agents.append(AgentInfo(
                agent_type=agent_type,
                name=info["name"],
                description=info["description"],
                capabilities=info["capabilities"],
                average_execution_time_ms=info["average_execution_time_ms"],
                reliability_score=info["reliability_score"],
                quality_score=info["quality_score"],
                complexity_handling=info["complexity_handling"],
                optimal_domains=info["optimal_domains"],
                endpoints=[
                    f"/api/v1/agents/{agent_type.value}/execute",
                    f"/api/v1/agents/{agent_type.value}",
                    f"/api/v1/agents/{agent_type.value}/metrics",
                ]
            ))
        
        return agents
    
    async def get_agent_metrics(self, agent_type: AgentType) -> AgentMetricsResponse:
        """Get performance metrics for specific agent type."""
        
        metrics_data = self.agent_metrics.get(agent_type, {})
        
        # Calculate derived metrics
        total_executions = metrics_data.get("total_executions", 0)
        successful_executions = metrics_data.get("successful_executions", 0)
        success_rate = successful_executions / max(total_executions, 1)
        
        total_time = metrics_data.get("total_execution_time", 0.0)
        avg_execution_time = (total_time / max(total_executions, 1)) * 1000  # Convert to ms
        
        total_quality = metrics_data.get("total_quality_score", 0.0)
        avg_quality = total_quality / max(successful_executions, 1)
        
        # Calculate trends (simplified)
        quality_history = metrics_data.get("quality_history", [])
        quality_trend = 0.0
        reliability_trend = 0.0
        
        if len(quality_history) > 5:
            recent_quality = quality_history[-5:]
            early_quality = quality_history[:5] if len(quality_history) >= 10 else quality_history
            quality_trend = statistics.mean(recent_quality) - statistics.mean(early_quality)
        
        return AgentMetricsResponse(
            agent_type=agent_type,
            total_executions=total_executions,
            successful_executions=successful_executions,
            success_rate=success_rate,
            average_execution_time_ms=avg_execution_time,
            average_quality_score=avg_quality,
            average_cost_per_execution=0.01,  # Would be calculated from actual costs
            total_cost_last_30_days=total_executions * 0.01,  # Simplified cost calculation
            cost_efficiency_score=0.85,  # Would be calculated from cost vs quality
            peak_usage_hour=14,  # Would be calculated from usage patterns
            most_common_domains=["research", "academic"],  # Would be tracked
            complexity_distribution={"simple": 30, "moderate": 50, "complex": 20},
            quality_trend_7_days=quality_trend,
            reliability_trend_7_days=reliability_trend,
            recent_executions=len([h for h in quality_history if h]),  # Simplified
            recent_success_rate=success_rate,  # Would calculate from recent data
            recent_average_quality=avg_quality,  # Would calculate from recent data
            last_updated=datetime.now(),
        )
    
    async def get_agent_health(self, agent_type: AgentType) -> AgentHealthStatus:
        """Get health status for specific agent type."""
        
        metrics = await self.get_agent_metrics(agent_type)
        
        # Determine health status
        if metrics.recent_success_rate >= 0.95 and metrics.average_execution_time_ms < 60000:
            status = "healthy"
        elif metrics.recent_success_rate >= 0.8 and metrics.average_execution_time_ms < 120000:
            status = "degraded"
        elif metrics.recent_success_rate >= 0.5:
            status = "unhealthy"
        else:
            status = "unavailable"
        
        return AgentHealthStatus(
            agent_type=agent_type,
            status=status,
            success_rate_24h=metrics.recent_success_rate,
            average_response_time_ms=metrics.average_execution_time_ms,
            error_rate=1.0 - metrics.recent_success_rate,
            resource_utilization=len(self.active_executions) / max(self.max_concurrent_executions, 1),
            queue_length=len([ex for ex in self.active_executions.values() if ex["status"] == "pending"]),
            current_issues=[],  # Would be populated from actual health checks
            last_health_check=datetime.now(),
        )
    
    async def _update_agent_metrics(
        self,
        agent_type: AgentType,
        response: AgentExecutionResponse,
        success: bool
    ) -> None:
        """Update performance metrics for agent type."""
        
        if not self.enable_performance_tracking:
            return
        
        metrics = self.agent_metrics[agent_type]
        
        # Update counters
        metrics["total_executions"] += 1
        if success:
            metrics["successful_executions"] += 1
        else:
            metrics["failed_executions"] += 1
        
        # Update totals
        metrics["total_execution_time"] += response.execution_time_seconds
        if success:
            metrics["total_quality_score"] += response.quality_score
            
            # Add to history (keep last 100)
            metrics["quality_history"].append(response.quality_score)
            if len(metrics["quality_history"]) > 100:
                metrics["quality_history"].pop(0)
            
            metrics["performance_history"].append(response.execution_time_seconds)
            if len(metrics["performance_history"]) > 100:
                metrics["performance_history"].pop(0)
        
        metrics["last_execution"] = datetime.now().isoformat()
    
    async def _get_agent_instance(self, agent_type: AgentType) -> BaseAgent:
        """Get agent instance from factory."""
        
        agent_class_name = self.agent_type_mapping.get(agent_type)
        if not agent_class_name:
            raise ValueError(f"Unknown agent type: {agent_type.value}")
        
        # Get agent from factory
        agent = self.agent_factory.create_agent(agent_class_name)
        if not agent:
            raise RuntimeError(f"Failed to create agent: {agent_type.value}")
        
        return agent
    
    async def get_service_stats(self) -> Dict[str, Any]:
        """Get comprehensive service statistics."""
        
        return {
            "service": {
                "active_executions": len(self.active_executions),
                "total_agents": len(AgentType),
                "max_concurrent": self.max_concurrent_executions,
            },
            "agent_metrics": {
                agent_type.value: {
                    "total_executions": metrics["total_executions"],
                    "success_rate": metrics["successful_executions"] / max(metrics["total_executions"], 1),
                    "avg_execution_time": metrics["total_execution_time"] / max(metrics["total_executions"], 1),
                }
                for agent_type, metrics in self.agent_metrics.items()
            },
            "system_health": await self._get_system_health(),
        }
    
    async def _get_system_health(self) -> str:
        """Calculate overall system health."""
        
        health_statuses = []
        
        for agent_type in AgentType:
            health = await self.get_agent_health(agent_type)
            health_statuses.append(health.status)
        
        if all(status == "healthy" for status in health_statuses):
            return "healthy"
        elif any(status == "unavailable" for status in health_statuses):
            return "unhealthy"
        else:
            return "degraded"


# Global service instance
_agent_execution_service: Optional[AgentExecutionService] = None


def get_agent_execution_service() -> AgentExecutionService:
    """Get global agent execution service instance."""
    global _agent_execution_service
    
    if _agent_execution_service is None:
        _agent_execution_service = AgentExecutionService()
    
    return _agent_execution_service


__all__ = [
    "AgentExecutionService",
    "get_agent_execution_service",
]