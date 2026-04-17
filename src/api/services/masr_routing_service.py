"""
MASR Routing Service Layer

Service layer for MASR routing intelligence, providing high-level
interfaces to the MASR router and supervisor bridge systems.
Based on "MasRouter: Learning to Route LLMs" research patterns.
"""

import uuid
from datetime import datetime
from typing import Any

from src.ai_brain.models.masr import ModelTier, RoutingStrategy
from src.ai_brain.supervisor_config_manager import SupervisorConfigurationManager
from src.config import get_settings

from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.learning.supervision_feedback import SupervisionFeedbackLearner
from src.ai_brain.router.hierarchical_cost_model import HierarchicalCostOptimizer
from src.ai_brain.router.masr import MASRouter, RoutingDecision
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityLevel,
    QueryDomain,
)
from src.models.masr_api_models import (
    AvailableStrategy,
    ComplexityAnalysisRequest,
    ComplexityAnalysisResponse,
    ComplexityFeatures,
    CostBreakdown,
    CostEstimationRequest,
    CostEstimationResponse,
    ModelInfo,
    ModelsListResponse,
    RouterStatus,
    RoutingDecisionResponse,
    RoutingFeedback,
    RoutingRequest,
    StrategiesListResponse,
    StrategyComparison,
    StrategyEvaluationRequest,
    StrategyEvaluationResponse,
    SupervisorAllocation,
)
from src.utils.type_coercion import coerce_float

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
    
    def __init__(self) -> None:
        """Initialize MASR routing service with all components"""
        from src.ai_brain.router.cost_optimizer import CostOptimizer
        # Core routing components
        self.router = MASRouter()
        self.bridge = MASRSupervisorBridge()
        base_cost_optimizer = CostOptimizer()
        self.cost_optimizer = HierarchicalCostOptimizer(base_cost_optimizer=base_cost_optimizer)
        self.config_manager = SupervisorConfigurationManager()
        self.feedback_learner = SupervisionFeedbackLearner()
        
        # Tracking and metrics
        self.routing_history: dict[str, RoutingDecision] = {}
        self.performance_metrics: dict[str, dict[str, float]] = {
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
            # Get routing decision
            decision = await self.router.route(
                request.query,
                context=request.context,
                strategy=request.strategy
            )

            # Generate unique routing ID
            routing_id = str(uuid.uuid4())

            # Store for feedback tracking
            self.routing_history[routing_id] = decision

            # Convert to supervisor allocations
            supervisor_allocations = []
            if hasattr(decision.agent_allocation, 'supervisors'):
                for supervisor in decision.agent_allocation.supervisors:
                    allocation = SupervisorAllocation(
                        supervisor_type=supervisor.get("type", "research"),
                        worker_count=supervisor.get("worker_count", 3),
                        refinement_rounds=supervisor.get("refinement_rounds", 1),
                        estimated_latency_ms=supervisor.get("estimated_latency_ms", 1000)
                    )
                    supervisor_allocations.append(allocation)

            # Get selected models info
            model_tier = decision.optimization_result.model_tier if hasattr(decision.optimization_result, 'model_tier') else ModelTier.STANDARD
            selected_models = self._get_model_info(model_tier)

            # Calculate confidence score
            confidence_score = decision.confidence_score

            # Extract complexity from complexity_analysis
            complexity = decision.complexity_analysis.level if hasattr(decision.complexity_analysis, 'level') else None
            if complexity is None:
                from src.ai_brain.router.query_analyzer import ComplexityLevel
                complexity = ComplexityLevel.MODERATE

            # Extract domain
            domains = decision.complexity_analysis.domains if hasattr(decision.complexity_analysis, 'domains') else []
            domain = domains[0] if domains else None

            # Build response
            response = RoutingDecisionResponse(
                routing_id=routing_id,
                domain=domain,
                complexity=complexity,
                strategy=decision.optimization_result.strategy if hasattr(decision.optimization_result, 'strategy') else RoutingStrategy.BALANCED,
                collaboration_mode=decision.collaboration_mode,
                supervisor_allocations=supervisor_allocations,
                selected_models=selected_models,
                estimated_cost=decision.estimated_cost,
                estimated_latency_ms=decision.estimated_latency_ms,
                confidence_score=confidence_score,
                reasoning=self._generate_reasoning(decision),
                alternatives=self._get_alternatives(decision)
            )

            # Update metrics
            strategy_val = decision.optimization_result.strategy if hasattr(decision.optimization_result, 'strategy') else RoutingStrategy.BALANCED
            self._update_metrics(strategy_val, success=True)

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
        # Get routing decision for cost calculation
        decision = await self.router.route(
            request.query,
            strategy=request.strategy
        )

        # Build breakdown if requested
        breakdown = None
        if request.include_breakdown:
            breakdown = CostBreakdown(
                model_costs=decision.estimated_cost * 0.7,
                coordination_overhead=decision.estimated_cost * 0.2,
                memory_operations=decision.estimated_cost * 0.1,
                total_cost=decision.estimated_cost,
                confidence_interval=(
                    decision.estimated_cost * 0.8,
                    decision.estimated_cost * 1.2
                ) if request.include_confidence else None
            )

        # Generate recommendations
        recommendations = self._generate_cost_recommendations(decision)

        # Extract complexity
        complexity_value = decision.complexity_analysis.level.value if hasattr(decision.complexity_analysis, 'level') else "moderate"
        model_tier = decision.optimization_result.model_tier if hasattr(decision.optimization_result, 'model_tier') else ModelTier.STANDARD
        worker_count = decision.agent_allocation.worker_count if hasattr(decision.agent_allocation, 'worker_count') else 3

        model_tier_map = {ModelTier.BASIC: 1.0, ModelTier.STANDARD: 2.0, ModelTier.ADVANCED: 3.0, ModelTier.PREMIUM: 4.0}
        response = CostEstimationResponse(
            estimated_cost=decision.estimated_cost,
            breakdown=breakdown,
            confidence_score=decision.confidence_score,
            cost_factors={
                "query_complexity": coerce_float(complexity_value, 0.5, min_val=0.0, max_val=1.0),
                "model_tier": float(model_tier_map.get(model_tier, 2.0)),
                "supervisor_count": 1.0,
                "total_workers": float(worker_count),
                "refinement_rounds": 1.0
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
        # Determine strategies to evaluate
        strategies = request.strategies or list(RoutingStrategy)

        # Evaluate each strategy
        comparisons = []
        best_strategy = None
        best_score = -1.0

        for strategy in strategies:
            # Get routing decision for this strategy
            decision = await self.router.route(
                request.query,
                strategy=strategy
            )

            # Calculate metrics
            cost = decision.estimated_cost
            quality = decision.estimated_quality
            latency_ms = decision.estimated_latency_ms

            # Apply custom weights if provided
            weights = request.weights or {"cost": 0.3, "quality": 0.5, "latency": 0.2}

            # Calculate recommendation score
            cost_score = 1.0 / (1.0 + cost)
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
        if best_strategy is None:
            best_strategy = RoutingStrategy.BALANCED

        response = StrategyEvaluationResponse(
            comparisons=comparisons,
            recommended_strategy=best_strategy,
            reasoning=self._generate_strategy_reasoning(
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
        # Get routing decision
        decision = await self.router.route(request.query)

        # Get analysis
        analysis = decision.complexity_analysis

        # Calculate complexity score (0-1)
        complexity_score = self._calculate_complexity_score(analysis)

        # Build features if requested
        features = None
        if request.include_features:
            domains = analysis.domains if hasattr(analysis, 'domains') else []
            uncertainty = analysis.uncertainty_level if hasattr(analysis, 'uncertainty_level') else 0.5
            features = ComplexityFeatures(
                query_length=len(request.query.split()),
                domain_count=len(domains),
                reasoning_depth=self._estimate_reasoning_depth(analysis),
                data_requirements=self._identify_data_requirements(request.query),
                coordination_needs=self._identify_coordination_needs(analysis),
                uncertainty_level=uncertainty
            )

        # Generate recommendations
        routing_recommendations = []
        if request.include_recommendations:
            routing_recommendations = self._generate_routing_recommendations(analysis)

        # Extract complexity level
        complexity_level = analysis.level if hasattr(analysis, 'level') else None
        if complexity_level is None:
            from src.ai_brain.router.query_analyzer import ComplexityLevel
            complexity_level = ComplexityLevel.MODERATE

        # Build response
        response = ComplexityAnalysisResponse(
            complexity=complexity_level,
            complexity_score=complexity_score,
            features=features,
            recommended_approach=self._recommend_approach(analysis),
            routing_recommendations=routing_recommendations
        )

        return response
    
    async def submit_feedback(
        self,
        feedback: RoutingFeedback
    ) -> dict[str, Any]:
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

        # Update performance metrics
        strategy = original_decision.optimization_result.strategy if hasattr(original_decision.optimization_result, 'strategy') else RoutingStrategy.BALANCED
        self._update_performance_from_feedback(
            strategy,
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
        tiers: dict[str, list[str]] = {}
        
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
            providers=list({m.provider for m in models})
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
        _total_routes = sum(
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
        active_supervisors = getattr(self.bridge, 'active_supervisor_count', 0)

        # Check model availability
        model_availability = self._check_model_availability()

        # Calculate total routes as int
        total_routes_int = int(sum(
            m["total_requests"]
            for m in self.performance_metrics.values()
        ))

        # Determine overall status
        if avg_success_rate < 0.8 or not all(model_availability.values()):
            status = "degraded"
        else:
            status = "healthy"

        # Get learning metrics (placeholder since method doesn't exist)
        learning_metrics: dict[str, int] = {
            "total_feedback": 0,
            "learning_cycles": 0
        }

        response = RouterStatus(
            status=status,
            uptime_seconds=int(uptime),
            total_routes=total_routes_int,
            average_latency_ms=avg_latency,
            success_rate=avg_success_rate,
            active_supervisors=active_supervisors,
            performance_metrics={
                strategy: {
                    "requests": int(metrics["total_requests"]),
                    "success_rate": float(metrics["success_rate"]),
                    "avg_cost": float(metrics["average_cost"]),
                    "avg_latency_ms": float(metrics["average_latency_ms"]),
                    "avg_quality": float(metrics["average_quality"])
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
    
    def _get_model_info(self, tier: ModelTier) -> list[ModelInfo]:
        """Get model information for a tier"""
        configs = self._get_model_configurations()
        return [
            ModelInfo(**config)
            for config in configs
            if config["tier"] == tier
        ]
    
    def _calculate_confidence(
        self,
        decision: RoutingDecision
    ) -> float:
        """Calculate routing confidence score"""
        return decision.confidence_score
    
    def _generate_reasoning(
        self,
        decision: RoutingDecision
    ) -> str:
        """Generate reasoning explanation for routing decision"""
        complexity = decision.complexity_analysis.level if hasattr(decision.complexity_analysis, 'level') else "moderate"
        complexity_value = complexity.value if hasattr(complexity, 'value') else str(complexity)
        domains = decision.complexity_analysis.domains if hasattr(decision.complexity_analysis, 'domains') else []
        domain_str = domains[0].value if domains and hasattr(domains[0], 'value') else "general"
        strategy = decision.optimization_result.strategy if hasattr(decision.optimization_result, 'strategy') else RoutingStrategy.BALANCED
        strategy_value = strategy.value if hasattr(strategy, 'value') else str(strategy)

        reasoning = f"Query analyzed as {complexity_value} complexity "
        reasoning += f"in {domain_str} domain. "
        reasoning += f"Selected {strategy_value} strategy "
        reasoning += f"optimizing for {self._get_optimization_focus(strategy)}. "
        worker_count = decision.agent_allocation.worker_count if hasattr(decision.agent_allocation, 'worker_count') else 1
        reasoning += f"Allocated {worker_count} worker(s) "
        reasoning += f"with {decision.collaboration_mode.value} collaboration mode."
        return reasoning
    
    def _get_alternatives(
        self,
        decision: RoutingDecision
    ) -> list[dict[str, Any]] | None:
        """Get alternative routing options considered"""
        alternatives = []

        current_strategy = decision.optimization_result.strategy if hasattr(decision.optimization_result, 'strategy') else RoutingStrategy.BALANCED

        for strategy in RoutingStrategy:
            if strategy != current_strategy:
                alternatives.append({
                    "strategy": strategy.value,
                    "estimated_cost": decision.estimated_cost * 0.9,
                    "estimated_latency": decision.estimated_latency_ms * 0.9,
                    "reason_not_selected": self._get_rejection_reason(
                        strategy,
                        current_strategy
                    )
                })

        return alternatives[:3]
    
    def _update_metrics(self, strategy: RoutingStrategy, success: bool) -> None:
        """Update performance metrics for a strategy"""
        metrics = self.performance_metrics[strategy.value]
        metrics["total_requests"] += 1

        if success:
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
    ) -> None:
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
    
    def _get_model_configurations(self) -> list[dict[str, Any]]:
        """Get model configurations"""
        configs: list[dict[str, Any]] = [
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
        return configs
    
    def _generate_cost_recommendations(
        self,
        decision: RoutingDecision
    ) -> list[str]:
        """Generate cost optimization recommendations"""
        recommendations = []

        if decision.estimated_cost > 0.5:
            recommendations.append(
                "Consider using cost_efficient strategy for similar queries"
            )

        complexity = decision.complexity_analysis.level if hasattr(decision.complexity_analysis, 'level') else None
        if complexity == ComplexityLevel.SIMPLE:
            recommendations.append(
                "Simple queries can use budget tier models effectively"
            )

        worker_count = decision.agent_allocation.worker_count if hasattr(decision.agent_allocation, 'worker_count') else 1
        if worker_count > 2:
            recommendations.append(
                "Multiple supervisors increase coordination overhead"
            )

        return recommendations
    
    def _estimate_quality(
        self,
        strategy: RoutingStrategy,
        analysis: ComplexityAnalysis
    ) -> float:
        """Estimate quality for a strategy"""
        base_quality = {
            RoutingStrategy.COST_EFFICIENT: 0.75,
            RoutingStrategy.QUALITY_FOCUSED: 0.95,
            RoutingStrategy.BALANCED: 0.85,
            RoutingStrategy.SPEED_OPTIMIZED: 0.80
        }.get(strategy, 0.85)

        # Adjust for complexity
        if hasattr(analysis, 'level') and analysis.level == ComplexityLevel.COMPLEX:
            base_quality *= 0.9

        return min(base_quality, 1.0)
    
    def _get_strategy_pros(self, strategy: RoutingStrategy) -> list[str]:
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
    
    def _get_strategy_cons(self, strategy: RoutingStrategy) -> list[str]:
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
    
    def _get_strategy_use_cases(self, strategy: RoutingStrategy) -> list[str]:
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
    
    def _get_trade_offs(self, strategy: RoutingStrategy) -> dict[str, str]:
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
        strategy: RoutingStrategy,
        comparisons: list[StrategyComparison]
    ) -> str:
        """Generate reasoning for strategy recommendation"""
        reasoning = f"{strategy.value} strategy is recommended because it "
        reasoning += f"optimizes for {self._get_optimization_focus(strategy)}. "

        # Add comparison insight
        best_comp = max(comparisons, key=lambda c: c.recommendation_score)
        reasoning += f"It scores {best_comp.recommendation_score:.2f} based on "
        reasoning += "weighted evaluation of cost, quality, and latency factors."

        return reasoning
    
    def _calculate_complexity_score(self, analysis: Any) -> float:
        """Calculate normalized complexity score"""
        if hasattr(analysis, 'score'):
            return float(analysis.score)
        return 0.5
    
    def _estimate_reasoning_depth(self, analysis: Any) -> int:
        """Estimate reasoning depth required"""
        if hasattr(analysis, 'level'):
            level = analysis.level
            if level == ComplexityLevel.SIMPLE:
                return 1
            elif level == ComplexityLevel.MODERATE:
                return 2
            elif level == ComplexityLevel.COMPLEX:
                return 3
        return 2
    
    def _identify_data_requirements(self, query: str) -> list[str]:
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
    
    def _identify_coordination_needs(self, analysis: Any) -> str:
        """Identify coordination requirements"""
        if hasattr(analysis, 'level'):
            level = analysis.level
            if level == ComplexityLevel.SIMPLE:
                return "Minimal coordination - single agent sufficient"
            elif level == ComplexityLevel.MODERATE:
                return "Moderate coordination - 2-3 agents working sequentially"
            elif level == ComplexityLevel.COMPLEX:
                return "High coordination - multiple agents with refinement"
            else:
                return "Extensive coordination - multi-supervisor orchestration"
        return "Moderate coordination - 2-3 agents working sequentially"
    
    def _recommend_approach(self, analysis: Any) -> str:
        """Recommend execution approach based on analysis"""
        if hasattr(analysis, 'level'):
            level = analysis.level
            if level == ComplexityLevel.SIMPLE:
                return "Direct single-agent execution with minimal overhead"
            elif level == ComplexityLevel.MODERATE:
                return "Chain-of-Agents pattern with sequential coordination"
            elif level == ComplexityLevel.COMPLEX:
                return "Hierarchical supervision with multiple refinement rounds"
            else:
                return "Multi-supervisor orchestration with cross-domain synthesis"
        return "Chain-of-Agents pattern with sequential coordination"
    
    def _generate_routing_recommendations(
        self,
        analysis: Any
    ) -> list[str]:
        """Generate routing recommendations based on analysis"""
        recommendations = []


        # Complexity-based recommendations
        if hasattr(analysis, 'level'):
            level = analysis.level
            if level == ComplexityLevel.SIMPLE:
                recommendations.append("Use speed-optimized routing for quick response")
                recommendations.append("Single supervisor allocation is sufficient")
            elif level == ComplexityLevel.COMPLEX:
                recommendations.append("Consider quality-focused strategy for best results")
                recommendations.append("Multiple refinement rounds recommended")

        # Uncertainty-based recommendations
        if hasattr(analysis, 'uncertainty') and analysis.uncertainty > 0.7:
            recommendations.append("Enable fallback mechanisms for high uncertainty")
            recommendations.append("Consider ensemble voting for critical decisions")

        # Domain-based recommendations
        if hasattr(analysis, 'domains') and analysis.domains:
            first_domain = analysis.domains[0]
            if first_domain == QueryDomain.RESEARCH:
                recommendations.append("Allocate research supervisor with citation agents")
            elif first_domain == QueryDomain.ANALYTICS:
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
    
    def _check_model_availability(self) -> dict[str, bool]:
        """Check availability of model providers"""
        # In production, this would check actual provider status
        return {
            "deepseek": True,
            "llama": True,
            "gemini": True
        }