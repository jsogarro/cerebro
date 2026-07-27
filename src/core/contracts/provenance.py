"""Tool, artifact, evidence, and claim-provenance records."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import (
    ContentSha256,
    ContractId,
    ContractModel,
    IdempotencyKey,
    JsonObject,
    Version,
    require_unique,
)


class TrustClassification(StrEnum):
    TRUSTED_CONTROL = "trusted_control"
    APPLICATION = "application"
    USER_SUPPLIED = "user_supplied"
    EXTERNAL_UNTRUSTED = "external_untrusted"
    DERIVED_UNTRUSTED = "derived_untrusted"


class ToolInvocationStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    DENIED = "denied"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"
    INVALIDATED = "invalidated"


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class ToolInvocation(ContractModel):
    """An auditable request to one versioned tool capability."""

    tool_invocation_id: ContractId
    run_id: ContractId
    task_id: ContractId
    attempt_id: ContractId
    tool_name: ContractId
    tool_version: Version
    status: ToolInvocationStatus
    capability_scope: ContractId
    idempotency_key: IdempotencyKey
    input: JsonObject
    input_trust: TrustClassification
    output: JsonObject | None = None
    output_trust: TrustClassification | None = None
    error_code: ContractId | None = None
    status_reason: str | None = Field(default=None, min_length=1)
    requested_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        terminal = self.status in {
            ToolInvocationStatus.SUCCEEDED,
            ToolInvocationStatus.FAILED,
            ToolInvocationStatus.CANCELLED,
            ToolInvocationStatus.TIMED_OUT,
            ToolInvocationStatus.DENIED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("Only terminal tool invocations require completed_at")
        if self.completed_at is not None and self.completed_at < self.requested_at:
            raise ValueError("completed_at cannot precede requested_at")
        if self.status is ToolInvocationStatus.SUCCEEDED and (
            self.output is None or self.output_trust is None
        ):
            raise ValueError("Successful tool invocations require output provenance")
        if (
            self.status
            in {
                ToolInvocationStatus.FAILED,
                ToolInvocationStatus.TIMED_OUT,
                ToolInvocationStatus.DENIED,
            }
            and self.error_code is None
        ):
            raise ValueError("Failed tool invocations require error_code")
        return self


class Artifact(ContractModel):
    """Immutable content metadata stored outside transient agent context."""

    artifact_id: ContractId
    run_id: ContractId
    task_id: ContractId | None = None
    attempt_id: ContractId | None = None
    kind: ContractId
    media_type: ContractId
    storage_uri: str = Field(min_length=1)
    content_sha256: ContentSha256
    status: ArtifactStatus
    trust: TrustClassification
    producer: ContractId
    created_at: AwareDatetime
    metadata: JsonObject = Field(default_factory=dict)


class Evidence(ContractModel):
    """Stable locator into an immutable source snapshot."""

    evidence_id: ContractId
    run_id: ContractId
    task_id: ContractId
    source_type: ContractId
    source_uri: str = Field(min_length=1)
    snapshot_artifact_id: ContractId
    content_sha256: ContentSha256
    locator: str = Field(min_length=1)
    trust: TrustClassification
    producer_tool_invocation_id: ContractId | None = None
    parent_evidence_ids: tuple[ContractId, ...] = ()
    acquired_at: AwareDatetime

    @model_validator(mode="after")
    def validate_provenance_chain(self) -> Self:
        if self.evidence_id in self.parent_evidence_ids:
            raise ValueError("Evidence cannot derive from itself")
        require_unique(self.parent_evidence_ids, field_name="parent_evidence_ids")
        return self


class ClaimSupport(ContractModel):
    """A versioned evaluator judgment connecting one claim to evidence."""

    claim_support_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    claim_id: ContractId
    claim_text: str = Field(min_length=1)
    status: ClaimSupportStatus
    evidence_ids: tuple[ContractId, ...] = ()
    evaluator_id: ContractId
    evaluator_version: Version
    explanation: str = Field(min_length=1)
    evaluated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_evidence_links(self) -> Self:
        require_unique(self.evidence_ids, field_name="evidence_ids")
        if (
            self.status
            in {ClaimSupportStatus.SUPPORTED, ClaimSupportStatus.CONTRADICTED}
            and not self.evidence_ids
        ):
            raise ValueError("Supported or contradicted claims require evidence")
        return self
