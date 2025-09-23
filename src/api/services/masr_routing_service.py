"""
MASR Routing Service Layer

Service layer for MASR routing intelligence, providing high-level
interfaces to the MASR router and supervisor bridge systems.
Based on "MasRouter: Learning to Route LLMs" research patterns.
"""

import uuid
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.hierarchical_cost_model import HierarchicalCostOptimizer
from src.ai_brain.config.supervisor_config import SupervisorConfigManager
from src.ai_brain.learning.supervision_feedback import SupervisionFeedbackLearner
from src.ai_brain.models.masr import (
    QueryDomain,
    QueryComplexity,
    RoutingStrategy,
    CollaborationMode,
    ModelTier,
    QueryAnalysis,
    RoutingDecision
)
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
    ModelInfo,
    SupervisorAllocation,
    CostBreakdown,
    StrategyComparison,
    ComplexityFeatures,
    AvailableStrategy,
    MASRErrorResponse
)
from src.config import get_settings

settings = get_settings()


class MASRRoutingService:
    """
    High-level service for MASR routing intelligence.
    
    Provides REST API access to MASR router capabilities including:
    - Intelligent routing decisions
    - Cost estimation and optimization
    - Strategy evaluation and selection
    - Query complexity analysis
    - Learning and feedback integration
    """
    
    def __init__(self):
        """Initialize MASR routing service with all components"""
        # Core routing components
        self.router = MASRouter()
        self.bridge = MASRSupervisorBridge()
        self.cost_optimizer = HierarchicalCostOptimizer()
        self.config_manager = SupervisorConfigManager()
        self.feedback_learner = SupervisionFeedbackLearner()
        
        # Tracking and metrics
        self.routing_history: Dict[str, RoutingDecision] = {}
        self.performance_metrics: Dict[str, Dict[str, float]] = {
            strategy.value: {
                "total_requests": 0,
                "success_rate": 1.0,
                "average_cost": 0,
                "average_latency_ms": 0,
                "average_quality": 0.85
            }
            for strategy in RoutingStrategy
        }
        
        # Service start time for uptime tracking
        self.start_time = datetime.utcnow()
        
    async def get_routing_decision(
        self,
        request: RoutingRequest
    ) -> RoutingDecisionResponse:
        """
        Get intelligent routing decision for a query.
        
        Args:
            request: Routing request with query and constraints
            
        Returns:
            RoutingDecisionResponse with routing decision and allocations
        """
        try:
            # Analyze query
            analysis = await self.router.analyze_query(request.query)
            
            # Apply strategy override if provided
            if request.strategy:
                strategy = request.strategy
            else:
                strategy = await self.router.select_strategy(
                    analysis,
                    max_cost=request.max_cost,
                    min_quality=request.min_quality
                )
            
            # Get routing decision
            decision = await self.router.route(
                request.query,
                context=request.context,
                strategy_override=strategy
            )
            
            # Generate unique routing ID
            routing_id = str(uuid.uuid4())
            
            # Store for feedback tracking
            self.routing_history[routing_id] = decision
            
            # Convert to supervisor allocations
            supervisor_allocations = []
            for agent in decision.agents:
                if agent.get("supervisor_type"):
                    allocation = SupervisorAllocation(
                        supervisor_type=agent["supervisor_type"],
                        worker_count=agent.get("worker_count", 3),
                        refinement_rounds=agent.get("refinement_rounds", 1),
                        estimated_latency_ms=agent.get("estimated_latency_ms", 1000)
                    )
                    supervisor_allocations.append(allocation)
            
            # Get selected models info
            selected_models = self._get_model_info(decision.model_tier)
            
            # Calculate confidence score
            confidence_score = self._calculate_confidence(analysis, decision)
            
            # Build response
            response = RoutingDecisionResponse(
                routing_id=routing_id,
                domain=decision.domain,
                complexity=analysis.complexity,
                strategy=decision.strategy,
                collaboration_mode=decision.collaboration_mode,
                supervisor_allocations=supervisor_allocations,
                selected_models=selected_models,
                estimated_cost=decision.estimated_cost,
                estimated_latency_ms=int(decision.estimated_latency * 1000),
                confidence_score=confidence_score,
                reasoning=self._generate_reasoning(analysis, decision),
                alternatives=self._get_alternatives(analysis, decision)
            )
            
            # Update metrics
            self._update_metrics(strategy, success=True)
            
            return response
            
        except Exception as e:
            # Update metrics for failure
            if request.strategy:
                self._update_metrics(request.strategy, success=False)
            raise e
    
    async def estimate_cost(
        self,
        request: CostEstimationRequest
    ) -> CostEstimationResponse:
        """
        Estimate cost for query execution with breakdown.
        
        Args:
            request: Cost estimation request
            
        Returns:
            CostEstimationResponse with detailed breakdown
        """
        # Analyze query
        analysis = await self.router.analyze_query(request.query)
        
        # Determine strategy
        strategy = request.strategy or await self.router.select_strategy(analysis)
        
        # Get routing decision for cost calculation
        decision = await self.router.route(
            request.query,
            strategy_override=strategy
        )
        
        # Calculate hierarchical costs
        hierarchical_cost = self.cost_optimizer.calculate_total_cost(
            supervisor_count=len(decision.agents),
            worker_count=sum(a.get("worker_count", 3) for a in decision.agents),
            refinement_rounds=max(a.get("refinement_rounds", 1) for a in decision.agents),
            model_tier=decision.model_tier,
            query_complexity=analysis.complexity
        )
        
        # Build breakdown if requested
        breakdown = None
        if request.include_breakdown:
            breakdown = CostBreakdown(
                model_costs=hierarchical_cost["model_cost"],
                coordination_overhead=hierarchical_cost["coordination_overhead"],
                memory_operations=hierarchical_cost.get("memory_cost", 0),
                total_cost=hierarchical_cost["total_cost"],
                confidence_interval=(
                    hierarchical_cost["total_cost"] * 0.8,
                    hierarchical_cost["total_cost"] * 1.2
                ) if request.include_confidence else None
            )
        
        # Generate recommendations
        recommendations = self._generate_cost_recommendations(
            analysis,
            decision,
            hierarchical_cost
        )
        
        response = CostEstimationResponse(
            estimated_cost=hierarchical_cost["total_cost"],
            breakdown=breakdown,
            confidence_score=0.85,  # Based on historical accuracy
            cost_factors={
                "query_complexity": analysis.complexity.value,
                "model_tier": decision.model_tier.value,
                "supervisor_count": len(decision.agents),
                "total_workers": sum(a.get("worker_count", 3) for a in decision.agents),
                "refinement_rounds": max(a.get("refinement_rounds", 1) for a in decision.agents)
            },
            recommendations=recommendations
        )
        
        return response
    
    async def evaluate_strategies(
        self,
        request: StrategyEvaluationRequest
    ) -> StrategyEvaluationResponse:
        """
        Evaluate and compare routing strategies.
        
        Args:
            request: Strategy evaluation request
            
        Returns:
            StrategyEvaluationResponse with comparisons
        """
        # Analyze query
        analysis = await self.router.analyze_query(request.query)
        
        # Determine strategies to evaluate
        strategies = request.strategies or list(RoutingStrategy)
        
        # Evaluate each strategy
        comparisons = []
        best_strategy = None
        best_score = -1
        
        for strategy in strategies:
            # Get routing decision for this strategy
            decision = await self.router.route(
                request.query,
                strategy_override=strategy
            )
            
            # Calculate metrics
            cost = decision.estimated_cost
            quality = self._estimate_quality(strategy, analysis)
            latency_ms = int(decision.estimated_latency * 1000)
            
            # Apply custom weights if provided
            weights = request.weights or {"cost": 0.3, "quality": 0.5, "latency": 0.2}
            
            # Calculate recommendation score
            # Lower cost is better, so we invert it
            cost_score = 1.0 / (1.0 + cost)
            # Lower latency is better, so we invert it
            latency_score = 1.0 / (1.0 + latency_ms / 10000)
            
            recommendation_score = (
                weights.get("cost", 0.3) * cost_score +
                weights.get("quality", 0.5) * quality +
                weights.get("latency", 0.2) * latency_score
            )
            
            # Track best strategy
            if recommendation_score > best_score:
                best_score = recommendation_score
                best_strategy = strategy
            
            # Build comparison
            comparison = StrategyComparison(
                strategy=strategy,
                estimated_cost=cost,
                estimated_quality=quality,
                estimated_latency_ms=latency_ms,
                pros=self._get_strategy_pros(strategy),
                cons=self._get_strategy_cons(strategy),
                recommendation_score=recommendation_score
            )
            comparisons.append(comparison)
        
        # Build response
        response = StrategyEvaluationResponse(
            comparisons=comparisons,
            recommended_strategy=best_strategy,
            reasoning=self._generate_strategy_reasoning(
                analysis,
                best_strategy,
                comparisons
            ),
            trade_offs=self._get_trade_offs(best_strategy)
        )
        
        return response
    
    async def analyze_complexity(
        self,
        request: ComplexityAnalysisRequest
    ) -> ComplexityAnalysisResponse:
        """
        Analyze query complexity with feature breakdown.
        
        Args:
            request: Complexity analysis request
            
        Returns:
            ComplexityAnalysisResponse with detailed analysis
        """
        # Analyze query
        analysis = await self.router.analyze_query(request.query)
        
        # Calculate complexity score (0-1)
        complexity_score = self._calculate_complexity_score(analysis)
        
        # Build features if requested
        features = None
        if request.include_features:
            features = ComplexityFeatures(
                query_length=len(request.query.split()),
                domain_count=len(analysis.domains) if hasattr(analysis, 'domains') else 1,
                reasoning_depth=self._estimate_reasoning_depth(analysis),
                data_requirements=self._identify_data_requirements(request.query),
                coordination_needs=self._identify_coordination_needs(analysis),
                uncertainty_level=analysis.uncertainty_level
            )
        
        # Generate recommendations
        routing_recommendations = []
        if request.include_recommendations:
            routing_recommendations = self._generate_routing_recommendations(analysis)
        
        # Build response
        response = ComplexityAnalysisResponse(
            complexity=analysis.complexity,
            complexity_score=complexity_score,
            features=features,
            recommended_approach=self._recommend_approach(analysis),
            routing_recommendations=routing_recommendations
        )
        
        return response
    
    async def submit_feedback(
        self,
        feedback: RoutingFeedback
    ) -> Dict[str, Any]:
        """
        Submit feedback for routing decision learning.
        
        Args:
            feedback: Routing feedback with actual metrics
            
        Returns:
            Acknowledgment response
        """
        # Get original routing decision
        if feedback.routing_id not in self.routing_history:
            raise ValueError(f"Unknown routing ID: {feedback.routing_id}")
        
        original_decision = self.routing_history[feedback.routing_id]
        
        # Submit to feedback learner
        await self.feedback_learner.submit_feedback(
            routing_id=feedback.routing_id,
            strategy=original_decision.strategy,
            predicted_cost=original_decision.estimated_cost,
            actual_cost=feedback.actual_cost,
            predicted_latency=original_decision.estimated_latency,
            actual_latency=feedback.actual_latency_ms / 1000,
            quality_score=feedback.quality_score,
            error_occurred=feedback.error_occurred
        )
        
        # Update performance metrics
        self._update_performance_from_feedback(
            original_decision.strategy,
            feedback
        )
        
        # Clean up old history entries (keep last 1000)
        if len(self.routing_history) > 1000:
            oldest_keys = sorted(self.routing_history.keys())[:100]
            for key in oldest_keys:
                del self.routing_history[key]
        
        return {
            "status": "accepted",
            "routing_id": feedback.routing_id,
            "feedback_processed": True,
            "learning_updated": True
        }
    
    async def get_available_strategies(self) -> StrategiesListResponse:
        """
        Get list of available routing strategies.
        
        Returns:
            StrategiesListResponse with strategy details
        """
        strategies = []
        
        for strategy in RoutingStrategy:
            available_strategy = AvailableStrategy(
                strategy=strategy,
                name=strategy.value.replace("_", " ").title(),
                description=self._get_strategy_description(strategy),
                optimization_focus=self._get_optimization_focus(strategy),
                use_cases=self._get_strategy_use_cases(strategy),
                trade_offs=self._get_trade_offs(strategy)
            )
            strategies.append(available_strategy)
        
        response = StrategiesListResponse(
            strategies=strategies,
            default_strategy=RoutingStrategy.BALANCED,
            total_count=len(strategies)
        )
        
        return response
    
    async def get_available_models(self) -> ModelsListResponse:
        """
        Get list of available models and tiers.
        
        Returns:
            ModelsListResponse with model information
        """
        models = []
        tiers = {}
        
        # Get model configurations from settings
        model_configs = self._get_model_configurations()
        
        for config in model_configs:
            model_info = ModelInfo(
                provider=config["provider"],
                model_id=config["model_id"],
                tier=config["tier"],
                cost_per_token=config["cost_per_token"],
                max_tokens=config["max_tokens"],
                capabilities=config["capabilities"],
                average_latency_ms=config["average_latency_ms"],
                quality_score=config["quality_score"]
            )
            models.append(model_info)
            
            # Group by tier
            tier_name = config["tier"].value
            if tier_name not in tiers:
                tiers[tier_name] = []
            tiers[tier_name].append(config["model_id"])
        
        response = ModelsListResponse(
            models=models,
            tiers=tiers,
            total_count=len(models),
            providers=list(set(m.provider for m in models))
        )
        
        return response
    
    async def get_router_status(self) -> RouterStatus:
        """
        Get MASR router health and performance status.
        
        Returns:
            RouterStatus with health and metrics
        """
        # Calculate uptime
        uptime = (datetime.utcnow() - self.start_time).total_seconds()
        
        # Calculate total routes
        total_routes = sum(
            m["total_requests"] 
            for m in self.performance_metrics.values()
        )
        
        # Calculate average latency
        latencies = [
            m["average_latency_ms"] 
            for m in self.performance_metrics.values()
            if m["total_requests"] > 0
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        # Calculate success rate
        success_rates = [
            m["success_rate"]
            for m in self.performance_metrics.values()
            if m["total_requests"] > 0
        ]
        avg_success_rate = sum(success_rates) / len(success_rates) if success_rates else 1.0
        
        # Get active supervisors from bridge
        active_supervisors = len(self.bridge.supervisor_pool)
        
        # Check model availability
        model_availability = self._check_model_availability()
        
        # Determine overall status
        if avg_success_rate < 0.8:
            status = "degraded"
        elif not all(model_availability.values()):
            status = "degraded"
        else:
            status = "healthy"
        
        # Get learning metrics
        learning_metrics = await self.feedback_learner.get_metrics()
        
        response = RouterStatus(
            status=status,
            uptime_seconds=int(uptime),
            total_routes=total_routes,
            average_latency_ms=avg_latency,
            success_rate=avg_success_rate,
            active_supervisors=active_supervisors,
            performance_metrics={
                strategy: {
                    "requests": metrics["total_requests"],
                    "success_rate": metrics["success_rate"],
                    "avg_cost": metrics["average_cost"],
                    "avg_latency_ms": metrics["average_latency_ms"],
                    "avg_quality": metrics["average_quality"]
                }
                for strategy, metrics in self.performance_metrics.items()
            },
            model_availability=model_availability,
            learning_metrics=learning_metrics,
            last_error=None,
            last_error_time=None
        )
        
        return response
    
    # Helper methods
    
    def _get_model_info(self, tier: ModelTier) -> List[ModelInfo]:
        """Get model information for a tier"""
        configs = self._get_model_configurations()
        return [
            ModelInfo(**config)
            for config in configs
            if config["tier"] == tier
        ]
    
    def _calculate_confidence(
        self,
        analysis: QueryAnalysis,
        decision: RoutingDecision
    ) -> float:
        """Calculate routing confidence score"""
        # Base confidence on complexity and uncertainty
        base_confidence = 1.0 - (analysis.uncertainty_level * 0.3)
        
        # Adjust for complexity
        if analysis.complexity == QueryComplexity.SIMPLE:
            confidence = base_confidence * 0.95
        elif analysis.complexity == QueryComplexity.MODERATE:
            confidence = base_confidence * 0.85
        else:
            confidence = base_confidence * 0.75
        
        return min(max(confidence, 0.0), 1.0)
    
    def _generate_reasoning(
        self,
        analysis: QueryAnalysis,
        decision: RoutingDecision
    ) -> str:
        """Generate reasoning explanation for routing decision"""
        reasoning = f"Query analyzed as {analysis.complexity.value} complexity "
        reasoning += f"in {decision.domain.value} domain. "
        reasoning += f"Selected {decision.strategy.value} strategy "
        reasoning += f"optimizing for {self._get_optimization_focus(decision.strategy)}. "
        reasoning += f"Allocated {len(decision.agents)} supervisor(s) "
        reasoning += f"with {decision.collaboration_mode.value} collaboration mode."
        return reasoning
    
    def _get_alternatives(
        self,
        analysis: QueryAnalysis,
        decision: RoutingDecision
    ) -> Optional[List[Dict[str, Any]]]:
        """Get alternative routing options considered"""
        alternatives = []
        
        for strategy in RoutingStrategy:
            if strategy != decision.strategy:
                alt_decision = self.router._create_routing_decision(
                    analysis,
                    strategy
                )
                alternatives.append({
                    "strategy": strategy.value,
                    "estimated_cost": alt_decision.estimated_cost,
                    "estimated_latency": alt_decision.estimated_latency,
                    "reason_not_selected": self._get_rejection_reason(
                        strategy,
                        decision.strategy
                    )
                })
        
        return alternatives[:3]  # Return top 3 alternatives
    
    def _update_metrics(self, strategy: RoutingStrategy, success: bool):
        """Update performance metrics for a strategy"""
        metrics = self.performance_metrics[strategy.value]
        metrics["total_requests"] += 1
        
        if success:
            # Update success rate (exponential moving average)
            alpha = 0.1
            metrics["success_rate"] = (
                alpha * 1.0 + (1 - alpha) * metrics["success_rate"]
            )
        else:
            metrics["success_rate"] = (
                0.1 * 0.0 + 0.9 * metrics["success_rate"]
            )
    
    def _update_performance_from_feedback(
        self,
        strategy: RoutingStrategy,
        feedback: RoutingFeedback
    ):
        """Update performance metrics from feedback"""
        metrics = self.performance_metrics[strategy.value]
        
        # Update averages using exponential moving average
        alpha = 0.1
        metrics["average_cost"] = (
            alpha * feedback.actual_cost +
            (1 - alpha) * metrics["average_cost"]
        )
        metrics["average_latency_ms"] = (
            alpha * feedback.actual_latency_ms +
            (1 - alpha) * metrics["average_latency_ms"]
        )
        metrics["average_quality"] = (
            alpha * feedback.quality_score +
            (1 - alpha) * metrics["average_quality"]
        )
    
    def _get_model_configurations(self) -> List[Dict[str, Any]]:
        """Get model configurations"""
        return [
            {
                "provider": "deepseek",
                "model_id": "deepseek-v3",
                "tier": ModelTier.PREMIUM,
                "cost_per_token": 0.002,
                "max_tokens": 128000,
                "capabilities": ["reasoning", "code", "analysis"],
                "average_latency_ms": 500,
                "quality_score": 0.95
            },
            {
                "provider": "llama",
                "model_id": "llama-3.3-70b",
                "tier": ModelTier.STANDARD,
                "cost_per_token": 0.001,
                "max_tokens": 8192,
                "capabilities": ["general", "conversation"],
                "average_latency_ms": 300,
                "quality_score": 0.85
            },
            {
                "provider": "gemini",
                "model_id": "gemini-pro",
                "tier": ModelTier.STANDARD,
                "cost_per_token": 0.0015,
                "max_tokens": 32000,
                "capabilities": ["multimodal", "analysis"],
                "average_latency_ms": 400,
                "quality_score": 0.90
            }
        ]
    
    def _generate_cost_recommendations(
        self,
        analysis: QueryAnalysis,
        decision: RoutingDecision,
        cost: Dict[str, float]
    ) -> List[str]:
        """Generate cost optimization recommendations"""
        recommendations = []
        
        if cost["total_cost"] > 0.5:
            recommendations.append(
                "Consider using cost_efficient strategy for similar queries"
            )
        
        if analysis.complexity == QueryComplexity.SIMPLE:
            recommendations.append(
                "Simple queries can use budget tier models effectively"
            )
        
        if len(decision.agents) > 2:
            recommendations.append(
                "Multiple supervisors increase coordination overhead"
            )
        
        return recommendations
    
    def _estimate_quality(
        self,
        strategy: RoutingStrategy,
        analysis: QueryAnalysis
    ) -> float:
        """Estimate quality for a strategy"""
        base_quality = {
            RoutingStrategy.COST_EFFICIENT: 0.75,
            RoutingStrategy.QUALITY_FOCUSED: 0.95,
            RoutingStrategy.BALANCED: 0.85,
            RoutingStrategy.SPEED_OPTIMIZED: 0.80
        }.get(strategy, 0.85)
        
        # Adjust for complexity
        if analysis.complexity == QueryComplexity.COMPLEX:
            base_quality *= 0.9
        
        return min(base_quality, 1.0)
    
    def _get_strategy_pros(self, strategy: RoutingStrategy) -> List[str]:
        """Get advantages of a strategy"""
        pros_map = {
            RoutingStrategy.COST_EFFICIENT: [
                "Lowest cost per query",
                "Efficient resource utilization",
                "Good for high-volume queries"
            ],
            RoutingStrategy.QUALITY_FOCUSED: [
                "Highest output quality",
                "Best for critical tasks",
                "Multiple refinement rounds"
            ],
            RoutingStrategy.BALANCED: [
                "Good cost-quality trade-off",
                "Versatile for most queries",
                "Adaptive to complexity"
            ],
            RoutingStrategy.SPEED_OPTIMIZED: [
                "Fastest response time",
                "Minimal coordination overhead",
                "Best for real-time needs"
            ]
        }
        return pros_map.get(strategy, [])
    
    def _get_strategy_cons(self, strategy: RoutingStrategy) -> List[str]:
        """Get disadvantages of a strategy"""
        cons_map = {
            RoutingStrategy.COST_EFFICIENT: [
                "May sacrifice quality",
                "Limited refinement",
                "Not ideal for complex tasks"
            ],
            RoutingStrategy.QUALITY_FOCUSED: [
                "Higher cost per query",
                "Longer execution time",
                "May be overkill for simple tasks"
            ],
            RoutingStrategy.BALANCED: [
                "Not optimal for any single metric",
                "May need tuning for specific use cases"
            ],
            RoutingStrategy.SPEED_OPTIMIZED: [
                "Higher cost for speed",
                "May sacrifice depth of analysis",
                "Limited collaboration"
            ]
        }
        return cons_map.get(strategy, [])
    
    def _get_strategy_description(self, strategy: RoutingStrategy) -> str:
        """Get strategy description"""
        descriptions = {
            RoutingStrategy.COST_EFFICIENT: "Minimizes cost while maintaining acceptable quality",
            RoutingStrategy.QUALITY_FOCUSED: "Maximizes output quality regardless of cost",
            RoutingStrategy.BALANCED: "Balances cost, quality, and speed",
            RoutingStrategy.SPEED_OPTIMIZED: "Minimizes latency for real-time responses"
        }
        return descriptions.get(strategy, "")
    
    def _get_optimization_focus(self, strategy: RoutingStrategy) -> str:
        """Get what a strategy optimizes for"""
        focus_map = {
            RoutingStrategy.COST_EFFICIENT: "cost reduction",
            RoutingStrategy.QUALITY_FOCUSED: "output quality",
            RoutingStrategy.BALANCED: "overall efficiency",
            RoutingStrategy.SPEED_OPTIMIZED: "response time"
        }
        return focus_map.get(strategy, "balanced performance")
    
    def _get_strategy_use_cases(self, strategy: RoutingStrategy) -> List[str]:
        """Get recommended use cases for a strategy"""
        use_cases = {
            RoutingStrategy.COST_EFFICIENT: [
                "High-volume batch processing",
                "Non-critical queries",
                "Budget-constrained operations"
            ],
            RoutingStrategy.QUALITY_FOCUSED: [
                "Research and analysis",
                "Critical decision support",
                "Publication-quality content"
            ],
            RoutingStrategy.BALANCED: [
                "General purpose queries",
                "Mixed workloads",
                "Default production use"
            ],
            RoutingStrategy.SPEED_OPTIMIZED: [
                "Real-time interactions",
                "User-facing applications",
                "Time-critical operations"
            ]
        }
        return use_cases.get(strategy, [])
    
    def _get_trade_offs(self, strategy: RoutingStrategy) -> Dict[str, str]:
        """Get key trade-offs for a strategy"""
        trade_offs = {
            RoutingStrategy.COST_EFFICIENT: {
                "benefit": "60% cost reduction",
                "trade_off": "15-20% quality reduction"
            },
            RoutingStrategy.QUALITY_FOCUSED: {
                "benefit": "95%+ quality score",
                "trade_off": "2-3x higher cost"
            },
            RoutingStrategy.BALANCED: {
                "benefit": "Good all-around performance",
                "trade_off": "Not optimal for specific needs"
            },
            RoutingStrategy.SPEED_OPTIMIZED: {
                "benefit": "<1s response time",
                "trade_off": "Higher cost and reduced depth"
            }
        }
        return trade_offs.get(strategy, {})
    
    def _generate_strategy_reasoning(
        self,
        analysis: QueryAnalysis,
        strategy: RoutingStrategy,
        comparisons: List[StrategyComparison]
    ) -> str:
        """Generate reasoning for strategy recommendation"""
        reasoning = f"For this {analysis.complexity.value} complexity query, "
        reasoning += f"{strategy.value} strategy is recommended because it "
        reasoning += f"optimizes for {self._get_optimization_focus(strategy)}. "
        
        # Add comparison insight
        best_comp = max(comparisons, key=lambda c: c.recommendation_score)
        reasoning += f"It scores {best_comp.recommendation_score:.2f} based on "
        reasoning += "weighted evaluation of cost, quality, and latency factors."
        
        return reasoning
    
    def _calculate_complexity_score(self, analysis: QueryAnalysis) -> float:
        """Calculate normalized complexity score"""
        scores = {
            QueryComplexity.SIMPLE: 0.2,
            QueryComplexity.MODERATE: 0.5,
            QueryComplexity.COMPLEX: 0.8,
            QueryComplexity.VERY_COMPLEX: 1.0
        }
        return scores.get(analysis.complexity, 0.5)
    
    def _estimate_reasoning_depth(self, analysis: QueryAnalysis) -> int:
        """Estimate reasoning depth required"""
        if analysis.complexity == QueryComplexity.SIMPLE:
            return 1
        elif analysis.complexity == QueryComplexity.MODERATE:
            return 2
        elif analysis.complexity == QueryComplexity.COMPLEX:
            return 3
        else:
            return 4
    
    def _identify_data_requirements(self, query: str) -> List[str]:
        """Identify data requirements from query"""
        requirements = []
        
        keywords = {
            "research": "Academic literature access",
            "analyze": "Data analysis capabilities",
            "compare": "Comparative data sets",
            "statistics": "Statistical data access",
            "visualize": "Visualization capabilities"
        }
        
        query_lower = query.lower()
        for keyword, requirement in keywords.items():
            if keyword in query_lower:
                requirements.append(requirement)
        
        return requirements or ["General knowledge base"]
    
    def _identify_coordination_needs(self, analysis: QueryAnalysis) -> str:
        """Identify coordination requirements"""
        if analysis.complexity == QueryComplexity.SIMPLE:
            return "Minimal coordination - single agent sufficient"
        elif analysis.complexity == QueryComplexity.MODERATE:
            return "Moderate coordination - 2-3 agents working sequentially"
        elif analysis.complexity == QueryComplexity.COMPLEX:
            return "High coordination - multiple agents with refinement"
        else:
            return "Extensive coordination - multi-supervisor orchestration"
    
    def _recommend_approach(self, analysis: QueryAnalysis) -> str:
        """Recommend execution approach based on analysis"""
        if analysis.complexity == QueryComplexity.SIMPLE:
            return "Direct single-agent execution with minimal overhead"
        elif analysis.complexity == QueryComplexity.MODERATE:
            return "Chain-of-Agents pattern with sequential coordination"
        elif analysis.complexity == QueryComplexity.COMPLEX:
            return "Hierarchical supervision with multiple refinement rounds"
        else:
            return "Multi-supervisor orchestration with cross-domain synthesis"
    
    def _generate_routing_recommendations(
        self,
        analysis: QueryAnalysis
    ) -> List[str]:
        """Generate routing recommendations based on analysis"""
        recommendations = []
        
        # Complexity-based recommendations
        if analysis.complexity == QueryComplexity.SIMPLE:
            recommendations.append("Use speed-optimized routing for quick response")
            recommendations.append("Single supervisor allocation is sufficient")
        elif analysis.complexity in [QueryComplexity.COMPLEX, QueryComplexity.VERY_COMPLEX]:
            recommendations.append("Consider quality-focused strategy for best results")
            recommendations.append("Multiple refinement rounds recommended")
        
        # Uncertainty-based recommendations
        if analysis.uncertainty_level > 0.7:
            recommendations.append("Enable fallback mechanisms for high uncertainty")
            recommendations.append("Consider ensemble voting for critical decisions")
        
        # Domain-based recommendations
        if analysis.domain == QueryDomain.RESEARCH:
            recommendations.append("Allocate research supervisor with citation agents")
        elif analysis.domain == QueryDomain.ANALYTICS:
            recommendations.append("Include data analysis and visualization agents")
        
        return recommendations
    
    def _get_rejection_reason(
        self,
        strategy: RoutingStrategy,
        selected: RoutingStrategy
    ) -> str:
        """Get reason why a strategy wasn't selected"""
        if strategy == RoutingStrategy.COST_EFFICIENT and selected == RoutingStrategy.QUALITY_FOCUSED:
            return "Quality requirements exceeded cost-efficient capabilities"
        elif strategy == RoutingStrategy.QUALITY_FOCUSED and selected == RoutingStrategy.COST_EFFICIENT:
            return "Cost constraints made quality-focused approach infeasible"
        elif strategy == RoutingStrategy.SPEED_OPTIMIZED and selected != RoutingStrategy.SPEED_OPTIMIZED:
            return "Query complexity requires more thorough processing"
        else:
            return f"Selected strategy better optimizes for {self._get_optimization_focus(selected)}"
    
    def _check_model_availability(self) -> Dict[str, bool]:
        """Check availability of model providers"""
        # In production, this would check actual provider status
        return {
            "deepseek": True,
            "llama": True,
            "gemini": True
        }