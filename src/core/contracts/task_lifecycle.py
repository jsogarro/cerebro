"""Canonical immutable Task and Attempt lifecycle records."""

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractId, ContractModel, IdempotencyKey, JsonObject
from .lifecycle import (
    _require_reason,
    _validate_cancellation_provenance,
    _validate_lifecycle_timestamps,
    _validate_transition_time,
)
from .states import (
    ATTEMPT_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_ATTEMPT_STATUSES,
    TERMINAL_TASK_STATUSES,
    AttemptStatus,
    InvalidTransitionError,
    TaskStatus,
    ensure_legal_transition,
    ensure_status_type,
)


class Task(ContractModel):
    """A logical unit of work within a run; retries create new attempts."""

    task_id: ContractId
    run_id: ContractId
    task_key: ContractId
    task_type: ContractId
    objective: str = Field(min_length=1)
    idempotency_key: IdempotencyKey
    dependency_ids: tuple[ContractId, ...] = ()
    assigned_worker_type: ContractId | None = None
    input: JsonObject = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    status_reason: str | None = Field(default=None, min_length=1)
    cancellation_requested_at: AwareDatetime | None = None
    cancellation_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.task_id in self.dependency_ids:
            raise ValueError("A task cannot depend on itself")
        if len(self.dependency_ids) != len(set(self.dependency_ids)):
            raise ValueError("dependency_ids entries must be unique")
        _validate_lifecycle_timestamps(
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            is_terminal=self.status in TERMINAL_TASK_STATUSES,
        )
        if self.status in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLING,
        }:
            _require_reason(self.status_reason)
        if (
            self.status
            in {TaskStatus.RUNNING, TaskStatus.CANCELLING, TaskStatus.SUCCEEDED}
            and self.started_at is None
        ):
            raise ValueError(f"Task status {self.status.value!r} requires started_at")
        if (
            self.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.SKIPPED}
            and self.started_at is not None
        ):
            raise ValueError(f"Task status {self.status.value!r} forbids started_at")
        _validate_cancellation_provenance(
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancellation_requested_at=self.cancellation_requested_at,
            cancellation_reason=self.cancellation_reason,
            required=self.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED},
            allowed=self.status
            in {TaskStatus.CANCELLING, TaskStatus.CANCELLED, TaskStatus.FAILED},
            failed_cleanup=self.status is TaskStatus.FAILED,
        )
        return self

    def transition_to(
        self,
        target: TaskStatus,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> Self:
        ensure_legal_transition(
            entity_name="Task",
            current=self.status,
            target=target,
            status_type=TaskStatus,
            legal_transitions=TASK_TRANSITIONS,
            terminal_statuses=TERMINAL_TASK_STATUSES,
        )
        _validate_transition_time(at, updated_at=self.updated_at)
        updates: dict[str, object] = {"status": target, "updated_at": at}
        if target is TaskStatus.RUNNING and self.started_at is None:
            updates["started_at"] = at
        if target in TERMINAL_TASK_STATUSES:
            updates["completed_at"] = at
        if target in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLING,
        }:
            updates["status_reason"] = _require_reason(reason)
        if (
            target in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}
            and self.cancellation_requested_at is None
        ):
            updates["cancellation_requested_at"] = at
            updates["cancellation_reason"] = updates["status_reason"]
        return type(self).model_validate(self.model_dump() | updates)

    def request_cancellation(self, *, at: datetime, reason: str) -> Self:
        ensure_status_type(
            entity_name="Task",
            role="current",
            status=self.status,
            status_type=TaskStatus,
        )
        validated = type(self).model_validate(dict(self.__dict__))
        if validated.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}:
            return validated
        if validated.status in TERMINAL_TASK_STATUSES:
            raise InvalidTransitionError(
                f"Task status {validated.status.value!r} is terminal and immutable"
            )
        target = (
            TaskStatus.CANCELLING
            if validated.status is TaskStatus.RUNNING
            else TaskStatus.CANCELLED
        )
        return validated.transition_to(target, at=at, reason=reason)


class Attempt(ContractModel):
    """One immutable-identity execution attempt for a task."""

    attempt_id: ContractId
    task_id: ContractId
    ordinal: int = Field(ge=1)
    idempotency_key: IdempotencyKey
    executor_id: ContractId | None = None
    status: AttemptStatus = AttemptStatus.CREATED
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
            is_terminal=self.status in TERMINAL_ATTEMPT_STATUSES,
        )
        if self.status in {
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
            AttemptStatus.TIMED_OUT,
            AttemptStatus.CANCELLING,
        }:
            _require_reason(self.status_reason)
        if (
            self.status
            in {
                AttemptStatus.RUNNING,
                AttemptStatus.CANCELLING,
                AttemptStatus.SUCCEEDED,
                AttemptStatus.TIMED_OUT,
            }
            and self.started_at is None
        ):
            raise ValueError(
                f"Attempt status {self.status.value!r} requires started_at"
            )
        if self.status is AttemptStatus.CREATED and self.started_at is not None:
            raise ValueError("Attempt status 'created' forbids started_at")
        _validate_cancellation_provenance(
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancellation_requested_at=self.cancellation_requested_at,
            cancellation_reason=self.cancellation_reason,
            required=self.status in {AttemptStatus.CANCELLING, AttemptStatus.CANCELLED},
            allowed=self.status
            in {
                AttemptStatus.CANCELLING,
                AttemptStatus.CANCELLED,
                AttemptStatus.FAILED,
            },
            failed_cleanup=self.status is AttemptStatus.FAILED,
        )
        return self

    def transition_to(
        self,
        target: AttemptStatus,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> Self:
        ensure_legal_transition(
            entity_name="Attempt",
            current=self.status,
            target=target,
            status_type=AttemptStatus,
            legal_transitions=ATTEMPT_TRANSITIONS,
            terminal_statuses=TERMINAL_ATTEMPT_STATUSES,
        )
        _validate_transition_time(at, updated_at=self.updated_at)
        updates: dict[str, object] = {"status": target, "updated_at": at}
        if target is AttemptStatus.RUNNING and self.started_at is None:
            updates["started_at"] = at
        if target in TERMINAL_ATTEMPT_STATUSES:
            updates["completed_at"] = at
        if target in {
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
            AttemptStatus.TIMED_OUT,
            AttemptStatus.CANCELLING,
        }:
            updates["status_reason"] = _require_reason(reason)
        if (
            target in {AttemptStatus.CANCELLING, AttemptStatus.CANCELLED}
            and self.cancellation_requested_at is None
        ):
            updates["cancellation_requested_at"] = at
            updates["cancellation_reason"] = updates["status_reason"]
        return type(self).model_validate(self.model_dump() | updates)

    def request_cancellation(self, *, at: datetime, reason: str) -> Self:
        ensure_status_type(
            entity_name="Attempt",
            role="current",
            status=self.status,
            status_type=AttemptStatus,
        )
        validated = type(self).model_validate(dict(self.__dict__))
        if validated.status in {
            AttemptStatus.CANCELLING,
            AttemptStatus.CANCELLED,
        }:
            return validated
        if validated.status in TERMINAL_ATTEMPT_STATUSES:
            raise InvalidTransitionError(
                f"Attempt status {validated.status.value!r} is terminal and immutable"
            )
        target = (
            AttemptStatus.CANCELLING
            if validated.status is AttemptStatus.RUNNING
            else AttemptStatus.CANCELLED
        )
        return validated.transition_to(target, at=at, reason=reason)
