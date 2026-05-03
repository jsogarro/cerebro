"""
Registry for supervisor metadata, workers, and metrics.

This keeps the static supervisor/worker catalog separate from orchestration
logic while preserving the in-memory data structures used by the API service.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from src.models.supervisor_api_models import (
    SupervisorInfo,
    SupervisorType,
    WorkerInfo,
    WorkerStatus,
)


@dataclass
class SupervisorMetrics:
    """Metrics tracking for supervisor performance."""

    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_execution_time_ms: float = 0.0
    total_quality_score: float = 0.0
    worker_utilization_samples: list[float] = field(default_factory=list)
    last_execution_time: datetime | None = None


class SupervisorRegistry:
    """In-memory registry for supervisors and their workers."""

    def __init__(self) -> None:
        self.supervisors: dict[str, SupervisorInfo] = {}
        self.workers: dict[str, list[WorkerInfo]] = {}
        self.metrics: dict[str, SupervisorMetrics] = {}
        self.initialize()

    def initialize(self) -> None:
        """Initialize available supervisors based on domains."""
        for supervisor_type in SupervisorType:
            supervisor_id = str(uuid.uuid4())
            self.supervisors[supervisor_type.value] = SupervisorInfo(
                supervisor_id=supervisor_id,
                supervisor_type=supervisor_type,
                status="active",
                capabilities=self.get_supervisor_capabilities(supervisor_type),
                worker_count=self.get_worker_count(supervisor_type),
                active_tasks=0,
                performance_metrics=self.get_initial_metrics(supervisor_type),
            )

            self.metrics[supervisor_type.value] = SupervisorMetrics()
            self.workers[supervisor_type.value] = self.create_workers_for_supervisor(
                supervisor_type
            )

    def get_supervisor_capabilities(
        self, supervisor_type: SupervisorType
    ) -> list[str]:
        """Get capabilities for a supervisor type."""
        capabilities_map = {
            SupervisorType.RESEARCH: [
                "literature_review",
                "comparative_analysis",
                "methodology_design",
                "synthesis",
                "citation_management",
            ],
            SupervisorType.CONTENT: [
                "content_creation",
                "editing",
                "optimization",
                "seo",
                "formatting",
            ],
            SupervisorType.ANALYTICS: [
                "data_analysis",
                "visualization",
                "statistical_modeling",
                "prediction",
                "reporting",
            ],
            SupervisorType.SERVICE: [
                "customer_support",
                "troubleshooting",
                "documentation",
                "feedback_analysis",
                "escalation",
            ],
            SupervisorType.GENERAL: [
                "task_coordination",
                "resource_allocation",
                "monitoring",
                "quality_assurance",
                "reporting",
            ],
        }
        return capabilities_map.get(supervisor_type, ["general_coordination"])

    def get_worker_count(self, supervisor_type: SupervisorType) -> int:
        """Get initial worker count for supervisor type."""
        worker_counts = {
            SupervisorType.RESEARCH: 5,
            SupervisorType.CONTENT: 4,
            SupervisorType.ANALYTICS: 3,
            SupervisorType.SERVICE: 3,
            SupervisorType.GENERAL: 2,
        }
        return worker_counts.get(supervisor_type, 2)

    def get_initial_metrics(self, supervisor_type: SupervisorType) -> dict[str, float]:
        """Get initial performance metrics for supervisor."""
        return {
            "success_rate": 0.95,
            "average_quality": 0.88,
            "average_latency_ms": 2500,
            "cost_efficiency": 0.82,
            "worker_satisfaction": 0.90,
        }

    def create_workers_for_supervisor(
        self, supervisor_type: SupervisorType
    ) -> list[WorkerInfo]:
        """Create worker agents for a supervisor."""
        workers = []
        worker_types = self.get_worker_types_for_supervisor(supervisor_type)

        for i, worker_type in enumerate(worker_types):
            worker = WorkerInfo(
                worker_id=f"{supervisor_type.value}-worker-{i + 1}",
                worker_type=worker_type,
                status=WorkerStatus.IDLE,
                capabilities=self.get_worker_capabilities(worker_type),
                performance_score=random.uniform(0.8, 0.95),
                current_task=None,
            )
            workers.append(worker)

        return workers

    def get_worker_types_for_supervisor(
        self, supervisor_type: SupervisorType
    ) -> list[str]:
        """Get worker types for a supervisor."""
        worker_types_map = {
            SupervisorType.RESEARCH: [
                "literature_review",
                "comparative_analysis",
                "methodology",
                "synthesis",
                "citation",
            ],
            SupervisorType.CONTENT: [
                "writer",
                "editor",
                "optimizer",
                "formatter",
            ],
            SupervisorType.ANALYTICS: [
                "data_analyst",
                "statistician",
                "visualizer",
            ],
            SupervisorType.SERVICE: [
                "support_agent",
                "troubleshooter",
                "escalation_specialist",
            ],
            SupervisorType.GENERAL: [
                "coordinator",
                "monitor",
            ],
        }
        return worker_types_map.get(supervisor_type, ["general_worker"])

    def get_worker_capabilities(self, worker_type: str) -> list[str]:
        """Get capabilities for a worker type."""
        capabilities_map = {
            "literature_review": ["search", "extract", "summarize", "evaluate"],
            "comparative_analysis": ["compare", "contrast", "evaluate", "synthesize"],
            "methodology": ["design", "validate", "recommend", "critique"],
            "synthesis": ["integrate", "summarize", "conclude", "recommend"],
            "citation": ["format", "validate", "cross_reference", "manage"],
            "writer": ["create", "draft", "structure", "style"],
            "editor": ["review", "correct", "improve", "polish"],
            "data_analyst": ["analyze", "interpret", "model", "predict"],
            "support_agent": ["respond", "assist", "resolve", "document"],
        }
        return capabilities_map.get(worker_type, ["execute", "report"])
