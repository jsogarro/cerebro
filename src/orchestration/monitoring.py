"""
Monitoring and observability for LangGraph orchestration.

This module provides comprehensive monitoring, metrics collection,
and visualization for the orchestration system.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram, Summary

from src.orchestration.state import AgentExecutionStatus, WorkflowPhase

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# Prometheus metrics
workflow_counter = Counter(
    "langgraph_workflows_total",
    "Total number of workflows executed",
    ["status", "project_id"],
)

workflow_duration = Histogram(
    "langgraph_workflow_duration_seconds",
    "Workflow execution duration in seconds",
    ["phase", "project_id"],
)

agent_execution_counter = Counter(
    "langgraph_agent_executions_total",
    "Total number of agent executions",
    ["agent_type", "status"],
)

agent_execution_duration = Histogram(
    "langgraph_agent_duration_seconds",
    "Agent execution duration in seconds",
    ["agent_type"],
)

active_workflows = Gauge(
    "langgraph_active_workflows", "Number of currently active workflows"
)

workflow_quality_score = Histogram(
    "langgraph_workflow_quality_score",
    "Distribution of workflow quality scores",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

checkpoint_operations = Counter(
    "langgraph_checkpoint_operations_total",
    "Total number of checkpoint operations",
    ["operation", "status"],
)

node_execution_summary = Summary(
    "langgraph_node_execution_seconds", "Summary of node execution times", ["node_name"]
)


class MetricType(Enum):
    """Types of metrics to collect."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class WorkflowMetrics:
    """Metrics for a single workflow execution."""

    workflow_id: str
    project_id: str
    started_at: datetime
    completed_at: datetime | None = None
    total_duration: float = 0.0
    phases_completed: list[str] = field(default_factory=list)
    phase_durations: dict[str, float] = field(default_factory=dict)
    agents_executed: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_executions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    checkpoints_created: int = 0
    quality_score: float = 0.0
    total_sources: int = 0
    total_findings: int = 0
    success: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "workflow_id": self.workflow_id,
            "project_id": self.project_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_duration": self.total_duration,
            "phases_completed": self.phases_completed,
            "phase_durations": self.phase_durations,
            "agents_executed": self.agents_executed,
            "node_executions": self.node_executions,
            "errors": self.errors,
            "checkpoints_created": self.checkpoints_created,
            "quality_score": self.quality_score,
            "total_sources": self.total_sources,
            "total_findings": self.total_findings,
            "success": self.success,
        }


class OrchestrationMonitor:
    """
    Main monitoring system for LangGraph orchestration.

    Collects metrics, traces, and provides real-time monitoring
    of workflow execution.
    """

    def __init__(self, enable_tracing: bool = True):
        """
        Initialize orchestration monitor.

        Args:
            enable_tracing: Whether to enable OpenTelemetry tracing
        """
        self.enable_tracing = enable_tracing
        self._active_workflows: dict[str, WorkflowMetrics] = {}
        self._completed_workflows: list[WorkflowMetrics] = []
        self._phase_timers: dict[str, float] = {}
        self._node_timers: dict[str, float] = {}
        self._spans: dict[str, Any] = {}

    def start_workflow(self, workflow_id: str, project_id: str, query: str) -> None:
        """
        Start monitoring a workflow.

        Args:
            workflow_id: Workflow identifier
            project_id: Project identifier
            query: Research query
        """
        logger.info(f"Starting monitoring for workflow {workflow_id}")

        # Create metrics container
        metrics = WorkflowMetrics(
            workflow_id=workflow_id, project_id=project_id, started_at=datetime.utcnow()
        )

        self._active_workflows[workflow_id] = metrics

        # Update Prometheus metrics
        active_workflows.inc()

        # Start OpenTelemetry span if enabled
        if self.enable_tracing:
            span = tracer.start_span(
                "research_workflow",
                attributes={
                    "workflow.id": workflow_id,
                    "project.id": project_id,
                    "query": query[:100],  # Limit query length
                },
            )
            self._spans[workflow_id] = span

    def end_workflow(
        self, workflow_id: str, success: bool, quality_score: float = 0.0
    ) -> None:
        """
        End monitoring for a workflow.

        Args:
            workflow_id: Workflow identifier
            success: Whether workflow succeeded
            quality_score: Final quality score
        """
        if workflow_id not in self._active_workflows:
            logger.warning(f"Workflow {workflow_id} not found in active workflows")
            return

        metrics = self._active_workflows[workflow_id]
        metrics.completed_at = datetime.utcnow()
        metrics.total_duration = (
            metrics.completed_at - metrics.started_at
        ).total_seconds()
        metrics.success = success
        metrics.quality_score = quality_score

        # Update Prometheus metrics
        active_workflows.dec()
        workflow_counter.labels(
            status="success" if success else "failure", project_id=metrics.project_id
        ).inc()
        workflow_duration.labels(phase="total", project_id=metrics.project_id).observe(
            metrics.total_duration
        )

        if quality_score > 0:
            workflow_quality_score.observe(quality_score)

        # End OpenTelemetry span
        if self.enable_tracing and workflow_id in self._spans:
            span = self._spans[workflow_id]
            span.set_status(Status(StatusCode.OK if success else StatusCode.ERROR))
            span.set_attribute("quality.score", quality_score)
            span.end()
            del self._spans[workflow_id]

        # Move to completed
        self._completed_workflows.append(metrics)
        del self._active_workflows[workflow_id]

        logger.info(
            f"Workflow {workflow_id} completed. Success: {success}, Quality: {quality_score:.2f}"
        )

    def record_phase_transition(
        self, workflow_id: str, from_phase: WorkflowPhase, to_phase: WorkflowPhase
    ) -> None:
        """
        Record workflow phase transition.

        Args:
            workflow_id: Workflow identifier
            from_phase: Previous phase
            to_phase: New phase
        """
        if workflow_id not in self._active_workflows:
            return

        metrics = self._active_workflows[workflow_id]

        # Record phase completion
        if from_phase and from_phase != WorkflowPhase.INITIALIZATION:
            phase_key = f"{workflow_id}:{from_phase.value}"

            if phase_key in self._phase_timers:
                duration = time.time() - self._phase_timers[phase_key]
                metrics.phase_durations[from_phase.value] = duration
                metrics.phases_completed.append(from_phase.value)

                # Update Prometheus metrics
                workflow_duration.labels(
                    phase=from_phase.value, project_id=metrics.project_id
                ).observe(duration)

                del self._phase_timers[phase_key]

        # Start timing new phase
        if to_phase and to_phase != WorkflowPhase.COMPLETED:
            phase_key = f"{workflow_id}:{to_phase.value}"
            self._phase_timers[phase_key] = time.time()

        # Create OpenTelemetry event
        if self.enable_tracing and workflow_id in self._spans:
            span = self._spans[workflow_id]
            span.add_event(
                "phase_transition",
                attributes={
                    "from_phase": from_phase.value if from_phase else "none",
                    "to_phase": to_phase.value,
                },
            )

    def record_node_execution(
        self,
        workflow_id: str,
        node_name: str,
        duration: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """
        Record node execution metrics.

        Args:
            workflow_id: Workflow identifier
            node_name: Name of the executed node
            duration: Execution duration in seconds
            success: Whether execution succeeded
            error: Error message if failed
        """
        if workflow_id not in self._active_workflows:
            return

        metrics = self._active_workflows[workflow_id]

        # Record node execution
        execution_record = {
            "node_name": node_name,
            "duration": duration,
            "success": success,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if error:
            execution_record["error"] = error
            metrics.errors.append(
                {
                    "node": node_name,
                    "error": error,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

        metrics.node_executions.append(execution_record)

        # Update Prometheus metrics
        node_execution_summary.labels(node_name=node_name).observe(duration)

        # Create OpenTelemetry span for node
        if self.enable_tracing and workflow_id in self._spans:
            _parent_span = self._spans[workflow_id]

            with tracer.start_as_current_span(
                f"node_{node_name}"
            ) as node_span:
                node_span.set_attribute("node.name", node_name)
                node_span.set_attribute("execution.duration", duration)

                if not success:
                    node_span.set_status(Status(StatusCode.ERROR))
                    if error:
                        node_span.set_attribute("error.message", error)

    def record_agent_execution(
        self,
        workflow_id: str,
        agent_type: str,
        status: AgentExecutionStatus,
        duration: float,
        metrics_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Record agent execution metrics.

        Args:
            workflow_id: Workflow identifier
            agent_type: Type of agent
            status: Execution status
            duration: Execution duration
            metrics_data: Additional metrics data
        """
        if workflow_id not in self._active_workflows:
            return

        metrics = self._active_workflows[workflow_id]

        # Record agent execution
        if agent_type not in metrics.agents_executed:
            metrics.agents_executed[agent_type] = {
                "executions": 0,
                "total_duration": 0.0,
                "status_counts": {},
            }

        agent_metrics = metrics.agents_executed[agent_type]
        agent_metrics["executions"] += 1
        agent_metrics["total_duration"] += duration

        status_key = status.value
        agent_metrics["status_counts"][status_key] = (
            agent_metrics["status_counts"].get(status_key, 0) + 1
        )

        # Add additional metrics if provided
        if metrics_data:
            agent_metrics.update(metrics_data)

        # Update Prometheus metrics
        agent_execution_counter.labels(agent_type=agent_type, status=status.value).inc()

        agent_execution_duration.labels(agent_type=agent_type).observe(duration)

    def record_checkpoint_operation(
        self, workflow_id: str, operation: str, success: bool
    ) -> None:
        """
        Record checkpoint operation.

        Args:
            workflow_id: Workflow identifier
            operation: Operation type (create, restore, delete)
            success: Whether operation succeeded
        """
        if workflow_id in self._active_workflows:
            metrics = self._active_workflows[workflow_id]

            if operation == "create" and success:
                metrics.checkpoints_created += 1

        # Update Prometheus metrics
        checkpoint_operations.labels(
            operation=operation, status="success" if success else "failure"
        ).inc()

    def record_research_metrics(
        self, workflow_id: str, sources: int, findings: int, citations: int
    ) -> None:
        """
        Record research-specific metrics.

        Args:
            workflow_id: Workflow identifier
            sources: Number of sources found
            findings: Number of findings
            citations: Number of citations
        """
        if workflow_id not in self._active_workflows:
            return

        metrics = self._active_workflows[workflow_id]
        metrics.total_sources = sources
        metrics.total_findings = findings

        # Add to OpenTelemetry span
        if self.enable_tracing and workflow_id in self._spans:
            span = self._spans[workflow_id]
            span.set_attribute("research.sources", sources)
            span.set_attribute("research.findings", findings)
            span.set_attribute("research.citations", citations)

    def get_workflow_metrics(
        self, workflow_id: str | None = None
    ) -> WorkflowMetrics | list[WorkflowMetrics] | None:
        """
        Get metrics for workflow(s).

        Args:
            workflow_id: Specific workflow or None for all

        Returns:
            Workflow metrics
        """
        if workflow_id:
            # Check active workflows
            if workflow_id in self._active_workflows:
                return self._active_workflows[workflow_id]

            # Check completed workflows
            for metrics in self._completed_workflows:
                if metrics.workflow_id == workflow_id:
                    return metrics

            return None

        # Return all workflows
        all_workflows = list(self._active_workflows.values())
        all_workflows.extend(self._completed_workflows)
        return all_workflows

    def get_summary_statistics(self) -> dict[str, Any]:
        """
        Get summary statistics across all workflows.

        Returns:
            Summary statistics
        """
        all_workflows_raw = self.get_workflow_metrics()

        if not all_workflows_raw:
            return {}

        all_workflows: list[WorkflowMetrics]
        if isinstance(all_workflows_raw, list):
            all_workflows = all_workflows_raw
        else:
            all_workflows = [all_workflows_raw]

        completed = [w for w in all_workflows if w.completed_at]
        successful = [w for w in completed if w.success]

        # Calculate statistics
        stats = {
            "total_workflows": len(all_workflows),
            "active_workflows": len(self._active_workflows),
            "completed_workflows": len(completed),
            "successful_workflows": len(successful),
            "success_rate": len(successful) / len(completed) if completed else 0,
            "average_duration": (
                sum(w.total_duration for w in completed) / len(completed)
                if completed
                else 0
            ),
            "average_quality_score": (
                sum(w.quality_score for w in completed) / len(completed)
                if completed
                else 0
            ),
            "total_errors": sum(len(w.errors) for w in all_workflows),
            "phase_statistics": self._calculate_phase_statistics(completed),
            "agent_statistics": self._calculate_agent_statistics(all_workflows),
        }

        return stats

    def _calculate_phase_statistics(
        self, workflows: list[WorkflowMetrics]
    ) -> dict[str, Any]:
        """Calculate statistics per phase."""
        phase_stats = {}

        for workflow in workflows:
            for phase, duration in workflow.phase_durations.items():
                if phase not in phase_stats:
                    phase_stats[phase] = {
                        "count": 0,
                        "total_duration": 0.0,
                        "min_duration": float("inf"),
                        "max_duration": 0.0,
                    }

                stats = phase_stats[phase]
                stats["count"] += 1
                stats["total_duration"] += duration
                stats["min_duration"] = min(stats["min_duration"], duration)
                stats["max_duration"] = max(stats["max_duration"], duration)

        # Calculate averages
        for _phase, stats in phase_stats.items():
            if stats["count"] > 0:
                stats["average_duration"] = stats["total_duration"] / stats["count"]

        return phase_stats

    def _calculate_agent_statistics(
        self, workflows: list[WorkflowMetrics]
    ) -> dict[str, Any]:
        """Calculate statistics per agent."""
        agent_stats: dict[str, Any] = {}

        for workflow in workflows:
            for agent_type, agent_data in workflow.agents_executed.items():
                if agent_type not in agent_stats:
                    agent_stats[agent_type] = {
                        "total_executions": 0,
                        "total_duration": 0.0,
                        "status_distribution": {},
                    }

                stats: dict[str, Any] = agent_stats[agent_type]
                if isinstance(agent_data, dict):
                    stats["total_executions"] += agent_data.get("executions", 0)
                    stats["total_duration"] += agent_data.get("total_duration", 0.0)

                    # Merge status counts
                    status_counts = agent_data.get("status_counts", {})
                    if isinstance(status_counts, dict):
                        for status, count in status_counts.items():
                            status_dist: dict[str, int] = stats["status_distribution"]
                            status_dist[status] = status_dist.get(status, 0) + count

        # Calculate success rates
        for _agent_type, stats in agent_stats.items():
            if isinstance(stats, dict):
                total = stats.get("total_executions", 0)
                if isinstance(total, int) and total > 0:
                    total_duration = stats.get("total_duration", 0.0)
                    if isinstance(total_duration, (int, float)):
                        stats["average_duration"] = total_duration / total

                    status_dist = stats.get("status_distribution", {})
                    if isinstance(status_dist, dict):
                        successful = status_dist.get("completed", 0)
                        if isinstance(successful, int):
                            stats["success_rate"] = successful / total

        return agent_stats


class WorkflowVisualizer:
    """
    Visualizer for workflow execution.

    Generates visual representations of workflow execution
    for debugging and analysis.
    """

    def __init__(self, monitor: OrchestrationMonitor):
        """
        Initialize workflow visualizer.

        Args:
            monitor: Orchestration monitor instance
        """
        self.monitor = monitor

    def generate_timeline(self, workflow_id: str) -> dict[str, Any]:
        """
        Generate execution timeline for a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Timeline data
        """
        metrics = self.monitor.get_workflow_metrics(workflow_id)

        if not metrics or isinstance(metrics, list):
            return {}

        timeline: dict[str, Any] = {
            "workflow_id": workflow_id,
            "start_time": metrics.started_at.isoformat(),
            "end_time": (
                metrics.completed_at.isoformat() if metrics.completed_at else None
            ),
            "events": [],
        }

        # Add phase transitions
        current_time = metrics.started_at
        for phase in metrics.phases_completed:
            duration = metrics.phase_durations.get(phase, 0)

            if isinstance(timeline["events"], list):
                timeline["events"].append(
                    {
                        "type": "phase",
                        "name": phase,
                        "start": current_time.isoformat(),
                        "duration": duration,
                        "end": (current_time + timedelta(seconds=duration)).isoformat(),
                    }
                )

            current_time += timedelta(seconds=duration)

        # Add node executions
        for node_exec in metrics.node_executions:
            if isinstance(timeline["events"], list):
                timeline["events"].append(
                    {
                        "type": "node",
                        "name": node_exec["node_name"],
                        "timestamp": node_exec["timestamp"],
                        "duration": node_exec["duration"],
                        "success": node_exec["success"],
                    }
                )

        # Add errors
        for error in metrics.errors:
            if isinstance(timeline["events"], list):
                timeline["events"].append(
                    {
                        "type": "error",
                        "node": error["node"],
                        "message": error["error"],
                        "timestamp": error["timestamp"],
                    }
                )

        return timeline

    def generate_flow_diagram(self, workflow_id: str) -> str:
        """
        Generate flow diagram in DOT format.

        Args:
            workflow_id: Workflow identifier

        Returns:
            DOT format diagram
        """
        metrics = self.monitor.get_workflow_metrics(workflow_id)

        if not metrics or isinstance(metrics, list):
            return ""

        dot_lines = ["digraph WorkflowExecution {"]
        dot_lines.append('  rankdir="TB";')
        dot_lines.append("  node [shape=box, style=rounded];")

        # Add nodes for completed phases
        for i, phase in enumerate(metrics.phases_completed):
            color = "green" if phase in metrics.phases_completed else "gray"
            dot_lines.append(f"  {phase} [fillcolor={color}, style=filled];")

            # Add edge to next phase
            if i < len(metrics.phases_completed) - 1:
                next_phase = metrics.phases_completed[i + 1]
                dot_lines.append(f"  {phase} -> {next_phase};")

        # Add agent nodes
        for agent_type, agent_data in metrics.agents_executed.items():
            status = (
                "completed"
                if agent_data["status_counts"].get("completed", 0) > 0
                else "failed"
            )
            color = "lightgreen" if status == "completed" else "lightpink"

            dot_lines.append(
                f'  {agent_type} [label="{agent_type}\\n'
                f'Duration: {agent_data["total_duration"]:.2f}s", '
                f"fillcolor={color}, style=filled];"
            )

        dot_lines.append("}")

        return "\n".join(dot_lines)

    def generate_metrics_dashboard(self) -> dict[str, Any]:
        """
        Generate metrics dashboard data.

        Returns:
            Dashboard data
        """
        stats = self.monitor.get_summary_statistics()

        dashboard = {
            "summary": {
                "total_workflows": stats.get("total_workflows", 0),
                "active_workflows": stats.get("active_workflows", 0),
                "success_rate": f"{stats.get('success_rate', 0) * 100:.1f}%",
                "avg_duration": f"{stats.get('average_duration', 0):.1f}s",
                "avg_quality": f"{stats.get('average_quality_score', 0):.2f}",
            },
            "charts": {
                "workflow_status": self._generate_status_chart(stats),
                "phase_durations": self._generate_phase_chart(stats),
                "agent_performance": self._generate_agent_chart(stats),
            },
            "recent_errors": self._get_recent_errors(),
        }

        return dashboard

    def _generate_status_chart(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Generate workflow status chart data."""
        return {
            "type": "pie",
            "data": {
                "successful": stats.get("successful_workflows", 0),
                "failed": stats.get("completed_workflows", 0)
                - stats.get("successful_workflows", 0),
                "active": stats.get("active_workflows", 0),
            },
        }

    def _generate_phase_chart(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Generate phase duration chart data."""
        phase_stats = stats.get("phase_statistics", {})

        return {
            "type": "bar",
            "data": {
                phase: data.get("average_duration", 0)
                for phase, data in phase_stats.items()
            },
        }

    def _generate_agent_chart(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Generate agent performance chart data."""
        agent_stats = stats.get("agent_statistics", {})

        return {
            "type": "grouped_bar",
            "data": {
                agent: {
                    "success_rate": data.get("success_rate", 0) * 100,
                    "avg_duration": data.get("average_duration", 0),
                }
                for agent, data in agent_stats.items()
            },
        }

    def _get_recent_errors(self) -> list[dict[str, Any]]:
        """Get recent errors across all workflows."""
        all_errors = []

        metrics_result = self.monitor.get_workflow_metrics()
        if metrics_result is None:
            return []

        metrics_list = metrics_result if isinstance(metrics_result, list) else [metrics_result]
        for metrics in metrics_list:
            for error in metrics.errors:
                all_errors.append(
                    {
                        "workflow_id": metrics.workflow_id,
                        "node": error["node"],
                        "error": error["error"],
                        "timestamp": error["timestamp"],
                    }
                )

        # Sort by timestamp and return most recent
        all_errors.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_errors[:10]


# Global monitor instance
_global_monitor: OrchestrationMonitor | None = None


def get_monitor() -> OrchestrationMonitor:
    """Get global orchestration monitor."""
    global _global_monitor

    if _global_monitor is None:
        _global_monitor = OrchestrationMonitor()

    return _global_monitor


def initialize_monitoring(enable_tracing: bool = True) -> None:
    """Initialize global monitoring."""
    global _global_monitor

    _global_monitor = OrchestrationMonitor(enable_tracing=enable_tracing)
    logger.info("Orchestration monitoring initialized")


__all__ = [
    "OrchestrationMonitor",
    "WorkflowMetrics",
    "WorkflowVisualizer",
    "get_monitor",
    "initialize_monitoring",
]
