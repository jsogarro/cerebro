from __future__ import annotations

from typing import Any, TypedDict


class HealthCheckDict(TypedDict):
    """Recurring health_check() return structure across supervisors, bridges, and services."""

    status: str
    components: dict[str, str]
    metrics: dict[str, Any]


class HealthReportDict(TypedDict):
    """Return structure of SupervisorHealthMonitor.get_health_report()."""

    total_supervisors: int
    healthy_supervisors: int
    supervisor_details: dict[str, dict[str, Any]]


class SupervisionStatsDict(TypedDict):
    """Return structure of BaseSupervisor.get_supervision_stats()."""

    supervisor: dict[str, Any]
    workers: dict[str, Any]
    communication: dict[str, Any]


class ProtocolStatsDict(TypedDict):
    """Return structure of CommunicationProtocol.get_protocol_stats()."""

    protocol: dict[str, Any]
    consensus_builder: dict[str, Any]
    active_conversations: int


class FactoryStatsDict(TypedDict):
    """Return structure of SupervisorFactory.get_factory_stats()."""

    factory_stats: dict[str, Any]
    registry: dict[str, Any]
    health_report: HealthReportDict


class BridgeStatsDict(TypedDict):
    """Return structure of MASRSupervisorBridge.get_bridge_stats()."""

    bridge: dict[str, Any]
    translator: dict[str, Any]
    executor: dict[str, Any]


class RoutingMetricsDict(TypedDict, total=False):
    """Routing decision metrics used in MASR responses."""

    level: str
    score: float
    uncertainty: float
    domains: list[str]
    subtask_count: int


class OrchestratorStatsDict(TypedDict):
    """Return structure of orchestrator get_stats() methods."""

    status: str
    total_executions: int
    average_execution_time_ms: float
    components: dict[str, Any]
