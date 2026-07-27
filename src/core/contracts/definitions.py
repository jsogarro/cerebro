"""Version-pinned workflow and routing policy definitions."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractId, ContractModel, JsonObject, Version, require_unique


class WorkflowControlMode(StrEnum):
    """How much step selection is fixed by the workflow definition."""

    PREDEFINED = "predefined"
    BOUNDED_AGENTIC = "bounded_agentic"


class WorkflowDefinition(ContractModel):
    """A versioned workflow boundary, distinct from an executing agent."""

    workflow_definition_id: ContractId
    workflow_version: Version
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    control_mode: WorkflowControlMode
    task_types: tuple[ContractId, ...] = Field(min_length=1)
    input_schema: JsonObject
    output_schema: JsonObject
    default_routing_policy_id: ContractId
    default_routing_policy_version: Version
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_task_types(self) -> Self:
        require_unique(self.task_types, field_name="task_types")
        return self


class RoutingPolicy(ContractModel):
    """Versioned routing envelope selected before execution."""

    routing_policy_id: ContractId
    routing_policy_version: Version
    strategy: ContractId
    collaboration_mode: ContractId
    worker_types: tuple[ContractId, ...] = ()
    max_parallel_tasks: int = Field(ge=1)
    max_attempts_per_task: int = Field(ge=1)
    task_timeout_seconds: int = Field(ge=1)
    provider_allowlist: tuple[ContractId, ...] = ()
    model_allowlist: tuple[ContractId, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ordered_sets(self) -> Self:
        require_unique(self.worker_types, field_name="worker_types")
        require_unique(self.provider_allowlist, field_name="provider_allowlist")
        require_unique(self.model_allowlist, field_name="model_allowlist")
        return self
