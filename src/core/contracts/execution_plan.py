"""Immutable routing authority compiled once before execution dispatch."""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .base import (
    ContractId,
    ContractModel,
    JsonObject,
    Version,
    require_unique,
)


class CollaborationMode(StrEnum):
    """Canonical graph semantics that a compiler may select."""

    FAST_PATH = "fast_path"
    DIRECT = "direct"
    PARALLEL = "parallel"
    HIERARCHICAL = "hierarchical"
    DEBATE = "debate"
    ENSEMBLE = "ensemble"


class CollaborationModeSupport(StrEnum):
    """Wave disposition for every collaboration mode advertised by MASR."""

    IMPLEMENTED = "implemented"
    EXPLICITLY_REJECTED = "explicitly_rejected"
    OUT_OF_WAVE_2 = "out_of_wave_2"


COLLABORATION_MODE_SUPPORT: Mapping[CollaborationMode, CollaborationModeSupport] = (
    MappingProxyType(
        {
            CollaborationMode.FAST_PATH: CollaborationModeSupport.IMPLEMENTED,
            CollaborationMode.DIRECT: CollaborationModeSupport.IMPLEMENTED,
            CollaborationMode.PARALLEL: CollaborationModeSupport.IMPLEMENTED,
            CollaborationMode.HIERARCHICAL: CollaborationModeSupport.IMPLEMENTED,
            CollaborationMode.DEBATE: CollaborationModeSupport.EXPLICITLY_REJECTED,
            CollaborationMode.ENSEMBLE: CollaborationModeSupport.EXPLICITLY_REJECTED,
        }
    )
)


class FallbackMode(StrEnum):
    """Whether provider failure closes execution or follows an ordered list."""

    FAIL_CLOSED = "fail_closed"
    ORDERED_PROVIDER_MODEL = "ordered_provider_model"


class AmendmentValidationStatus(StrEnum):
    """Recorded compiler outcome for an amendment authority check."""

    APPROVED = "approved"
    REJECTED = "rejected"


class WorkerAssignment(ContractModel):
    """One stable worker node and its non-provider execution authority."""

    worker_id: ContractId
    worker_type: ContractId
    objective: str = Field(min_length=1)
    output_schema: JsonObject
    permission_scopes: tuple[ContractId, ...]
    tool_allowlist: tuple[ContractId, ...]

    @model_validator(mode="after")
    def validate_ordered_sets(self) -> Self:
        require_unique(self.permission_scopes, field_name="permission_scopes")
        require_unique(self.tool_allowlist, field_name="tool_allowlist")
        return self


class RoutingEdge(ContractModel):
    """One directed, named edge in the admitted execution graph."""

    source_node_id: ContractId
    target_node_id: ContractId
    relation: ContractId

    @model_validator(mode="after")
    def validate_distinct_nodes(self) -> Self:
        if self.source_node_id == self.target_node_id:
            raise ValueError("A routing edge cannot target itself")
        return self


class ProviderModelRoute(ContractModel):
    """One exact provider/model pair; neither component may be defaulted."""

    provider: ContractId
    model: ContractId


class ProviderModelPolicy(ContractModel):
    """Primary provider/model authority and its only allowed fallback order."""

    primary: ProviderModelRoute
    fallback_mode: FallbackMode
    fallbacks: tuple[ProviderModelRoute, ...]
    provider_allowlist: tuple[ContractId, ...] = Field(min_length=1)
    model_allowlist: tuple[ContractId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_routes(self) -> Self:
        require_unique(self.provider_allowlist, field_name="provider_allowlist")
        require_unique(self.model_allowlist, field_name="model_allowlist")
        routes = (self.primary, *self.fallbacks)
        route_keys = tuple((route.provider, route.model) for route in routes)
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("provider/model routes must be unique")
        if self.fallback_mode == FallbackMode.FAIL_CLOSED and self.fallbacks:
            raise ValueError("fail_closed policy cannot declare fallback routes")
        if (
            self.fallback_mode == FallbackMode.ORDERED_PROVIDER_MODEL
            and not self.fallbacks
        ):
            raise ValueError("ordered_provider_model requires at least one fallback")
        for route in routes:
            if route.provider not in self.provider_allowlist:
                raise ValueError(
                    f"Provider {route.provider!r} is not in the provider allowlist"
                )
            if route.model not in self.model_allowlist:
                raise ValueError(f"Model {route.model!r} is not in the model allowlist")
        return self


class ExecutionBudget(ContractModel):
    """Complete operational ceiling; omitted values cannot become defaults."""

    max_cost_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    max_total_tokens: int = Field(ge=1, strict=True)
    max_tool_invocations: int = Field(ge=0, strict=True)
    max_parallel_tasks: int = Field(ge=1, strict=True)
    max_attempts_per_task: int = Field(ge=1, strict=True)
    task_timeout_seconds: int = Field(ge=1, strict=True)


class RoutingDecision(ContractModel):
    """The complete, immutable authority consumed after routing compilation."""

    routing_decision_id: ContractId
    strategy: ContractId
    domains: tuple[ContractId, ...] = Field(min_length=1)
    collaboration_mode: CollaborationMode
    supervisor_id: ContractId | None
    supervisor_type: ContractId | None
    workers: tuple[WorkerAssignment, ...] = Field(min_length=1)
    edges: tuple[RoutingEdge, ...]
    provider_model_policy: ProviderModelPolicy
    budget: ExecutionBudget
    stop_conditions: tuple[ContractId, ...] = Field(min_length=1)
    evaluator_requirements: tuple[ContractId, ...]

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        require_unique(self.domains, field_name="domains")
        require_unique(self.stop_conditions, field_name="stop_conditions")
        require_unique(
            self.evaluator_requirements,
            field_name="evaluator_requirements",
        )
        if (self.supervisor_id is None) != (self.supervisor_type is None):
            raise ValueError(
                "supervisor_id and supervisor_type must be provided together"
            )

        worker_ids = tuple(worker.worker_id for worker in self.workers)
        require_unique(worker_ids, field_name="worker_id")
        node_ids = set(worker_ids)
        if self.supervisor_id is not None:
            if self.supervisor_id in node_ids:
                raise ValueError("supervisor_id must be distinct from worker ids")
            node_ids.add(self.supervisor_id)

        edge_keys = tuple(
            (edge.source_node_id, edge.target_node_id, edge.relation)
            for edge in self.edges
        )
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("routing edges must be unique")
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                raise ValueError(
                    f"Routing edge references unknown topology node "
                    f"{edge.source_node_id!r}"
                )
            if edge.target_node_id not in node_ids:
                raise ValueError(
                    f"Routing edge references unknown topology node "
                    f"{edge.target_node_id!r}"
                )

        if self.budget.max_parallel_tasks > len(self.workers):
            raise ValueError("max_parallel_tasks cannot exceed admitted worker count")

        if self.collaboration_mode in {
            CollaborationMode.FAST_PATH,
            CollaborationMode.DIRECT,
        }:
            if len(self.workers) != 1:
                raise ValueError(
                    f"{self.collaboration_mode.value} requires exactly one worker"
                )
            if self.supervisor_id is not None:
                raise ValueError(
                    f"{self.collaboration_mode.value} cannot declare a supervisor"
                )
            if self.edges:
                raise ValueError(
                    f"{self.collaboration_mode.value} cannot declare routing edges"
                )
        if (
            self.collaboration_mode == CollaborationMode.PARALLEL
            and len(self.workers) < 2
        ):
            raise ValueError("parallel requires at least two workers")
        if (
            self.collaboration_mode == CollaborationMode.HIERARCHICAL
            and self.supervisor_id is None
        ):
            raise ValueError("hierarchical requires an explicit supervisor")
        return self


class PlanAmendment(ContractModel):
    """Lineage and compiler validation attached to a replacement plan."""

    prior_execution_plan_id: ContractId
    prior_plan_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1)
    policy_validation: AmendmentValidationStatus
    budget_validation: AmendmentValidationStatus

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("amendment reason must be non-empty")
        return normalized


class ExecutionPlan(ContractModel):
    """One immutable plan version tied to existing Run/workflow/policy records."""

    execution_plan_id: ContractId
    plan_version: int = Field(ge=1, strict=True)
    run_id: ContractId
    workflow_definition_id: ContractId
    workflow_definition_version: Version
    routing_policy_id: ContractId
    routing_policy_version: Version
    routing_decision: RoutingDecision
    compiled_at: AwareDatetime
    deadline: AwareDatetime
    amendment: PlanAmendment | None

    @model_validator(mode="after")
    def validate_version_and_deadline(self) -> Self:
        if self.deadline <= self.compiled_at:
            raise ValueError("deadline must follow compiled_at")
        if self.plan_version == 1:
            if self.amendment is not None:
                raise ValueError("plan version 1 cannot be an amendment")
            return self
        if self.amendment is None:
            raise ValueError("plan version greater than 1 requires amendment metadata")
        if self.amendment.prior_plan_version != self.plan_version - 1:
            raise ValueError(
                "amendment must reference the immediately prior plan version"
            )
        if self.amendment.prior_execution_plan_id == self.execution_plan_id:
            raise ValueError("amendment must reference a distinct prior execution plan")
        approved = AmendmentValidationStatus.APPROVED
        if (
            self.amendment.policy_validation != approved
            or self.amendment.budget_validation != approved
        ):
            raise ValueError(
                "amended plans require approved policy and budget validation"
            )
        return self
