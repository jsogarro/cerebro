"""Lifecycle states and legal transitions for durable execution records."""

from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar


class RunStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AttemptStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        {RunStatus.QUEUED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RUNNING: frozenset(
        {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLING}
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}
TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.READY, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}
    ),
    TaskStatus.READY: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLING}
    ),
    TaskStatus.CANCELLING: frozenset({TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}
ATTEMPT_TRANSITIONS: Mapping[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.CREATED: frozenset(
        {AttemptStatus.RUNNING, AttemptStatus.FAILED, AttemptStatus.CANCELLED}
    ),
    AttemptStatus.RUNNING: frozenset(
        {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLING,
            AttemptStatus.TIMED_OUT,
        }
    ),
    AttemptStatus.CANCELLING: frozenset(
        {AttemptStatus.CANCELLED, AttemptStatus.FAILED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.CANCELLED: frozenset(),
    AttemptStatus.TIMED_OUT: frozenset(),
}

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    }
)
TERMINAL_ATTEMPT_STATUSES = frozenset(
    {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
        AttemptStatus.TIMED_OUT,
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition would violate the state machine."""


StateT = TypeVar("StateT", bound=StrEnum)


def ensure_status_type(
    *,
    entity_name: str,
    role: str,
    status: StateT,
    status_type: type[StateT],
) -> None:
    """Reject values that bypassed enum validation on an immutable snapshot."""

    if type(status) is not status_type:
        raise TypeError(
            f"{entity_name} {role} status type must be {status_type.__name__}; "
            f"got {type(status).__name__}"
        )


def ensure_legal_transition(
    *,
    entity_name: str,
    current: StateT,
    target: StateT,
    status_type: type[StateT],
    legal_transitions: Mapping[StateT, frozenset[StateT]],
    terminal_statuses: frozenset[StateT],
) -> None:
    """Validate a transition without changing the immutable contract."""

    ensure_status_type(
        entity_name=entity_name,
        role="current",
        status=current,
        status_type=status_type,
    )
    ensure_status_type(
        entity_name=entity_name,
        role="target",
        status=target,
        status_type=status_type,
    )
    if current in terminal_statuses:
        raise InvalidTransitionError(
            f"{entity_name} status {current.value!r} is terminal and immutable"
        )
    if target not in legal_transitions[current]:
        raise InvalidTransitionError(
            f"Illegal {entity_name} transition from {current.value!r} "
            f"to {target.value!r}"
        )
