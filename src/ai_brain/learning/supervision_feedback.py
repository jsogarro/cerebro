"""
Supervision Feedback Learning System

Advanced learning system that collects feedback from supervisor executions
to continuously improve MASR routing decisions and cost predictions.

Key Features:
- Performance metric collection and analysis
- Routing strategy optimization based on historical data
- Cost prediction model training and refinement
- Adaptive threshold adjustment
- A/B testing integration for routing strategies
"""

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict, deque
import statistics
import pickle

from ..router.masr import RoutingDecision, RoutingStrategy, CollaborationMode
from ..router.query_analyzer import ComplexityLevel
from ..integration.masr_supervisor_bridge import SupervisorExecutionResult
from ...agents.supervisors.base_supervisor import SupervisionMode

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """Types of feedback data collected."""
    
    EXECUTION_PERFORMANCE = "execution_performance"
    COST_ACCURACY = "cost_accuracy"
    QUALITY_ASSESSMENT = "quality_assessment"
    USER_SATISFACTION = "user_satisfaction"
    ROUTING_EFFECTIVENESS = "routing_effectiveness"


@dataclass
class FeedbackEvent:
    """Single feedback event from supervisor execution."""
    
    event_id: str
    timestamp: datetime
    feedback_type: FeedbackType
    
    # Routing context
    query_id: str
    routing_decision: Dict[str, Any]
    supervisor_type: str
    complexity_level: str
    domains: List[str]
    
    # Execution metrics
    predicted_cost: float
    actual_cost: float
    predicted_quality: float
    actual_quality: float
    predicted_latency_ms: int
    actual_latency_ms: int
    
    # Performance indicators
    execution_success: bool
    worker_count_used: int
    refinement_rounds_used: int
    consensus_achieved: bool
    
    # User and context information
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    # Calculated metrics
    cost_accuracy: float = 0.0
    quality_accuracy: float = 0.0
    latency_accuracy: float = 0.0
    overall_satisfaction: float = 0.0


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics for analysis."""
    
    total_executions: int = 0
    successful_executions: int = 0
    success_rate: float = 0.0
    
    # Cost metrics
    average_cost_accuracy: float = 0.0
    cost_prediction_variance: float = 0.0
    cost_improvement_trend: float = 0.0
    
    # Quality metrics
    average_quality_score: float = 0.0
    quality_consistency: float = 0.0
    quality_improvement_trend: float = 0.0
    
    # Latency metrics
    average_latency_ms: float = 0.0
    latency_prediction_accuracy: float = 0.0
    
    # Routing effectiveness
    routing_accuracy: float = 0.0
    optimal_supervisor_selection_rate: float = 0.0
    
    # Temporal analysis
    last_updated: datetime = field(default_factory=datetime.now)
    measurement_window_hours: int = 24


class RoutingStrategyAnalyzer:
    """Analyzes routing strategy effectiveness."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize strategy analyzer."""
        self.config = config or {}
        
        # Strategy performance tracking
        self.strategy_metrics: Dict[str, PerformanceMetrics] = defaultdict(PerformanceMetrics)
        self.strategy_events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # Analysis configuration
        self.analysis_window_hours = self.config.get("analysis_window_hours", 24)
        self.min_samples_for_analysis = self.config.get("min_samples", 10)
        
    def record_strategy_feedback(self, strategy: RoutingStrategy, feedback: FeedbackEvent):
        """Record feedback for a specific routing strategy."""
        
        strategy_name = strategy.value if hasattr(strategy, 'value') else str(strategy)
        
        # Add to event history
        self.strategy_events[strategy_name].append(feedback)
        
        # Update aggregated metrics
        metrics = self.strategy_metrics[strategy_name]
        metrics.total_executions += 1
        
        if feedback.execution_success:
            metrics.successful_executions += 1
        
        metrics.success_rate = metrics.successful_executions / metrics.total_executions
        
        # Update cost accuracy (exponential moving average)
        alpha = 0.1
        metrics.average_cost_accuracy = (
            metrics.average_cost_accuracy * (1 - alpha) + 
            feedback.cost_accuracy * alpha
        )
        
        # Update quality metrics
        metrics.average_quality_score = (
            metrics.average_quality_score * (1 - alpha) + 
            feedback.actual_quality * alpha
        )
        
        metrics.last_updated = datetime.now()
        
        logger.debug(f"Updated metrics for strategy {strategy_name}: success_rate={metrics.success_rate:.3f}")
    
    def analyze_strategy_performance(
        self, 
        time_window_hours: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze performance across all routing strategies."""
        
        window_hours = time_window_hours or self.analysis_window_hours
        cutoff_time = datetime.now() - timedelta(hours=window_hours)
        
        analysis_results = {}
        
        for strategy_name, events in self.strategy_events.items():
            # Filter events within time window
            recent_events = [
                event for event in events 
                if event.timestamp >= cutoff_time
            ]
            
            if len(recent_events) < self.min_samples_for_analysis:
                continue
            
            # Calculate detailed metrics
            cost_accuracies = [e.cost_accuracy for e in recent_events]
            quality_scores = [e.actual_quality for e in recent_events]
            latency_ratios = [e.actual_latency_ms / max(e.predicted_latency_ms, 1) for e in recent_events]
            
            analysis_results[strategy_name] = {
                "sample_size": len(recent_events),
                "success_rate": sum(e.execution_success for e in recent_events) / len(recent_events),
                "cost_accuracy": {
                    "mean": statistics.mean(cost_accuracies),
                    "std": statistics.stdev(cost_accuracies) if len(cost_accuracies) > 1 else 0.0,
                    "trend": self._calculate_trend([e.cost_accuracy for e in recent_events[-10:]]),
                },
                "quality_performance": {
                    "mean": statistics.mean(quality_scores),
                    "std": statistics.stdev(quality_scores) if len(quality_scores) > 1 else 0.0,
                    "consistency": 1.0 - (statistics.stdev(quality_scores) if len(quality_scores) > 1 else 0.0),
                },
                "latency_accuracy": {
                    "mean_ratio": statistics.mean(latency_ratios),
                    "prediction_accuracy": sum(0.8 <= ratio <= 1.2 for ratio in latency_ratios) / len(latency_ratios),
                },
                "overall_score": self._calculate_overall_strategy_score(recent_events),
            }
        
        return analysis_results
    
    def _calculate_trend(self, values: List[float]) -> float:
        """Calculate trend direction for a series of values."""
        if len(values) < 3:
            return 0.0
        
        # Simple linear trend calculation
        x = list(range(len(values)))
        n = len(values)
        
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        slope = numerator / denominator
        return slope
    
    def _calculate_overall_strategy_score(self, events: List[FeedbackEvent]) -> float:
        """Calculate overall performance score for a strategy."""
        if not events:
            return 0.0
        
        # Weighted scoring
        success_weight = 0.3
        cost_accuracy_weight = 0.25
        quality_weight = 0.25
        latency_weight = 0.2
        
        success_score = sum(e.execution_success for e in events) / len(events)
        cost_score = statistics.mean([e.cost_accuracy for e in events])
        quality_score = statistics.mean([e.actual_quality for e in events])
        latency_score = statistics.mean([
            min(e.predicted_latency_ms / max(e.actual_latency_ms, 1), 1.0) 
            for e in events
        ])
        
        overall_score = (
            success_score * success_weight +
            cost_score * cost_accuracy_weight + 
            quality_score * quality_weight +
            latency_score * latency_weight
        )
        
        return overall_score
    
    def get_optimal_strategy_recommendation(
        self, 
        context: Dict[str, Any]
    ) -> Tuple[str, float]:
        """Get optimal strategy recommendation based on context."""
        
        analysis = self.analyze_strategy_performance()
        
        if not analysis:
            return "balanced", 0.5
        
        # Context-aware strategy selection
        complexity = context.get("complexity", "moderate")
        priority = context.get("priority", "normal")
        
        # Score strategies based on context
        strategy_scores = {}
        
        for strategy_name, metrics in analysis.items():
            base_score = metrics["overall_score"]
            
            # Adjust score based on context
            if complexity == "simple" and strategy_name == "cost_efficient":
                base_score *= 1.1
            elif complexity == "complex" and strategy_name == "quality_focused":
                base_score *= 1.2
            elif priority == "critical" and strategy_name == "speed_first":
                base_score *= 1.15
            
            strategy_scores[strategy_name] = base_score
        
        # Select best strategy
        if strategy_scores:
            best_strategy = max(strategy_scores, key=strategy_scores.get)
            confidence = strategy_scores[best_strategy]
            return best_strategy, confidence
        
        return "balanced", 0.5


class CostPredictionOptimizer:
    """Optimizes cost prediction models based on feedback."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize cost prediction optimizer."""
        self.config = config or {}
        
        # Cost prediction tracking
        self.cost_predictions: List[Tuple[float, float]] = []  # (predicted, actual)
        self.cost_factors: Dict[str, float] = {
            "complexity_multiplier": 1.0,
            "domain_adjustment": 1.0, 
            "supervisor_overhead": 1.0,
            "refinement_cost": 1.0,
        }
        
        # Learning parameters
        self.learning_rate = self.config.get("learning_rate", 0.01)
        self.min_samples_for_update = self.config.get("min_samples", 20)
        
    def record_cost_feedback(self, predicted_cost: float, actual_cost: float):
        """Record cost prediction vs actual cost."""
        self.cost_predictions.append((predicted_cost, actual_cost))
        
        # Keep only recent predictions
        max_history = self.config.get("max_history", 1000)
        if len(self.cost_predictions) > max_history:
            self.cost_predictions = self.cost_predictions[-max_history:]
    
    def optimize_cost_factors(self) -> Dict[str, float]:
        """Optimize cost factors based on prediction accuracy."""
        if len(self.cost_predictions) < self.min_samples_for_update:
            return self.cost_factors
        
        # Calculate prediction errors
        errors = [
            (actual - predicted) / predicted 
            for predicted, actual in self.cost_predictions[-100:]
            if predicted > 0
        ]
        
        if not errors:
            return self.cost_factors
        
        # Calculate bias and adjust factors
        average_error = statistics.mean(errors)
        
        # Adjust cost factors to reduce bias
        if average_error > 0.1:  # Underpredicting costs
            self.cost_factors["complexity_multiplier"] *= (1 + self.learning_rate)
            self.cost_factors["supervisor_overhead"] *= (1 + self.learning_rate)
        elif average_error < -0.1:  # Overpredicting costs
            self.cost_factors["complexity_multiplier"] *= (1 - self.learning_rate)
            self.cost_factors["supervisor_overhead"] *= (1 - self.learning_rate)
        
        # Ensure factors stay within reasonable bounds
        for factor_name in self.cost_factors:
            self.cost_factors[factor_name] = max(0.5, min(2.0, self.cost_factors[factor_name]))
        
        logger.info(f"Updated cost factors: {self.cost_factors}")
        
        return self.cost_factors
    
    def get_cost_accuracy_metrics(self) -> Dict[str, float]:
        """Get cost prediction accuracy metrics."""
        if not self.cost_predictions:
            return {"accuracy": 0.0, "bias": 0.0, "variance": 0.0}
        
        # Calculate metrics
        accuracies = [
            1.0 - abs(actual - predicted) / predicted
            for predicted, actual in self.cost_predictions
            if predicted > 0
        ]
        
        errors = [
            (actual - predicted) / predicted
            for predicted, actual in self.cost_predictions
            if predicted > 0
        ]
        
        return {
            "accuracy": statistics.mean(accuracies) if accuracies else 0.0,
            "bias": statistics.mean(errors) if errors else 0.0,
            "variance": statistics.variance(errors) if len(errors) > 1 else 0.0,
            "sample_size": len(self.cost_predictions),
        }


class SupervisionFeedbackLearner:
    """
    Main feedback learning system that coordinates all learning components.
    
    Integrates routing strategy analysis, cost prediction optimization,
    and supervisor performance tracking to continuously improve MASR decisions.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize supervision feedback learner."""
        self.config = config or {}
        
        # Initialize learning components
        self.strategy_analyzer = RoutingStrategyAnalyzer(
            self.config.get("strategy_analyzer", {})
        )
        self.cost_optimizer = CostPredictionOptimizer(
            self.config.get("cost_optimizer", {})
        )
        
        # Feedback storage
        self.feedback_events: deque = deque(maxlen=self.config.get("max_events", 10000))
        self.supervisor_performance: Dict[str, PerformanceMetrics] = defaultdict(PerformanceMetrics)
        
        # Learning configuration
        self.enable_adaptive_learning = self.config.get("enable_adaptive_learning", True)
        self.learning_interval_hours = self.config.get("learning_interval_hours", 6)
        self.auto_optimization_enabled = self.config.get("auto_optimization", True)
        
        # Performance tracking
        self.learning_stats = {
            "total_feedback_events": 0,
            "learning_cycles_completed": 0,
            "last_learning_cycle": None,
            "routing_improvements": 0,
            "cost_improvements": 0,
        }
        
        # Scheduled learning task
        self._learning_task: Optional[asyncio.Task] = None
    
    def record_execution_feedback(
        self,
        routing_decision: RoutingDecision,
        execution_result: SupervisorExecutionResult,
        user_feedback: Optional[Dict[str, Any]] = None
    ):
        """
        Record comprehensive feedback from a supervisor execution.
        
        Args:
            routing_decision: Original MASR routing decision
            execution_result: Supervisor execution result
            user_feedback: Optional user satisfaction feedback
        """
        
        # Calculate accuracy metrics
        cost_accuracy = 1.0 - abs(
            execution_result.actual_cost - routing_decision.estimated_cost
        ) / max(routing_decision.estimated_cost, 0.001)
        
        quality_accuracy = 1.0 - abs(
            execution_result.quality_score - routing_decision.estimated_quality
        ) / max(routing_decision.estimated_quality, 0.001)
        
        latency_accuracy = 1.0 - abs(
            execution_result.execution_time_seconds * 1000 - routing_decision.estimated_latency_ms
        ) / max(routing_decision.estimated_latency_ms, 1)
        
        # Create feedback event
        feedback_event = FeedbackEvent(
            event_id=f"feedback_{len(self.feedback_events)}_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            feedback_type=FeedbackType.EXECUTION_PERFORMANCE,
            query_id=routing_decision.query_id,
            routing_decision=routing_decision.__dict__,
            supervisor_type=execution_result.supervisor_type,
            complexity_level=routing_decision.complexity_analysis.level.value,
            domains=[d.value if hasattr(d, 'value') else str(d) 
                    for d in routing_decision.complexity_analysis.domains],
            predicted_cost=routing_decision.estimated_cost,
            actual_cost=execution_result.actual_cost,
            predicted_quality=routing_decision.estimated_quality,
            actual_quality=execution_result.quality_score,
            predicted_latency_ms=routing_decision.estimated_latency_ms,
            actual_latency_ms=int(execution_result.execution_time_seconds * 1000),
            execution_success=execution_result.status.value == "completed",
            worker_count_used=execution_result.workers_used,
            refinement_rounds_used=execution_result.refinement_rounds,
            consensus_achieved=execution_result.consensus_score >= 0.9,
            cost_accuracy=max(0.0, min(1.0, cost_accuracy)),
            quality_accuracy=max(0.0, min(1.0, quality_accuracy)),
            latency_accuracy=max(0.0, min(1.0, latency_accuracy)),
            overall_satisfaction=user_feedback.get("satisfaction", 0.8) if user_feedback else 0.8,
            context=user_feedback or {},
        )
        
        # Store feedback event
        self.feedback_events.append(feedback_event)
        self.learning_stats["total_feedback_events"] += 1
        
        # Update component learners
        routing_strategy = RoutingStrategy(routing_decision.__dict__.get("routing_strategy", "balanced"))
        self.strategy_analyzer.record_strategy_feedback(routing_strategy, feedback_event)
        self.cost_optimizer.record_cost_feedback(
            routing_decision.estimated_cost,
            execution_result.actual_cost
        )
        
        # Update supervisor performance
        supervisor_metrics = self.supervisor_performance[execution_result.supervisor_type]
        supervisor_metrics.total_executions += 1
        if execution_result.status.value == "completed":
            supervisor_metrics.successful_executions += 1
        supervisor_metrics.success_rate = (
            supervisor_metrics.successful_executions / supervisor_metrics.total_executions
        )
        
        logger.info(f"Recorded feedback for {execution_result.supervisor_type} supervisor: "
                   f"cost_accuracy={cost_accuracy:.3f}, quality_accuracy={quality_accuracy:.3f}")
    
    async def perform_learning_cycle(self) -> Dict[str, Any]:
        """Perform a complete learning cycle to update routing strategies."""
        
        learning_start = datetime.now()
        improvements = {"routing": 0, "cost": 0, "supervisor": 0}
        
        try:
            logger.info("Starting supervision feedback learning cycle")
            
            # 1. Analyze routing strategy performance
            strategy_analysis = self.strategy_analyzer.analyze_strategy_performance()
            if strategy_analysis:
                logger.info(f"Analyzed {len(strategy_analysis)} routing strategies")
                improvements["routing"] = len(strategy_analysis)
            
            # 2. Optimize cost prediction factors
            cost_factors = self.cost_optimizer.optimize_cost_factors()
            cost_metrics = self.cost_optimizer.get_cost_accuracy_metrics()
            if cost_metrics["sample_size"] > 0:
                logger.info(f"Cost prediction accuracy: {cost_metrics['accuracy']:.3f}")
                improvements["cost"] = 1
            
            # 3. Generate supervisor performance report
            supervisor_report = self._generate_supervisor_performance_report()
            improvements["supervisor"] = len(supervisor_report)
            
            # Update learning stats
            self.learning_stats["learning_cycles_completed"] += 1
            self.learning_stats["last_learning_cycle"] = datetime.now().isoformat()
            self.learning_stats["routing_improvements"] += improvements["routing"]
            self.learning_stats["cost_improvements"] += improvements["cost"]
            
            learning_duration = (datetime.now() - learning_start).total_seconds()
            
            logger.info(f"Learning cycle completed in {learning_duration:.2f}s: "
                       f"routing={improvements['routing']}, cost={improvements['cost']}, "
                       f"supervisor={improvements['supervisor']}")
            
            return {
                "status": "completed",
                "duration_seconds": learning_duration,
                "improvements": improvements,
                "strategy_analysis": strategy_analysis,
                "cost_metrics": cost_metrics,
                "supervisor_report": supervisor_report,
                "learning_stats": self.learning_stats,
            }
            
        except Exception as e:
            logger.error(f"Learning cycle failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "improvements": improvements,
            }
    
    def _generate_supervisor_performance_report(self) -> Dict[str, Dict[str, Any]]:
        """Generate comprehensive supervisor performance report."""
        
        report = {}
        
        for supervisor_type, metrics in self.supervisor_performance.items():
            if metrics.total_executions == 0:
                continue
            
            # Calculate recent performance (last 24 hours)
            recent_events = [
                event for event in self.feedback_events
                if (event.supervisor_type == supervisor_type and 
                    event.timestamp >= datetime.now() - timedelta(hours=24))
            ]
            
            recent_quality = [e.actual_quality for e in recent_events]
            recent_cost_accuracy = [e.cost_accuracy for e in recent_events]
            
            report[supervisor_type] = {
                "total_executions": metrics.total_executions,
                "success_rate": metrics.success_rate,
                "recent_performance": {
                    "executions_24h": len(recent_events),
                    "average_quality": statistics.mean(recent_quality) if recent_quality else 0.0,
                    "average_cost_accuracy": statistics.mean(recent_cost_accuracy) if recent_cost_accuracy else 0.0,
                },
                "trends": {
                    "quality_trend": self.strategy_analyzer._calculate_trend(recent_quality[-10:]),
                    "cost_accuracy_trend": self.strategy_analyzer._calculate_trend(recent_cost_accuracy[-10:]),
                }
            }
        
        return report
    
    def get_routing_recommendations(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Get routing recommendations based on learned patterns."""
        
        # Get optimal strategy
        optimal_strategy, confidence = self.strategy_analyzer.get_optimal_strategy_recommendation(context)
        
        # Get cost optimization suggestions
        cost_factors = self.cost_optimizer.cost_factors
        
        # Generate supervisor recommendations
        supervisor_rankings = self._rank_supervisors_for_context(context)
        
        return {
            "optimal_routing_strategy": {
                "strategy": optimal_strategy,
                "confidence": confidence,
                "reason": self._explain_strategy_selection(optimal_strategy, context),
            },
            "cost_optimization": {
                "factors": cost_factors,
                "accuracy_metrics": self.cost_optimizer.get_cost_accuracy_metrics(),
            },
            "supervisor_recommendations": supervisor_rankings,
            "learning_confidence": self._calculate_learning_confidence(),
        }
    
    def _rank_supervisors_for_context(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Rank supervisors based on context and performance history."""
        
        domain = context.get("domain", "research")
        complexity = context.get("complexity", "moderate")
        
        rankings = []
        
        for supervisor_type, metrics in self.supervisor_performance.items():
            if metrics.total_executions < 3:  # Need minimum data
                continue
            
            # Calculate context-specific score
            base_score = metrics.success_rate
            
            # Adjust for domain match (simplified)
            if supervisor_type == domain:
                base_score *= 1.2
            
            # Adjust for complexity handling
            supervisor_events = [
                e for e in self.feedback_events 
                if e.supervisor_type == supervisor_type
            ]
            
            complexity_performance = statistics.mean([
                e.actual_quality for e in supervisor_events 
                if e.complexity_level == complexity
            ]) if supervisor_events else 0.5
            
            final_score = base_score * 0.7 + complexity_performance * 0.3
            
            rankings.append({
                "supervisor_type": supervisor_type,
                "score": final_score,
                "executions": metrics.total_executions,
                "success_rate": metrics.success_rate,
                "complexity_performance": complexity_performance,
            })
        
        return sorted(rankings, key=lambda x: x["score"], reverse=True)
    
    def _explain_strategy_selection(self, strategy: str, context: Dict[str, Any]) -> str:
        """Explain why a particular strategy was selected."""
        
        explanations = {
            "speed_first": "Optimized for low latency based on priority requirements",
            "cost_efficient": "Selected for cost optimization with acceptable quality trade-offs",
            "quality_focused": "Chosen for maximum quality with complex query requirements",
            "balanced": "Balanced approach for optimal cost-quality trade-off",
            "adaptive": "Dynamic strategy based on learned performance patterns",
        }
        
        base_explanation = explanations.get(strategy, "Selected based on performance history")
        
        # Add context-specific reasoning
        complexity = context.get("complexity", "moderate")
        if complexity == "complex" and strategy == "quality_focused":
            base_explanation += " (complex queries require higher quality thresholds)"
        elif complexity == "simple" and strategy == "cost_efficient":
            base_explanation += " (simple queries can use cost-optimized approaches)"
        
        return base_explanation
    
    def _calculate_learning_confidence(self) -> float:
        """Calculate confidence in learning recommendations."""
        
        # Base confidence on amount of feedback data
        total_events = len(self.feedback_events)
        data_confidence = min(total_events / 1000, 1.0)  # Max confidence at 1000 events
        
        # Adjust for recency of data
        recent_events = sum(
            1 for event in self.feedback_events
            if event.timestamp >= datetime.now() - timedelta(hours=48)
        )
        recency_confidence = min(recent_events / 100, 1.0)  # Max confidence at 100 recent events
        
        # Calculate overall confidence
        overall_confidence = (data_confidence * 0.6 + recency_confidence * 0.4)
        
        return overall_confidence
    
    async def get_learning_stats(self) -> Dict[str, Any]:
        """Get comprehensive learning system statistics."""
        
        return {
            "learning_stats": self.learning_stats,
            "feedback_events": len(self.feedback_events),
            "strategy_analysis": len(self.strategy_analyzer.strategy_events),
            "cost_optimization": self.cost_optimizer.get_cost_accuracy_metrics(),
            "supervisor_performance": {
                supervisor_type: {
                    "executions": metrics.total_executions,
                    "success_rate": metrics.success_rate,
                }
                for supervisor_type, metrics in self.supervisor_performance.items()
            },
            "learning_confidence": self._calculate_learning_confidence(),
        }
    
    async def save_learning_state(self, file_path: str) -> None:
        """Save learning state to file."""

        state = {
            "feedback_events": [asdict(event) for event in self.feedback_events],
            "strategy_metrics": {k: asdict(v) for k, v in self.strategy_analyzer.strategy_metrics.items()},
            "cost_factors": self.cost_optimizer.cost_factors,
            "cost_predictions": self.cost_optimizer.cost_predictions,
            "supervisor_performance": {k: asdict(v) for k, v in self.supervisor_performance.items()},
            "learning_stats": self.learning_stats,
            "timestamp": datetime.now().isoformat(),
        }
        
        with open(file_path, 'wb') as f:
            pickle.dump(state, f)
        
        logger.info(f"Saved learning state to {file_path}")
    
    async def load_learning_state(self, file_path: str) -> None:
        """Load learning state from file."""

        try:
            with open(file_path, 'rb') as f:
                state = pickle.load(f)
            
            # Restore state (simplified restoration)
            self.learning_stats = state.get("learning_stats", self.learning_stats)
            self.cost_optimizer.cost_factors = state.get("cost_factors", self.cost_optimizer.cost_factors)
            
            logger.info(f"Loaded learning state from {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to load learning state: {e}")


__all__ = [
    "SupervisionFeedbackLearner",
    "FeedbackEvent",
    "FeedbackType",
    "PerformanceMetrics",
    "RoutingStrategyAnalyzer",
    "CostPredictionOptimizer",
]