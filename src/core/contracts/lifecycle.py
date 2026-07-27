"""Canonical immutable Run lifecycle record and shared lifecycle validation."""

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractId, ContractModel, IdempotencyKey, Version
from .states import (
    RUN_TRANSITIONS,
    TERMINAL_RUN_STATUSES,
    InvalidTransitionError,
    RunStatus,
    ensure_legal_transition,
    ensure_status_type,
)


def _validate_transition_time(at: datetime, *, updated_at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Transition time must be timezone-aware")
    if at < updated_at:
        raise ValueError("Transition time cannot precede updated_at")


def _require_reason(reason: str | None) -> str:
    if reason is None or not reason.strip():
        raise ValueError("A non-empty reason is required for this transition")
    return reason.strip()


def _validate_lifecycle_timestamps(
    *,
    created_at: datetime,
    updated_at: datetime,
    started_at: datetime | None,
    completed_at: datetime | None,
    is_terminal: bool,
) -> None:
    if updated_at < created_at:
        raise ValueError("updated_at cannot precede created_at")
    if started_at is not None and started_at < created_at:
        raise ValueError("started_at cannot precede created_at")
    if started_at is not None and updated_at < started_at:
        raise ValueError("updated_at cannot precede started_at")
    if completed_at is not None and completed_at < (started_at or created_at):
        raise ValueError("completed_at cannot precede lifecycle start")
    if completed_at is not None and updated_at < completed_at:
        raise ValueError("updated_at cannot precede completed_at")
    if is_terminal and completed_at is None:
        raise ValueError("Terminal lifecycle states require completed_at")
    if not is_terminal and completed_at is not None:
        raise ValueError("Non-terminal lifecycle states cannot have completed_at")


def _validate_cancellation_provenance(
    *,
    created_at: datetime,
    updated_at: datetime,
    started_at: datetime | None,
    completed_at: datetime | None,
    cancellation_requested_at: datetime | None,
    cancellation_reason: str | None,
    required: bool,
    allowed: bool,
    failed_cleanup: bool,
) -> None:
    has_requested_at = cancellation_requested_at is not None
    has_reason = cancellation_reason is not None
    if has_requested_at != has_reason:
        raise ValueError(
            "cancellation_requested_at and cancellation_reason must be provided together"
        )
    if required and not has_requested_at:
        raise ValueError(
            "Cancelling and cancelled states require cancellation provenance"
        )
    if cancellation_requested_at is None:
        return
    _require_reason(cancellation_reason)
    if failed_cleanup and started_at is None:
        raise ValueError("Failed lifecycle cancellation provenance requires started_at")
    if cancellation_requested_at < created_at:
        raise ValueError("cancellation_requested_at cannot precede created_at")
    if started_at is not None and cancellation_requested_at < started_at:
        raise ValueError("cancellation_requested_at cannot precede started_at")
    if cancellation_requested_at > updated_at:
        raise ValueError("cancellation_requested_at cannot follow updated_at")
    if completed_at is not None and cancellation_requested_at > completed_at:
        raise ValueError("cancellation_requested_at cannot follow completed_at")
    if not allowed:
        raise ValueError("Current lifecycle status forbids cancellation provenance")


class Run(ContractModel):
    """A workflow execution pinned to workflow and policy versions."""

    run_id: ContractId
    tenant_id: ContractId
    workflow_definition_id: ContractId
    workflow_definition_version: Version
    routing_policy_id: ContractId
    routing_policy_version: Version
    idempotency_key: IdempotencyKey
    requested_by: ContractId
    status: RunStatus = RunStatus.CREATED
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    status_reason: str | None = Field(default=None, min_length=1)
    cancellation_requested_at: AwareDatetime | None = None
    cancellation_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        _validate_lifecycle_timestamps(
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            is_terminal=self.status in TERMINAL_RUN_STATUSES,
        )
        if self.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            _require_reason(self.status_reason)
        if self.status is RunStatus.CANCELLING:
            _require_reason(self.status_reason)
        if (
            self.status
            in {RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.SUCCEEDED}
            and self.started_at is None
        ):
            raise ValueError(f"Run status {self.status.value!r} requires started_at")
        if (
            self.status in {RunStatus.CREATED, RunStatus.QUEUED}
            and self.started_at is not None
        ):
            raise ValueError(f"Run status {self.status.value!r} forbids started_at")
        _validate_cancellation_provenance(
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancellation_requested_at=self.cancellation_requested_at,
            cancellation_reason=self.cancellation_reason,
            required=self.status in {RunStatus.CANCELLING, RunStatus.CANCELLED},
            allowed=self.status
            in {RunStatus.CANCELLING, RunStatus.CANCELLED, RunStatus.FAILED},
            failed_cleanup=self.status is RunStatus.FAILED,
        )
        return self

    def transition_to(
        self,
        target: RunStatus,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> Self:
        ensure_legal_transition(
            entity_name="Run",
            current=self.status,
            target=target,
            status_type=RunStatus,
            legal_transitions=RUN_TRANSITIONS,
            terminal_statuses=TERMINAL_RUN_STATUSES,
        )
        _validate_transition_time(at, updated_at=self.updated_at)
        updates: dict[str, object] = {"status": target, "updated_at": at}
        if target is RunStatus.RUNNING and self.started_at is None:
            updates["started_at"] = at
        if target in TERMINAL_RUN_STATUSES:
            updates["completed_at"] = at
        if target in {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.CANCELLING}:
            updates["status_reason"] = _require_reason(reason)
        if (
            target in {RunStatus.CANCELLING, RunStatus.CANCELLED}
            and self.cancellation_requested_at is None
        ):
            updates["cancellation_requested_at"] = at
            updates["cancellation_reason"] = updates["status_reason"]
        return type(self).model_validate(self.model_dump() | updates)

    def request_cancellation(self, *, at: datetime, reason: str) -> Self:
        ensure_status_type(
            entity_name="Run",
            role="current",
            status=self.status,
            status_type=RunStatus,
        )
        validated = type(self).model_validate(dict(self.__dict__))
        if validated.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            return validated
        if validated.status in TERMINAL_RUN_STATUSES:
            raise InvalidTransitionError(
                f"Run status {validated.status.value!r} is terminal and immutable"
            )
        target = (
            RunStatus.CANCELLING
            if validated.status is RunStatus.RUNNING
            else RunStatus.CANCELLED
        )
        return validated.transition_to(target, at=at, reason=reason)
