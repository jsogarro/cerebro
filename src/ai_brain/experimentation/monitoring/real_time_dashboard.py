"""
Real-Time A/B Testing Dashboard

This module provides real-time monitoring and visualization of A/B testing
experiments running on the Agent Framework APIs. It integrates with WebSocket
infrastructure for live updates and provides comprehensive analytics.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from enum import Enum

# Import WebSocket components
from src.api.websocket.connection_manager import ConnectionManager
from src.api.websocket.event_publisher import EventPublisher

# Import visualization components
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class DashboardMetric(Enum):
    """Metrics displayed on the dashboard."""
    
    # Performance metrics
    QUALITY_SCORE = "quality_score"
    LATENCY = "latency_ms"
    COST = "total_cost"
    SUCCESS_RATE = "success_rate"
    
    # Volume metrics
    REQUEST_COUNT = "request_count"
    TOKEN_USAGE = "token_usage"
    
    # Statistical metrics
    P_VALUE = "p_value"
    CONFIDENCE_INTERVAL = "confidence_interval"
    EFFECT_SIZE = "effect_size"
    STATISTICAL_POWER = "statistical_power"


@dataclass
class ExperimentSnapshot:
    """Point-in-time snapshot of experiment metrics."""
    
    experiment_id: str
    timestamp: datetime
    variants: Dict[str, Dict[str, float]]  # variant_id -> metrics
    sample_sizes: Dict[str, int]
    
    # Statistical analysis
    winning_variant: Optional[str] = None
    confidence_level: float = 0.0
    p_value: Optional[float] = None
    effect_size: Optional[float] = None
    
    # Recommendations
    recommendation: str = "Continue experiment"
    estimated_completion: Optional[datetime] = None


@dataclass
class DashboardConfig:
    """Configuration for the real-time dashboard."""
    
    update_interval_seconds: int = 5
    history_window_minutes: int = 60
    
    # Chart settings
    show_confidence_bands: bool = True
    show_statistical_significance: bool = True
    
    # Alert thresholds
    min_sample_size_alert: int = 50
    max_p_value_alert: float = 0.05
    min_effect_size_alert: float = 0.05
    
    # Display options
    metrics_to_show: List[DashboardMetric] = field(
        default_factory=lambda: [
            DashboardMetric.QUALITY_SCORE,
            DashboardMetric.LATENCY,
            DashboardMetric.COST,
            DashboardMetric.SUCCESS_RATE
        ]
    )


class RealTimeDashboard:
    """
    Real-time monitoring dashboard for A/B testing experiments.
    
    Provides live updates on experiment performance, statistical analysis,
    and recommendations through WebSocket connections.
    """
    
    def __init__(self, config: Optional[DashboardConfig] = None):
        """Initialize the real-time dashboard."""
        self.config = config or DashboardConfig()
        
        # WebSocket components
        self.connection_manager = ConnectionManager()
        self.event_publisher = EventPublisher()
        
        # Data storage
        self.experiment_history: Dict[str, List[ExperimentSnapshot]] = {}
        self.active_experiments: Set[str] = set()
        
        # Connected clients
        self.dashboard_clients: Set[str] = set()
        
        # Start background tasks
        self._start_background_tasks()
    
    def _start_background_tasks(self):
        """Start background tasks for dashboard updates."""
        asyncio.create_task(self._update_dashboard_periodically())
        asyncio.create_task(self._clean_old_data_periodically())
    
    async def _update_dashboard_periodically(self):
        """Periodically update dashboard with latest data."""
        while True:
            await asyncio.sleep(self.config.update_interval_seconds)
            await self._update_all_experiments()
    
    async def _clean_old_data_periodically(self):
        """Clean old data from history."""
        while True:
            await asyncio.sleep(600)  # Every 10 minutes
            await self._clean_old_history()
    
    # ==================== Dashboard Updates ====================
    
    async def register_experiment(
        self,
        experiment_id: str,
        experiment_config: Dict[str, Any]
    ):
        """Register a new experiment for monitoring."""
        self.active_experiments.add(experiment_id)
        self.experiment_history[experiment_id] = []
        
        # Notify dashboard clients
        await self._broadcast_to_dashboard({
            "event": "experiment_registered",
            "experiment_id": experiment_id,
            "config": experiment_config,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        logger.info(f"Registered experiment {experiment_id} for monitoring")
    
    async def update_experiment_metrics(
        self,
        experiment_id: str,
        variant_metrics: Dict[str, Dict[str, float]],
        sample_sizes: Dict[str, int],
        statistical_analysis: Optional[Dict[str, Any]] = None
    ):
        """Update metrics for an experiment."""
        if experiment_id not in self.active_experiments:
            return
        
        # Create snapshot
        snapshot = ExperimentSnapshot(
            experiment_id=experiment_id,
            timestamp=datetime.utcnow(),
            variants=variant_metrics,
            sample_sizes=sample_sizes
        )
        
        # Add statistical analysis if provided
        if statistical_analysis:
            snapshot.p_value = statistical_analysis.get("p_value")
            snapshot.effect_size = statistical_analysis.get("effect_size")
            snapshot.confidence_level = statistical_analysis.get("confidence_level", 0.0)
            snapshot.winning_variant = statistical_analysis.get("winning_variant")
            snapshot.recommendation = self._generate_recommendation(statistical_analysis)
        
        # Store in history
        if experiment_id not in self.experiment_history:
            self.experiment_history[experiment_id] = []
        self.experiment_history[experiment_id].append(snapshot)
        
        # Generate and broadcast update
        update = await self._generate_dashboard_update(experiment_id, snapshot)
        await self._broadcast_to_dashboard(update)
    
    async def _update_all_experiments(self):
        """Update all active experiments."""
        for experiment_id in self.active_experiments:
            # Get latest metrics (would integrate with experimentor here)
            # For now, using mock data
            await self._update_experiment_with_mock_data(experiment_id)
    
    async def _update_experiment_with_mock_data(self, experiment_id: str):
        """Update experiment with mock data for testing."""
        # This would be replaced with actual data from AgentFrameworkExperimentor
        import random
        
        variants = ["control", "treatment_a", "treatment_b"]
        variant_metrics = {}
        sample_sizes = {}
        
        for variant in variants:
            variant_metrics[variant] = {
                "quality_score": random.uniform(0.7, 0.95),
                "latency_ms": random.uniform(500, 2000),
                "total_cost": random.uniform(0.001, 0.01),
                "success_rate": random.uniform(0.85, 0.99)
            }
            sample_sizes[variant] = random.randint(100, 1000)
        
        await self.update_experiment_metrics(
            experiment_id=experiment_id,
            variant_metrics=variant_metrics,
            sample_sizes=sample_sizes
        )
    
    # ==================== Visualization Generation ====================
    
    async def _generate_dashboard_update(
        self,
        experiment_id: str,
        snapshot: ExperimentSnapshot
    ) -> Dict[str, Any]:
        """Generate dashboard update with visualizations."""
        update = {
            "event": "experiment_update",
            "experiment_id": experiment_id,
            "timestamp": snapshot.timestamp.isoformat(),
            "metrics": {},
            "charts": {},
            "alerts": []
        }
        
        # Add current metrics
        for variant_id, metrics in snapshot.variants.items():
            update["metrics"][variant_id] = {
                "sample_size": snapshot.sample_sizes.get(variant_id, 0),
                **metrics
            }
        
        # Generate charts
        history = self.experiment_history.get(experiment_id, [])
        
        # Time series chart
        update["charts"]["time_series"] = self._generate_time_series_chart(history)
        
        # Distribution chart
        update["charts"]["distribution"] = self._generate_distribution_chart(snapshot)
        
        # Statistical significance chart
        if snapshot.p_value is not None:
            update["charts"]["statistical"] = self._generate_statistical_chart(snapshot)
        
        # Generate alerts
        alerts = self._check_for_alerts(snapshot)
        update["alerts"] = alerts
        
        # Add recommendation
        update["recommendation"] = {
            "text": snapshot.recommendation,
            "confidence": snapshot.confidence_level,
            "winning_variant": snapshot.winning_variant
        }
        
        return update
    
    def _generate_time_series_chart(
        self,
        history: List[ExperimentSnapshot]
    ) -> Dict[str, Any]:
        """Generate time series chart data."""
        if not history:
            return {}
        
        # Prepare data
        timestamps = [s.timestamp for s in history]
        variants = list(history[0].variants.keys()) if history else []
        
        traces = []
        for metric in self.config.metrics_to_show:
            metric_name = metric.value
            
            for variant in variants:
                y_values = [
                    s.variants.get(variant, {}).get(metric_name, 0)
                    for s in history
                ]
                
                trace = {
                    "x": [t.isoformat() for t in timestamps],
                    "y": y_values,
                    "name": f"{variant}_{metric_name}",
                    "type": "scatter",
                    "mode": "lines+markers"
                }
                traces.append(trace)
        
        return {
            "data": traces,
            "layout": {
                "title": "Experiment Metrics Over Time",
                "xaxis": {"title": "Time"},
                "yaxis": {"title": "Value"},
                "showlegend": True
            }
        }
    
    def _generate_distribution_chart(
        self,
        snapshot: ExperimentSnapshot
    ) -> Dict[str, Any]:
        """Generate distribution comparison chart."""
        variants = list(snapshot.variants.keys())
        
        # Create bar chart for each metric
        traces = []
        for metric in self.config.metrics_to_show:
            metric_name = metric.value
            
            values = [
                snapshot.variants.get(v, {}).get(metric_name, 0)
                for v in variants
            ]
            
            trace = {
                "x": variants,
                "y": values,
                "name": metric_name,
                "type": "bar"
            }
            traces.append(trace)
        
        return {
            "data": traces,
            "layout": {
                "title": "Metric Distribution by Variant",
                "xaxis": {"title": "Variant"},
                "yaxis": {"title": "Value"},
                "barmode": "group"
            }
        }
    
    def _generate_statistical_chart(
        self,
        snapshot: ExperimentSnapshot
    ) -> Dict[str, Any]:
        """Generate statistical significance visualization."""
        variants = list(snapshot.variants.keys())
        
        # Create confidence interval chart
        control = variants[0] if variants else None
        if not control:
            return {}
        
        control_metrics = snapshot.variants.get(control, {})
        control_quality = control_metrics.get("quality_score", 0)
        
        # Calculate relative improvements
        improvements = []
        confidence_intervals = []
        
        for variant in variants[1:]:
            variant_metrics = snapshot.variants.get(variant, {})
            variant_quality = variant_metrics.get("quality_score", 0)
            
            improvement = ((variant_quality - control_quality) / control_quality) * 100
            improvements.append(improvement)
            
            # Mock confidence interval (would be calculated properly)
            ci_lower = improvement - 5
            ci_upper = improvement + 5
            confidence_intervals.append([ci_lower, ci_upper])
        
        trace = {
            "x": variants[1:],
            "y": improvements,
            "error_y": {
                "type": "data",
                "symmetric": False,
                "array": [ci[1] - imp for ci, imp in zip(confidence_intervals, improvements)],
                "arrayminus": [imp - ci[0] for ci, imp in zip(confidence_intervals, improvements)]
            },
            "type": "bar",
            "name": "Relative Improvement %"
        }
        
        # Add significance line
        significance_line = {
            "x": variants[1:],
            "y": [0] * len(variants[1:]),
            "type": "scatter",
            "mode": "lines",
            "name": "No Effect",
            "line": {"dash": "dash", "color": "red"}
        }
        
        return {
            "data": [trace, significance_line],
            "layout": {
                "title": f"Statistical Significance (p={snapshot.p_value:.4f})",
                "xaxis": {"title": "Variant"},
                "yaxis": {"title": "Relative Improvement (%)"},
                "showlegend": True
            }
        }
    
    # ==================== Alert Generation ====================
    
    def _check_for_alerts(self, snapshot: ExperimentSnapshot) -> List[Dict[str, Any]]:
        """Check for conditions that should trigger alerts."""
        alerts = []
        
        # Check sample size
        for variant, size in snapshot.sample_sizes.items():
            if size < self.config.min_sample_size_alert:
                alerts.append({
                    "type": "warning",
                    "message": f"Low sample size for {variant}: {size}",
                    "variant": variant
                })
        
        # Check statistical significance
        if snapshot.p_value is not None:
            if snapshot.p_value < self.config.max_p_value_alert:
                alerts.append({
                    "type": "info",
                    "message": f"Statistical significance reached (p={snapshot.p_value:.4f})",
                    "variant": snapshot.winning_variant
                })
        
        # Check effect size
        if snapshot.effect_size is not None:
            if abs(snapshot.effect_size) < self.config.min_effect_size_alert:
                alerts.append({
                    "type": "warning",
                    "message": f"Small effect size detected: {snapshot.effect_size:.4f}"
                })
        
        return alerts
    
    def _generate_recommendation(self, statistical_analysis: Dict[str, Any]) -> str:
        """Generate recommendation based on statistical analysis."""
        p_value = statistical_analysis.get("p_value")
        effect_size = statistical_analysis.get("effect_size")
        sample_size = statistical_analysis.get("total_samples", 0)
        
        if p_value is None:
            return "Continue experiment - insufficient data"
        
        if p_value < 0.05 and effect_size and abs(effect_size) > 0.1:
            winning = statistical_analysis.get("winning_variant", "unknown")
            return f"Ready to conclude - {winning} is winning with significant effect"
        
        if p_value < 0.05 but effect_size and abs(effect_size) < 0.05:
            return "Statistical significance reached but effect size is small"
        
        if sample_size < 1000:
            return "Continue experiment - need more samples for reliable conclusion"
        
        if p_value > 0.2:
            return "No significant difference detected - consider stopping"
        
        return "Continue experiment - approaching statistical significance"
    
    # ==================== WebSocket Communication ====================
    
    async def connect_dashboard_client(self, client_id: str, websocket):
        """Connect a new dashboard client."""
        self.dashboard_clients.add(client_id)
        await self.connection_manager.connect(websocket, client_id)
        
        # Send initial state
        initial_state = await self._get_dashboard_state()
        await self.connection_manager.send_json(websocket, initial_state)
        
        logger.info(f"Dashboard client {client_id} connected")
    
    async def disconnect_dashboard_client(self, client_id: str):
        """Disconnect a dashboard client."""
        self.dashboard_clients.discard(client_id)
        self.connection_manager.disconnect(client_id)
        
        logger.info(f"Dashboard client {client_id} disconnected")
    
    async def _broadcast_to_dashboard(self, message: Dict[str, Any]):
        """Broadcast message to all dashboard clients."""
        for client_id in self.dashboard_clients:
            await self.event_publisher.publish_event(
                event_type="dashboard_update",
                data=message,
                target_clients=[client_id]
            )
    
    async def _get_dashboard_state(self) -> Dict[str, Any]:
        """Get current dashboard state for new clients."""
        state = {
            "event": "dashboard_state",
            "timestamp": datetime.utcnow().isoformat(),
            "active_experiments": list(self.active_experiments),
            "experiments": {}
        }
        
        for exp_id in self.active_experiments:
            history = self.experiment_history.get(exp_id, [])
            if history:
                latest = history[-1]
                state["experiments"][exp_id] = {
                    "latest_snapshot": {
                        "timestamp": latest.timestamp.isoformat(),
                        "variants": latest.variants,
                        "sample_sizes": latest.sample_sizes,
                        "winning_variant": latest.winning_variant,
                        "p_value": latest.p_value,
                        "recommendation": latest.recommendation
                    },
                    "history_length": len(history)
                }
        
        return state
    
    # ==================== Data Management ====================
    
    async def _clean_old_history(self):
        """Remove old data from history."""
        cutoff_time = datetime.utcnow() - timedelta(
            minutes=self.config.history_window_minutes
        )
        
        for exp_id in list(self.experiment_history.keys()):
            history = self.experiment_history[exp_id]
            
            # Filter out old snapshots
            self.experiment_history[exp_id] = [
                s for s in history
                if s.timestamp > cutoff_time
            ]
            
            # Remove experiment if no recent data
            if not self.experiment_history[exp_id] and exp_id not in self.active_experiments:
                del self.experiment_history[exp_id]
    
    async def export_experiment_data(
        self,
        experiment_id: str,
        format: str = "json"
    ) -> Any:
        """Export experiment data for analysis."""
        if experiment_id not in self.experiment_history:
            raise ValueError(f"No data for experiment {experiment_id}")
        
        history = self.experiment_history[experiment_id]
        
        if format == "json":
            return [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "variants": s.variants,
                    "sample_sizes": s.sample_sizes,
                    "p_value": s.p_value,
                    "effect_size": s.effect_size,
                    "winning_variant": s.winning_variant
                }
                for s in history
            ]
        
        elif format == "dataframe":
            # Convert to pandas DataFrame
            data = []
            for snapshot in history:
                for variant, metrics in snapshot.variants.items():
                    row = {
                        "timestamp": snapshot.timestamp,
                        "variant": variant,
                        "sample_size": snapshot.sample_sizes.get(variant, 0),
                        **metrics
                    }
                    data.append(row)
            
            return pd.DataFrame(data)
        
        else:
            raise ValueError(f"Unsupported format: {format}")


# Singleton instance
_dashboard_instance = None


def get_dashboard() -> RealTimeDashboard:
    """Get singleton instance of the real-time dashboard."""
    global _dashboard_instance
    if _dashboard_instance is None:
        _dashboard_instance = RealTimeDashboard()
    return _dashboard_instance