# Real-Time Monitoring for A/B Testing System

## Overview

Real-time monitoring is critical for the Enhanced A/B Testing System, enabling immediate visibility into experiment performance, automated decision-making, and rapid response to issues. This document details the comprehensive monitoring infrastructure built on WebSocket technology and modern visualization frameworks.

## Architecture

### WebSocket Integration

```python
# src/ai_brain/experimentation/monitoring/websocket_monitor.py

from fastapi import WebSocket
from typing import Dict, Any, List
import asyncio
import json

class ExperimentWebSocketManager:
    """
    Manages WebSocket connections for real-time experiment monitoring.
    """
    
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.experiment_subscriptions: Dict[str, List[str]] = {}
        
    async def connect(self, websocket: WebSocket, client_id: str):
        """
        Accept WebSocket connection from monitoring client.
        """
        await websocket.accept()
        
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        
        self.active_connections[client_id].append(websocket)
        
        # Send initial experiment state
        await self.send_initial_state(websocket)
        
    async def disconnect(self, client_id: str, websocket: WebSocket):
        """
        Handle client disconnection.
        """
        if client_id in self.active_connections:
            self.active_connections[client_id].remove(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]
                
    async def subscribe_to_experiment(self, 
                                     client_id: str, 
                                     experiment_id: str):
        """
        Subscribe client to specific experiment updates.
        """
        if experiment_id not in self.experiment_subscriptions:
            self.experiment_subscriptions[experiment_id] = []
            
        if client_id not in self.experiment_subscriptions[experiment_id]:
            self.experiment_subscriptions[experiment_id].append(client_id)
            
    async def broadcast_experiment_update(self, 
                                         experiment_id: str, 
                                         update: Dict[str, Any]):
        """
        Broadcast update to all subscribed clients.
        """
        if experiment_id in self.experiment_subscriptions:
            message = json.dumps({
                'type': 'experiment_update',
                'experiment_id': experiment_id,
                'data': update,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            for client_id in self.experiment_subscriptions[experiment_id]:
                if client_id in self.active_connections:
                    for websocket in self.active_connections[client_id]:
                        try:
                            await websocket.send_text(message)
                        except:
                            # Handle disconnected clients
                            await self.disconnect(client_id, websocket)
```

### Real-Time Metrics Pipeline

```python
# src/ai_brain/experimentation/monitoring/metrics_pipeline.py

from dataclasses import dataclass
from typing import Optional, List
import time

@dataclass
class ExperimentMetric:
    """Real-time metric data point."""
    experiment_id: str
    variant: str
    metric_name: str
    value: float
    timestamp: float
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class MetricsPipeline:
    """
    High-throughput metrics processing pipeline.
    """
    
    def __init__(self, websocket_manager: ExperimentWebSocketManager):
        self.ws_manager = websocket_manager
        self.metric_buffer: List[ExperimentMetric] = []
        self.flush_interval = 1.0  # Flush every second
        self.last_flush = time.time()
        
    async def record_metric(self, metric: ExperimentMetric):
        """
        Record a metric and trigger real-time updates.
        """
        # Add to buffer
        self.metric_buffer.append(metric)
        
        # Check if we should flush
        if time.time() - self.last_flush > self.flush_interval:
            await self.flush_metrics()
            
    async def flush_metrics(self):
        """
        Process buffered metrics and send updates.
        """
        if not self.metric_buffer:
            return
            
        # Group metrics by experiment
        experiments_metrics = {}
        for metric in self.metric_buffer:
            if metric.experiment_id not in experiments_metrics:
                experiments_metrics[metric.experiment_id] = []
            experiments_metrics[metric.experiment_id].append(metric)
            
        # Process each experiment's metrics
        for experiment_id, metrics in experiments_metrics.items():
            # Calculate aggregates
            aggregates = await self._calculate_aggregates(metrics)
            
            # Update statistical analysis
            analysis = await self._update_statistical_analysis(
                experiment_id, 
                metrics
            )
            
            # Prepare update message
            update = {
                'metrics': self._serialize_metrics(metrics),
                'aggregates': aggregates,
                'analysis': analysis,
                'sample_count': len(metrics)
            }
            
            # Broadcast to subscribers
            await self.ws_manager.broadcast_experiment_update(
                experiment_id,
                update
            )
            
        # Clear buffer
        self.metric_buffer = []
        self.last_flush = time.time()
```

## Dashboard Components

### Main Experiment Dashboard

```python
# src/ai_brain/experimentation/monitoring/dashboard.py

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

class ExperimentDashboard:
    """
    Generate real-time dashboard visualizations.
    """
    
    def create_overview_dashboard(self, experiments: List[Experiment]) -> Dict:
        """
        Create overview dashboard for all active experiments.
        """
        # Create subplots
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'Active Experiments Status',
                'Conversion Rates by Variant',
                'Statistical Significance Progress',
                'Cost/Performance Tradeoff'
            ),
            specs=[
                [{'type': 'bar'}, {'type': 'box'}],
                [{'type': 'scatter'}, {'type': 'scatter'}]
            ]
        )
        
        # Active Experiments Status
        status_counts = pd.DataFrame(experiments).groupby('status').size()
        fig.add_trace(
            go.Bar(
                x=status_counts.index,
                y=status_counts.values,
                marker_color=['green', 'yellow', 'red', 'blue']
            ),
            row=1, col=1
        )
        
        # Conversion Rates by Variant
        for exp in experiments:
            if exp.metrics:
                fig.add_trace(
                    go.Box(
                        y=exp.metrics.conversion_rates,
                        name=exp.name,
                        boxmean='sd'
                    ),
                    row=1, col=2
                )
        
        # Statistical Significance Progress
        for exp in experiments:
            fig.add_trace(
                go.Scatter(
                    x=exp.timeline,
                    y=exp.p_values,
                    mode='lines+markers',
                    name=exp.name,
                    line=dict(width=2)
                ),
                row=2, col=1
            )
        
        # Add significance threshold line
        fig.add_hline(
            y=0.05, 
            line_dash="dash", 
            line_color="red",
            annotation_text="p=0.05",
            row=2, col=1
        )
        
        # Cost/Performance Tradeoff
        for exp in experiments:
            if exp.cost_metrics:
                fig.add_trace(
                    go.Scatter(
                        x=exp.cost_metrics.cost_per_query,
                        y=exp.cost_metrics.quality_score,
                        mode='markers',
                        marker=dict(
                            size=exp.cost_metrics.sample_size / 100,
                            color=exp.cost_metrics.variant_id,
                            showscale=True
                        ),
                        text=exp.variant_names,
                        name=exp.name
                    ),
                    row=2, col=2
                )
        
        # Update layout
        fig.update_layout(
            height=800,
            showlegend=True,
            title_text="Cerebro A/B Testing Dashboard",
            title_font_size=20
        )
        
        return fig.to_dict()
```

### Individual Experiment Monitor

```python
class ExperimentMonitor:
    """
    Detailed monitoring for individual experiments.
    """
    
    def create_experiment_dashboard(self, 
                                   experiment_id: str,
                                   data: ExperimentData) -> Dict:
        """
        Create detailed dashboard for single experiment.
        """
        fig = make_subplots(
            rows=3, cols=3,
            subplot_titles=(
                'Variant Performance',
                'Confidence Intervals',
                'Sample Size Progress',
                'P-Value Timeline',
                'Effect Size',
                'Conversion Funnel',
                'Cost Analysis',
                'User Segments',
                'Predictions'
            )
        )
        
        # Variant Performance (Real-time)
        self._add_variant_performance(fig, data, row=1, col=1)
        
        # Confidence Intervals
        self._add_confidence_intervals(fig, data, row=1, col=2)
        
        # Sample Size Progress
        self._add_sample_progress(fig, data, row=1, col=3)
        
        # P-Value Timeline
        self._add_pvalue_timeline(fig, data, row=2, col=1)
        
        # Effect Size with ROPE
        self._add_effect_size(fig, data, row=2, col=2)
        
        # Conversion Funnel
        self._add_conversion_funnel(fig, data, row=2, col=3)
        
        # Cost Analysis
        self._add_cost_analysis(fig, data, row=3, col=1)
        
        # User Segments
        self._add_user_segments(fig, data, row=3, col=2)
        
        # Predictions
        self._add_predictions(fig, data, row=3, col=3)
        
        return fig.to_dict()
    
    def _add_variant_performance(self, fig, data, row, col):
        """Add variant performance comparison."""
        for variant in data.variants:
            fig.add_trace(
                go.Bar(
                    x=[variant.name],
                    y=[variant.conversion_rate],
                    error_y=dict(
                        type='data',
                        array=[variant.std_error],
                        visible=True
                    ),
                    name=variant.name,
                    marker_color=variant.color
                ),
                row=row, col=col
            )
```

### Live Statistical Analysis Display

```python
class StatisticalAnalysisDisplay:
    """
    Real-time statistical analysis visualization.
    """
    
    def create_analysis_view(self, analysis: StatisticalAnalysis) -> Dict:
        """
        Create comprehensive statistical analysis view.
        """
        return {
            'summary_stats': self._format_summary_stats(analysis),
            'hypothesis_test': self._format_hypothesis_test(analysis),
            'bayesian_analysis': self._format_bayesian_analysis(analysis),
            'bandit_performance': self._format_bandit_performance(analysis),
            'recommendations': self._generate_recommendations(analysis)
        }
    
    def _format_bayesian_analysis(self, analysis: StatisticalAnalysis) -> Dict:
        """Format Bayesian analysis results."""
        return {
            'probability_best': {
                variant.name: variant.prob_best 
                for variant in analysis.variants
            },
            'expected_lift': analysis.expected_lift,
            'credible_interval': analysis.credible_interval,
            'rope_analysis': {
                'in_rope': analysis.rope_probability,
                'practical_significance': analysis.practical_significance
            },
            'posterior_plots': self._generate_posterior_plots(analysis)
        }
```

## Real-Time Alerts and Triggers

### Automated Alert System

```python
# src/ai_brain/experimentation/monitoring/alerts.py

from enum import Enum
from typing import List, Optional

class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

class AlertType(Enum):
    STATISTICAL_SIGNIFICANCE = "statistical_significance"
    SAMPLE_SIZE_REACHED = "sample_size_reached"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    EXPERIMENT_ERROR = "experiment_error"
    WINNER_IDENTIFIED = "winner_identified"
    EARLY_STOPPING = "early_stopping"

class ExperimentAlertManager:
    """
    Manage real-time alerts for experiments.
    """
    
    def __init__(self, notification_service: NotificationService):
        self.notification_service = notification_service
        self.alert_rules = self._initialize_alert_rules()
        
    async def check_alerts(self, 
                          experiment: Experiment,
                          current_metrics: MetricsSnapshot):
        """
        Check for alert conditions and trigger notifications.
        """
        triggered_alerts = []
        
        for rule in self.alert_rules:
            if rule.applies_to(experiment):
                if rule.check_condition(current_metrics):
                    alert = Alert(
                        type=rule.alert_type,
                        severity=rule.severity,
                        experiment_id=experiment.id,
                        message=rule.format_message(experiment, current_metrics),
                        metadata=rule.extract_metadata(current_metrics)
                    )
                    triggered_alerts.append(alert)
                    
        # Send notifications
        for alert in triggered_alerts:
            await self._send_alert(alert)
            
    async def _send_alert(self, alert: Alert):
        """
        Send alert through appropriate channels.
        """
        # WebSocket broadcast for real-time UI updates
        await self.ws_manager.broadcast_alert(alert)
        
        # Email/Slack for critical alerts
        if alert.severity == AlertSeverity.CRITICAL:
            await self.notification_service.send_critical_alert(alert)
            
        # Log all alerts
        await self.log_alert(alert)
```

### Decision Triggers

```python
class DecisionTriggerManager:
    """
    Automated decision triggers based on real-time data.
    """
    
    async def evaluate_triggers(self, 
                               experiment: Experiment,
                               analysis: StatisticalAnalysis):
        """
        Evaluate if automated decisions should be triggered.
        """
        # Check for winner
        if self._has_clear_winner(analysis):
            await self._trigger_winner_promotion(experiment, analysis)
            
        # Check for early stopping
        elif self._should_stop_early(analysis):
            await self._trigger_early_stopping(experiment, analysis)
            
        # Check for allocation adjustment (bandits)
        elif experiment.type == ExperimentType.MULTI_ARMED_BANDIT:
            await self._adjust_allocation(experiment, analysis)
            
    def _has_clear_winner(self, analysis: StatisticalAnalysis) -> bool:
        """
        Determine if we have a statistically significant winner.
        """
        return (
            analysis.p_value < 0.05 and
            analysis.sample_size >= analysis.minimum_sample_size and
            analysis.effect_size > analysis.minimum_effect_size and
            analysis.confidence_interval.lower > 0
        )
```

## Performance Analytics

### Real-Time Performance Metrics

```python
class PerformanceMonitor:
    """
    Monitor experiment system performance.
    """
    
    async def collect_performance_metrics(self) -> PerformanceMetrics:
        """
        Collect system performance metrics.
        """
        return PerformanceMetrics(
            active_experiments=await self._count_active_experiments(),
            metrics_per_second=await self._calculate_metrics_throughput(),
            websocket_connections=self.ws_manager.connection_count(),
            average_latency=await self._calculate_average_latency(),
            memory_usage=self._get_memory_usage(),
            cpu_usage=self._get_cpu_usage(),
            storage_usage=await self._get_storage_usage(),
            error_rate=await self._calculate_error_rate()
        )
    
    async def create_performance_dashboard(self) -> Dict:
        """
        Create performance monitoring dashboard.
        """
        metrics = await self.collect_performance_metrics()
        
        fig = go.Figure()
        
        # Metrics throughput timeline
        fig.add_trace(go.Scatter(
            x=metrics.timeline,
            y=metrics.throughput_history,
            mode='lines',
            name='Metrics/sec',
            line=dict(color='blue', width=2)
        ))
        
        # Latency timeline
        fig.add_trace(go.Scatter(
            x=metrics.timeline,
            y=metrics.latency_history,
            mode='lines',
            name='Avg Latency (ms)',
            yaxis='y2',
            line=dict(color='red', width=2)
        ))
        
        # Update layout with dual y-axes
        fig.update_layout(
            title='Experiment System Performance',
            xaxis_title='Time',
            yaxis=dict(title='Metrics/second'),
            yaxis2=dict(
                title='Latency (ms)',
                overlaying='y',
                side='right'
            )
        )
        
        return fig.to_dict()
```

## Health Monitoring

### System Health Checks

```python
class ExperimentSystemHealth:
    """
    Monitor health of experimentation system.
    """
    
    async def check_health(self) -> HealthStatus:
        """
        Comprehensive health check.
        """
        checks = {
            'database': await self._check_database_health(),
            'websocket': await self._check_websocket_health(),
            'metrics_pipeline': await self._check_metrics_pipeline(),
            'statistical_engine': await self._check_statistical_engine(),
            'alert_system': await self._check_alert_system()
        }
        
        overall_status = HealthStatus.HEALTHY
        issues = []
        
        for component, status in checks.items():
            if status != ComponentStatus.HEALTHY:
                overall_status = HealthStatus.DEGRADED
                issues.append(f"{component}: {status.message}")
                
                if status == ComponentStatus.CRITICAL:
                    overall_status = HealthStatus.UNHEALTHY
                    
        return HealthStatus(
            status=overall_status,
            components=checks,
            issues=issues,
            timestamp=datetime.utcnow()
        )
```

## Integration with Existing Systems

### WebSocket Infrastructure Integration

```python
# Integration with existing Cerebro WebSocket system

class ExperimentWebSocketIntegration:
    """
    Integrate experiment monitoring with main WebSocket system.
    """
    
    async def register_experiment_handlers(self, 
                                          main_ws_manager: WebSocketManager):
        """
        Register experiment-specific WebSocket handlers.
        """
        # Add experiment namespace
        main_ws_manager.add_namespace(
            '/experiments',
            self.experiment_ws_manager
        )
        
        # Register message handlers
        main_ws_manager.register_handler(
            'subscribe_experiment',
            self.handle_subscription
        )
        
        main_ws_manager.register_handler(
            'request_analysis',
            self.handle_analysis_request
        )
        
        main_ws_manager.register_handler(
            'trigger_decision',
            self.handle_decision_trigger
        )
```

### Prometheus Metrics Export

```python
from prometheus_client import Counter, Histogram, Gauge

# Experiment metrics for Prometheus
experiment_assignments = Counter(
    'cerebro_experiment_assignments_total',
    'Total experiment assignments',
    ['experiment_id', 'variant']
)

experiment_conversions = Counter(
    'cerebro_experiment_conversions_total',
    'Total experiment conversions',
    ['experiment_id', 'variant']
)

experiment_latency = Histogram(
    'cerebro_experiment_decision_latency_seconds',
    'Experiment decision latency',
    ['experiment_type']
)

active_experiments_gauge = Gauge(
    'cerebro_active_experiments',
    'Number of active experiments'
)

class PrometheusExporter:
    """
    Export experiment metrics to Prometheus.
    """
    
    def record_assignment(self, experiment_id: str, variant: str):
        """Record experiment assignment."""
        experiment_assignments.labels(
            experiment_id=experiment_id,
            variant=variant
        ).inc()
        
    def record_conversion(self, experiment_id: str, variant: str):
        """Record conversion event."""
        experiment_conversions.labels(
            experiment_id=experiment_id,
            variant=variant
        ).inc()
```

## Visualization Examples

### Live Experiment Progress

```javascript
// Frontend WebSocket connection for live updates

const ws = new WebSocket('ws://localhost:8000/ws/experiments');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'experiment_update') {
        updateExperimentChart(data.experiment_id, data.data);
    } else if (data.type === 'alert') {
        showAlert(data.alert);
    } else if (data.type === 'analysis_complete') {
        updateAnalysisView(data.analysis);
    }
};

function updateExperimentChart(experimentId, data) {
    // Update Plotly chart with new data
    Plotly.extendTraces(
        `chart-${experimentId}`,
        {
            x: [[new Date()]],
            y: [[data.conversion_rate]]
        },
        [0]
    );
}
```

## Best Practices

### 1. Real-Time Data Management
- Buffer metrics for efficient batch processing
- Use WebSocket compression for large updates
- Implement client-side caching for static data
- Throttle updates to prevent overwhelming clients

### 2. Dashboard Design
- Focus on actionable metrics
- Use appropriate visualizations for data types
- Provide drill-down capabilities
- Include confidence intervals and uncertainty

### 3. Alert Configuration
- Set appropriate thresholds to avoid alert fatigue
- Use severity levels appropriately
- Include context in alert messages
- Implement alert acknowledgment workflow

### 4. Performance Optimization
- Use server-side aggregation for large datasets
- Implement pagination for historical data
- Cache computed statistics
- Use efficient data structures for real-time processing

### 5. System Reliability
- Implement circuit breakers for external dependencies
- Use graceful degradation for non-critical features
- Maintain audit logs for all decisions
- Regular health checks and monitoring

## Conclusion

The real-time monitoring system provides comprehensive visibility into the Enhanced A/B Testing System, enabling data-driven decisions, rapid issue detection, and continuous optimization. By integrating with Cerebro's existing WebSocket infrastructure and leveraging modern visualization tools, we create an intuitive and powerful monitoring experience that accelerates experimentation velocity while maintaining system reliability.