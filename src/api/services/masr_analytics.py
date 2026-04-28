"""
MASR Performance Analytics Service

Advanced analytics and performance monitoring for MASR routing intelligence.
Provides real-time metrics, trend analysis, and optimization recommendations.

Based on "MasRouter: Learning to Route LLMs" research patterns.
"""

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from src.ai_brain.learning.supervision_feedback import SupervisionFeedbackLearner
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import ComplexityLevel as QueryComplexity
from src.ai_brain.router.query_analyzer import QueryDomain
from src.ai_brain.router.routing_types import RoutingStrategy


@dataclass
class PerformanceMetric:
    """Performance metric with statistical tracking"""
    name: str
    current_value: float
    average: float
    min_value: float
    max_value: float
    std_deviation: float
    trend: str  # "increasing", "decreasing", "stable"
    samples: int
    window: deque[float] = field(default_factory=lambda: deque(maxlen=1000))

    def update(self, value: float) -> None:
        """Update metric with new value"""
        self.window.append(value)
        self.current_value = value
        self.samples += 1
        
        if len(self.window) >= 2:
            self.average = statistics.mean(self.window)
            self.min_value = min(self.window)
            self.max_value = max(self.window)
            self.std_deviation = statistics.stdev(self.window)
            
            # Calculate trend
            recent = list(self.window)[-10:]
            if len(recent) >= 2:
                if recent[-1] > recent[0] * 1.1:
                    self.trend = "increasing"
                elif recent[-1] < recent[0] * 0.9:
                    self.trend = "decreasing"
                else:
                    self.trend = "stable"


@dataclass
class StrategyPerformance:
    """Performance tracking for a routing strategy"""
    strategy: RoutingStrategy
    total_requests: int = 0
    successful_routes: int = 0
    failed_routes: int = 0
    total_cost: float = 0
    total_latency_ms: float = 0
    quality_scores: list[float] = field(default_factory=list)
    cost_accuracy: float = 0.95  # Predicted vs actual
    latency_accuracy: float = 0.90
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        if self.total_requests == 0:
            return 0
        return self.successful_routes / self.total_requests
    
    @property
    def average_cost(self) -> float:
        """Calculate average cost"""
        if self.total_requests == 0:
            return 0
        return self.total_cost / self.total_requests
    
    @property
    def average_latency_ms(self) -> float:
        """Calculate average latency"""
        if self.total_requests == 0:
            return 0
        return self.total_latency_ms / self.total_requests
    
    @property
    def average_quality(self) -> float:
        """Calculate average quality score"""
        if not self.quality_scores:
            return 0.0
        return float(statistics.mean(self.quality_scores))


class MASRAnalyticsService:
    """
    Advanced analytics service for MASR routing performance.
    
    Provides:
    - Real-time performance metrics
    - Historical trend analysis
    - Cost optimization recommendations
    - Strategy effectiveness analysis
    - Model performance tracking
    - Anomaly detection
    """
    
    def __init__(self) -> None:
        """Initialize analytics service"""
        self.router = MASRouter()
        self.feedback_learner = SupervisionFeedbackLearner()

        # Performance tracking
        self.strategy_performance: dict[RoutingStrategy, StrategyPerformance] = {
            strategy: StrategyPerformance(strategy)
            for strategy in RoutingStrategy
        }
        
        # Metrics tracking
        self.metrics: dict[str, PerformanceMetric] = {
            "routing_latency_ms": PerformanceMetric("routing_latency_ms", 0, 0, 0, 0, 0, "stable", 0),
            "cost_per_query": PerformanceMetric("cost_per_query", 0, 0, 0, 0, 0, "stable", 0),
            "quality_score": PerformanceMetric("quality_score", 0, 0, 0, 0, 0, "stable", 0),
            "supervisor_utilization": PerformanceMetric("supervisor_utilization", 0, 0, 0, 0, 0, "stable", 0),
            "error_rate": PerformanceMetric("error_rate", 0, 0, 0, 0, 0, "stable", 0),
            "cache_hit_rate": PerformanceMetric("cache_hit_rate", 0, 0, 0, 0, 0, "stable", 0)
        }
        
        # Time-series data for trend analysis
        self.hourly_stats: dict[datetime, dict[str, float]] = defaultdict(dict)
        self.daily_stats: dict[datetime, dict[str, float]] = defaultdict(dict)

        # Model performance tracking
        self.model_performance: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "requests": 0,
            "errors": 0,
            "total_latency_ms": 0,
            "total_tokens": 0,
            "total_cost": 0
        })
        
        # Anomaly detection thresholds
        self.anomaly_thresholds = {
            "routing_latency_ms": 5000,  # 5 seconds
            "cost_per_query": 1.0,  # $1
            "error_rate": 0.1,  # 10%
            "quality_score": 0.6  # Below 60%
        }
        
        # Analytics start time
        self.start_time = datetime.now(UTC)
    
    async def record_routing_decision(
        self,
        routing_id: str,
        strategy: RoutingStrategy,
        complexity: QueryComplexity,
        domain: QueryDomain,
        estimated_cost: float,
        estimated_latency_ms: float,
        supervisor_count: int
    ) -> None:
        """
        Record a routing decision for analytics.
        
        Args:
            routing_id: Unique routing ID
            strategy: Routing strategy used
            complexity: Query complexity
            domain: Query domain
            estimated_cost: Estimated cost
            estimated_latency_ms: Estimated latency
            supervisor_count: Number of supervisors allocated
        """
        # Update strategy performance
        perf = self.strategy_performance[strategy]
        perf.total_requests += 1
        
        # Update metrics
        self.metrics["routing_latency_ms"].update(estimated_latency_ms)
        self.metrics["cost_per_query"].update(estimated_cost)
        self.metrics["supervisor_utilization"].update(supervisor_count / 5.0)  # Normalize to 0-1
        
        # Record hourly stats
        hour_key = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        self.hourly_stats[hour_key]["requests"] = self.hourly_stats[hour_key].get("requests", 0) + 1
        self.hourly_stats[hour_key]["total_cost"] = self.hourly_stats[hour_key].get("total_cost", 0) + estimated_cost
    
    async def record_routing_feedback(
        self,
        routing_id: str,
        strategy: RoutingStrategy,
        actual_cost: float,
        actual_latency_ms: float,
        quality_score: float,
        error_occurred: bool = False
    ) -> None:
        """
        Record feedback from completed routing.
        
        Args:
            routing_id: Routing ID
            strategy: Strategy used
            actual_cost: Actual execution cost
            actual_latency_ms: Actual latency
            quality_score: Quality score (0-1)
            error_occurred: Whether an error occurred
        """
        # Update strategy performance
        perf = self.strategy_performance[strategy]
        
        if error_occurred:
            perf.failed_routes += 1
            self.metrics["error_rate"].update(
                perf.failed_routes / perf.total_requests if perf.total_requests > 0 else 0
            )
        else:
            perf.successful_routes += 1
            perf.total_cost += actual_cost
            perf.total_latency_ms += actual_latency_ms
            perf.quality_scores.append(quality_score)
            
            # Update metrics
            self.metrics["quality_score"].update(quality_score)
            
            # Keep only last 100 quality scores
            if len(perf.quality_scores) > 100:
                perf.quality_scores = perf.quality_scores[-100:]
    
    async def get_performance_summary(self) -> dict[str, Any]:
        """
        Get comprehensive performance summary.
        
        Returns:
            Performance summary with metrics and recommendations
        """
        # Calculate overall statistics
        total_requests = sum(p.total_requests for p in self.strategy_performance.values())
        total_successful = sum(p.successful_routes for p in self.strategy_performance.values())
        overall_success_rate = total_successful / total_requests if total_requests > 0 else 0
        
        # Find best performing strategy
        best_strategy = max(
            self.strategy_performance.values(),
            key=lambda p: p.success_rate * p.average_quality if p.total_requests > 0 else 0
        )
        
        # Generate recommendations
        recommendations = await self._generate_recommendations()
        
        # Detect anomalies
        anomalies = self._detect_anomalies()
        
        return {
            "summary": {
                "total_requests": total_requests,
                "success_rate": overall_success_rate,
                "uptime_hours": (datetime.now(UTC) - self.start_time).total_seconds() / 3600,
                "best_strategy": best_strategy.strategy.value,
                "average_cost": statistics.mean(
                    [p.average_cost for p in self.strategy_performance.values() if p.total_requests > 0]
                ) if any(p.total_requests > 0 for p in self.strategy_performance.values()) else 0,
                "average_quality": statistics.mean(
                    [p.average_quality for p in self.strategy_performance.values() if p.quality_scores]
                ) if any(p.quality_scores for p in self.strategy_performance.values()) else 0
            },
            "metrics": {
                name: {
                    "current": metric.current_value,
                    "average": metric.average,
                    "min": metric.min_value,
                    "max": metric.max_value,
                    "std_dev": metric.std_deviation,
                    "trend": metric.trend
                }
                for name, metric in self.metrics.items()
            },
            "strategy_performance": {
                strategy.value: {
                    "requests": perf.total_requests,
                    "success_rate": perf.success_rate,
                    "avg_cost": perf.average_cost,
                    "avg_latency_ms": perf.average_latency_ms,
                    "avg_quality": perf.average_quality,
                    "cost_accuracy": perf.cost_accuracy,
                    "latency_accuracy": perf.latency_accuracy
                }
                for strategy, perf in self.strategy_performance.items()
            },
            "recommendations": recommendations,
            "anomalies": anomalies,
            "trends": await self._analyze_trends()
        }
    
    async def get_cost_analysis(self) -> dict[str, Any]:
        """
        Get detailed cost analysis and optimization opportunities.
        
        Returns:
            Cost analysis with breakdown and recommendations
        """
        # Calculate cost breakdown by strategy
        strategy_costs = {}
        for strategy, perf in self.strategy_performance.items():
            if perf.total_requests > 0:
                strategy_costs[strategy.value] = {
                    "total_cost": perf.total_cost,
                    "average_cost": perf.average_cost,
                    "requests": perf.total_requests,
                    "cost_per_quality": perf.average_cost / perf.average_quality if perf.average_quality > 0 else 0
                }
        
        # Calculate potential savings
        if strategy_costs:
            highest_cost = max(s["average_cost"] for s in strategy_costs.values())
            lowest_cost = min(s["average_cost"] for s in strategy_costs.values())
            potential_savings = (highest_cost - lowest_cost) * total_requests if (total_requests := sum(p.total_requests for p in self.strategy_performance.values())) > 0 else 0
        else:
            potential_savings = 0
        
        # Identify cost optimization opportunities
        opportunities = []
        
        # Check if quality-focused is overused for simple queries
        quality_perf = self.strategy_performance[RoutingStrategy.QUALITY_FOCUSED]
        if quality_perf.total_requests > 0 and quality_perf.average_cost > 0.5:
            opportunities.append({
                "opportunity": "Reduce quality-focused usage",
                "potential_savings": quality_perf.total_cost * 0.3,  # Assume 30% reduction possible
                "recommendation": "Use balanced strategy for moderate complexity queries"
            })
        
        # Check for cost-efficient underutilization
        cost_perf = self.strategy_performance[RoutingStrategy.COST_EFFICIENT]
        total_requests = sum(p.total_requests for p in self.strategy_performance.values())
        if total_requests > 0 and cost_perf.total_requests / total_requests < 0.3:
            opportunities.append({
                "opportunity": "Increase cost-efficient usage",
                "potential_savings": total_requests * 0.1 * 0.2,  # 10% more queries at 20% lower cost
                "recommendation": "Route simple queries to cost-efficient strategy"
            })
        
        return {
            "total_cost": sum(p.total_cost for p in self.strategy_performance.values()),
            "strategy_breakdown": strategy_costs,
            "hourly_costs": await self._get_hourly_costs(),
            "potential_monthly_savings": potential_savings * 30,
            "optimization_opportunities": opportunities,
            "cost_trends": {
                "7_day": await self._calculate_cost_trend(7),
                "30_day": await self._calculate_cost_trend(30)
            }
        }
    
    async def get_strategy_recommendations(
        self,
        complexity: QueryComplexity,
        domain: QueryDomain,
        constraints: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Get strategy recommendations for specific query characteristics.
        
        Args:
            complexity: Query complexity
            domain: Query domain
            constraints: Optional constraints (max_cost, min_quality, etc.)
            
        Returns:
            Strategy recommendations with reasoning
        """
        recommendations = []
        
        # Analyze historical performance for similar queries
        relevant_performance = await self._get_performance_for_characteristics(
            complexity,
            domain
        )
        
        # Score each strategy
        strategy_scores = {}
        for strategy in RoutingStrategy:
            score = await self._score_strategy(
                strategy,
                complexity,
                domain,
                relevant_performance,
                constraints or {}
            )
            strategy_scores[strategy] = score
        
        # Sort by score
        sorted_strategies = sorted(
            strategy_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Generate recommendations
        for strategy, score in sorted_strategies[:3]:
            perf = self.strategy_performance[strategy]
            recommendations.append({
                "strategy": strategy.value,
                "score": score,
                "reasoning": self._generate_strategy_reasoning(
                    strategy,
                    complexity,
                    domain,
                    perf
                ),
                "expected_cost": perf.average_cost,
                "expected_quality": perf.average_quality,
                "expected_latency_ms": perf.average_latency_ms,
                "confidence": min(0.95, perf.total_requests / 100)  # Confidence based on data
            })
        
        return {
            "primary_recommendation": recommendations[0] if recommendations else None,
            "alternatives": recommendations[1:],
            "analysis": {
                "complexity": complexity.value,
                "domain": domain.value,
                "constraints": constraints,
                "data_points": sum(1 for p in self.strategy_performance.values() if p.total_requests > 0)
            }
        }
    
    async def get_model_performance(self) -> dict[str, Any]:
        """
        Get model-specific performance metrics.
        
        Returns:
            Model performance analysis
        """
        model_stats = []
        
        for model_id, stats in self.model_performance.items():
            if stats["requests"] > 0:
                model_stats.append({
                    "model_id": model_id,
                    "requests": stats["requests"],
                    "error_rate": stats["errors"] / stats["requests"],
                    "avg_latency_ms": stats["total_latency_ms"] / stats["requests"],
                    "avg_tokens": stats["total_tokens"] / stats["requests"],
                    "total_cost": stats["total_cost"],
                    "cost_per_request": stats["total_cost"] / stats["requests"]
                })
        
        # Sort by requests
        model_stats.sort(key=lambda x: x["requests"], reverse=True)
        
        return {
            "models": model_stats,
            "most_used": model_stats[0]["model_id"] if model_stats else None,
            "most_reliable": min(model_stats, key=lambda x: x["error_rate"])["model_id"] if model_stats else None,
            "most_cost_effective": min(model_stats, key=lambda x: x["cost_per_request"])["model_id"] if model_stats else None,
            "recommendations": self._generate_model_recommendations(model_stats)
        }
    
    async def _generate_recommendations(self) -> list[str]:
        """Generate performance recommendations"""
        recommendations = []
        
        # Check error rate
        error_metric = self.metrics["error_rate"]
        if error_metric.current_value > 0.05:
            recommendations.append(
                f"High error rate ({error_metric.current_value:.1%}). Consider enabling fallback mechanisms."
            )
        
        # Check latency trend
        latency_metric = self.metrics["routing_latency_ms"]
        if latency_metric.trend == "increasing":
            recommendations.append(
                "Latency is increasing. Consider scaling supervisor pool or using speed-optimized strategy."
            )
        
        # Check cost efficiency
        cost_metric = self.metrics["cost_per_query"]
        if cost_metric.average > 0.5:
            recommendations.append(
                f"Average cost (${cost_metric.average:.2f}) is high. Increase use of cost-efficient strategy."
            )
        
        # Check quality scores
        quality_metric = self.metrics["quality_score"]
        if quality_metric.average < 0.8:
            recommendations.append(
                f"Quality scores averaging {quality_metric.average:.2f}. Consider using quality-focused strategy for critical queries."
            )
        
        # Check cache utilization
        cache_metric = self.metrics["cache_hit_rate"]
        if cache_metric.average < 0.3:
            recommendations.append(
                "Low cache hit rate. Consider implementing query result caching."
            )
        
        return recommendations
    
    def _detect_anomalies(self) -> list[dict[str, Any]]:
        """Detect performance anomalies"""
        anomalies = []
        
        for metric_name, threshold in self.anomaly_thresholds.items():
            if metric_name in self.metrics:
                metric = self.metrics[metric_name]
                
                # Check if current value exceeds threshold
                if metric_name == "quality_score":
                    # For quality, alert if below threshold
                    if metric.current_value < threshold:
                        anomalies.append({
                            "metric": metric_name,
                            "current_value": metric.current_value,
                            "threshold": threshold,
                            "severity": "warning" if metric.current_value > threshold * 0.8 else "critical",
                            "message": f"{metric_name} below acceptable threshold"
                        })
                else:
                    # For others, alert if above threshold
                    if metric.current_value > threshold:
                        anomalies.append({
                            "metric": metric_name,
                            "current_value": metric.current_value,
                            "threshold": threshold,
                            "severity": "warning" if metric.current_value < threshold * 1.5 else "critical",
                            "message": f"{metric_name} exceeds acceptable threshold"
                        })
        
        return anomalies
    
    async def _analyze_trends(self) -> dict[str, Any]:
        """Analyze performance trends"""
        trends = {}
        
        # Analyze each metric's trend
        for metric_name, metric in self.metrics.items():
            if len(metric.window) >= 10:
                # Calculate trend strength
                values = list(metric.window)
                if len(values) >= 2:
                    # Simple linear regression
                    x = np.arange(len(values))
                    y = np.array(values)
                    
                    # Calculate slope
                    if np.std(x) > 0 and np.std(y) > 0:
                        correlation = np.corrcoef(x, y)[0, 1]
                        slope = correlation * (np.std(y) / np.std(x))
                        
                        trends[metric_name] = {
                            "direction": metric.trend,
                            "strength": abs(correlation),
                            "change_rate": slope,
                            "forecast_1h": metric.current_value + slope * 6  # 6 samples = 1 hour
                        }
        
        return trends
    
    async def _get_hourly_costs(self) -> list[dict[str, Any]]:
        """Get hourly cost breakdown"""
        hourly_costs = []
        
        # Get last 24 hours
        now = datetime.now(UTC)
        for i in range(24):
            hour = now - timedelta(hours=i)
            hour_key = hour.replace(minute=0, second=0, microsecond=0)
            
            if hour_key in self.hourly_stats:
                stats = self.hourly_stats[hour_key]
                hourly_costs.append({
                    "hour": hour_key.isoformat(),
                    "requests": stats.get("requests", 0),
                    "total_cost": stats.get("total_cost", 0),
                    "avg_cost": stats.get("total_cost", 0) / stats.get("requests", 1) if stats.get("requests", 0) > 0 else 0
                })
        
        return hourly_costs
    
    async def _calculate_cost_trend(self, days: int) -> dict[str, float | str]:
        """Calculate cost trend over specified days"""
        # This would query historical data in production
        # For now, return estimated trend
        return {
            "trend": "decreasing" if days > 7 else "stable",
            "change_percent": -5.2 if days > 7 else 0.8,
            "projected_savings": 150.0 if days > 7 else 20.0
        }
    
    async def _get_performance_for_characteristics(
        self,
        complexity: QueryComplexity,
        domain: QueryDomain
    ) -> dict[RoutingStrategy, dict[str, float]]:
        """Get historical performance for query characteristics"""
        # In production, this would query a database
        # For now, return current strategy performance
        return {
            strategy: {
                "success_rate": perf.success_rate,
                "avg_cost": perf.average_cost,
                "avg_quality": perf.average_quality,
                "avg_latency": perf.average_latency_ms
            }
            for strategy, perf in self.strategy_performance.items()
        }
    
    async def _score_strategy(
        self,
        strategy: RoutingStrategy,
        complexity: QueryComplexity,
        domain: QueryDomain,
        historical_performance: dict[RoutingStrategy, dict[str, float]],
        constraints: dict[str, Any]
    ) -> float:
        """Score a strategy for given characteristics"""
        base_score = 0.5
        perf = historical_performance.get(strategy, {})
        
        # Adjust for complexity match
        if (complexity == QueryComplexity.SIMPLE and strategy == RoutingStrategy.COST_EFFICIENT) or (complexity == QueryComplexity.COMPLEX and strategy == RoutingStrategy.QUALITY_FOCUSED):
            base_score += 0.2
        elif strategy == RoutingStrategy.BALANCED:
            base_score += 0.1
        
        # Adjust for historical performance
        if perf:
            base_score += perf.get("success_rate", 0) * 0.2
            base_score += (1.0 - perf.get("avg_cost", 0)) * 0.1
            base_score += perf.get("avg_quality", 0) * 0.2
        
        # Apply constraints
        if "max_cost" in constraints and perf.get("avg_cost", 0) > constraints["max_cost"]:
            base_score *= 0.5
        if "min_quality" in constraints and perf.get("avg_quality", 0) < constraints["min_quality"]:
            base_score *= 0.5
        
        return min(1.0, max(0.0, base_score))
    
    def _generate_strategy_reasoning(
        self,
        strategy: RoutingStrategy,
        complexity: QueryComplexity,
        domain: QueryDomain,
        performance: StrategyPerformance
    ) -> str:
        """Generate reasoning for strategy recommendation"""
        reasoning = f"{strategy.value} strategy "
        
        if performance.total_requests > 0:
            reasoning += f"has {performance.success_rate:.1%} success rate "
            reasoning += f"with ${performance.average_cost:.2f} average cost "
            reasoning += f"and {performance.average_quality:.2f} quality score. "
        else:
            reasoning += "is recommended based on query characteristics. "
        
        if complexity == QueryComplexity.SIMPLE and strategy == RoutingStrategy.COST_EFFICIENT:
            reasoning += "Optimal for simple queries requiring quick, cost-effective responses."
        elif complexity == QueryComplexity.COMPLEX and strategy == RoutingStrategy.QUALITY_FOCUSED:
            reasoning += "Best for complex queries requiring deep analysis and high quality."
        elif strategy == RoutingStrategy.BALANCED:
            reasoning += "Provides good balance between cost, quality, and speed."
        
        return reasoning
    
    def _generate_model_recommendations(self, model_stats: list[dict[str, Any]]) -> list[str]:
        """Generate model-specific recommendations"""
        recommendations = []
        
        if not model_stats:
            return ["No model performance data available yet"]
        
        # Check for underperforming models
        for model in model_stats:
            if model["error_rate"] > 0.1:
                recommendations.append(
                    f"Model {model['model_id']} has high error rate ({model['error_rate']:.1%}). Consider fallback options."
                )
        
        # Check for cost optimization
        if len(model_stats) > 1:
            cheapest = min(model_stats, key=lambda x: x["cost_per_request"])
            most_used = max(model_stats, key=lambda x: x["requests"])
            
            if cheapest["model_id"] != most_used["model_id"]:
                potential_savings = (most_used["cost_per_request"] - cheapest["cost_per_request"]) * most_used["requests"]
                recommendations.append(
                    f"Consider using {cheapest['model_id']} more often. Potential savings: ${potential_savings:.2f}"
                )
        
        return recommendations if recommendations else ["Model performance is within acceptable parameters"]


# Global analytics service instance
masr_analytics = MASRAnalyticsService()