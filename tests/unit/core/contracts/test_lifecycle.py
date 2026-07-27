"""Executable lifecycle invariants for runs, tasks, and attempts."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    Attempt,
    AttemptStatus,
    InvalidTransitionError,
    Run,
    RunStatus,
    Task,
    TaskStatus,
)

CREATED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
TRANSITION_AT = CREATED_AT + timedelta(seconds=10)

RUN_LEGAL_TRANSITION_PAIRS = frozenset(
    {
        (RunStatus.CREATED, RunStatus.QUEUED),
        (RunStatus.CREATED, RunStatus.FAILED),
        (RunStatus.CREATED, RunStatus.CANCELLED),
        (RunStatus.QUEUED, RunStatus.RUNNING),
        (RunStatus.QUEUED, RunStatus.FAILED),
        (RunStatus.QUEUED, RunStatus.CANCELLED),
        (RunStatus.RUNNING, RunStatus.SUCCEEDED),
        (RunStatus.RUNNING, RunStatus.FAILED),
        (RunStatus.RUNNING, RunStatus.CANCELLING),
        (RunStatus.CANCELLING, RunStatus.CANCELLED),
        (RunStatus.CANCELLING, RunStatus.FAILED),
    }
)
TASK_LEGAL_TRANSITION_PAIRS = frozenset(
    {
        (TaskStatus.PENDING, TaskStatus.READY),
        (TaskStatus.PENDING, TaskStatus.FAILED),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.PENDING, TaskStatus.SKIPPED),
        (TaskStatus.READY, TaskStatus.RUNNING),
        (TaskStatus.READY, TaskStatus.FAILED),
        (TaskStatus.READY, TaskStatus.CANCELLED),
        (TaskStatus.READY, TaskStatus.SKIPPED),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLING),
        (TaskStatus.CANCELLING, TaskStatus.CANCELLED),
        (TaskStatus.CANCELLING, TaskStatus.FAILED),
    }
)
ATTEMPT_LEGAL_TRANSITION_PAIRS = frozenset(
    {
        (AttemptStatus.CREATED, AttemptStatus.RUNNING),
        (AttemptStatus.CREATED, AttemptStatus.FAILED),
        (AttemptStatus.CREATED, AttemptStatus.CANCELLED),
        (AttemptStatus.RUNNING, AttemptStatus.SUCCEEDED),
        (AttemptStatus.RUNNING, AttemptStatus.FAILED),
        (AttemptStatus.RUNNING, AttemptStatus.CANCELLING),
        (AttemptStatus.RUNNING, AttemptStatus.TIMED_OUT),
        (AttemptStatus.CANCELLING, AttemptStatus.CANCELLED),
        (AttemptStatus.CANCELLING, AttemptStatus.FAILED),
    }
)


def _run(**updates: Any) -> Run:
    values: dict[str, Any] = {
        "run_id": "run-001",
        "tenant_id": "tenant-001",
        "workflow_definition_id": "workflow.research",
        "workflow_definition_version": "1.0.0",
        "routing_policy_id": "routing.default",
        "routing_policy_version": "1.0.0",
        "idempotency_key": "request-001",
        "requested_by": "user-001",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    return Run(**(values | updates))


def _task(**updates: Any) -> Task:
    values: dict[str, Any] = {
        "task_id": "task-001",
        "run_id": "run-001",
        "task_key": "research",
        "task_type": "research",
        "objective": "Research the question.",
        "idempotency_key": "run-001:research",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    return Task(**(values | updates))


def _attempt(**updates: Any) -> Attempt:
    values: dict[str, Any] = {
        "attempt_id": "attempt-001",
        "task_id": "task-001",
        "ordinal": 1,
        "idempotency_key": "task-001:1",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    return Attempt(**(values | updates))


def _run_at_status(status: RunStatus) -> Run:
    updates: dict[str, Any] = {"status": status}
    if status is RunStatus.QUEUED:
        updates["updated_at"] = CREATED_AT + timedelta(seconds=1)
    elif status in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=2),
        }
    elif status in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=3),
            "completed_at": CREATED_AT + timedelta(seconds=3),
        }
    if status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
        updates |= {
            "status_reason": "Cancellation requested.",
            "cancellation_requested_at": CREATED_AT + timedelta(seconds=2),
            "cancellation_reason": "Cancellation requested.",
        }
    elif status is RunStatus.FAILED:
        updates["status_reason"] = "Run failed."
    return _run(**updates)


def _task_at_status(status: TaskStatus) -> Task:
    updates: dict[str, Any] = {"status": status}
    if status is TaskStatus.READY:
        updates["updated_at"] = CREATED_AT + timedelta(seconds=1)
    elif status in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=2),
        }
    elif status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=3),
            "completed_at": CREATED_AT + timedelta(seconds=3),
        }
    elif status is TaskStatus.SKIPPED:
        updates |= {
            "updated_at": CREATED_AT + timedelta(seconds=3),
            "completed_at": CREATED_AT + timedelta(seconds=3),
        }
    if status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}:
        updates |= {
            "status_reason": "Cancellation requested.",
            "cancellation_requested_at": CREATED_AT + timedelta(seconds=2),
            "cancellation_reason": "Cancellation requested.",
        }
    elif status in {TaskStatus.FAILED, TaskStatus.SKIPPED}:
        updates["status_reason"] = f"Task {status.value}."
    return _task(**updates)


def _attempt_at_status(status: AttemptStatus) -> Attempt:
    updates: dict[str, Any] = {"status": status}
    if status in {AttemptStatus.RUNNING, AttemptStatus.CANCELLING}:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=2),
        }
    elif status in {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
        AttemptStatus.TIMED_OUT,
    }:
        updates |= {
            "started_at": CREATED_AT + timedelta(seconds=1),
            "updated_at": CREATED_AT + timedelta(seconds=3),
            "completed_at": CREATED_AT + timedelta(seconds=3),
        }
    if status in {AttemptStatus.CANCELLING, AttemptStatus.CANCELLED}:
        updates |= {
            "status_reason": "Cancellation requested.",
            "cancellation_requested_at": CREATED_AT + timedelta(seconds=2),
            "cancellation_reason": "Cancellation requested.",
        }
    elif status in {AttemptStatus.FAILED, AttemptStatus.TIMED_OUT}:
        updates["status_reason"] = f"Attempt {status.value}."
    return _attempt(**updates)


def test_run_follows_the_legal_success_path_without_mutating_prior_states() -> None:
    queued_at = CREATED_AT + timedelta(seconds=1)
    started_at = CREATED_AT + timedelta(seconds=2)
    completed_at = CREATED_AT + timedelta(seconds=3)
    created = _run()

    queued = created.transition_to(RunStatus.QUEUED, at=queued_at)
    running = queued.transition_to(RunStatus.RUNNING, at=started_at)
    succeeded = running.transition_to(RunStatus.SUCCEEDED, at=completed_at)

    assert created.status is RunStatus.CREATED
    assert queued.status is RunStatus.QUEUED
    assert running.started_at == started_at
    assert succeeded.status is RunStatus.SUCCEEDED
    assert succeeded.completed_at == completed_at


@pytest.mark.parametrize(
    ("entity", "target"),
    [
        (_run(), RunStatus.SUCCEEDED),
        (_task(), TaskStatus.SUCCEEDED),
        (_attempt(), AttemptStatus.SUCCEEDED),
    ],
)
def test_lifecycle_contracts_reject_illegal_state_transitions(
    entity: Run | Task | Attempt,
    target: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    with pytest.raises(InvalidTransitionError, match="Illegal"):
        entity.transition_to(target, at=CREATED_AT + timedelta(seconds=1))  # type: ignore[arg-type]


def test_terminal_states_reject_transitions_and_direct_mutation() -> None:
    running = (
        _run()
        .transition_to(RunStatus.QUEUED, at=CREATED_AT + timedelta(seconds=1))
        .transition_to(RunStatus.RUNNING, at=CREATED_AT + timedelta(seconds=2))
    )
    succeeded = running.transition_to(
        RunStatus.SUCCEEDED, at=CREATED_AT + timedelta(seconds=3)
    )

    with pytest.raises(InvalidTransitionError, match="terminal"):
        succeeded.transition_to(
            RunStatus.FAILED,
            at=CREATED_AT + timedelta(seconds=4),
            reason="late failure",
        )

    with pytest.raises(ValidationError, match="frozen"):
        succeeded.status = RunStatus.FAILED


def test_cancellation_is_immediate_before_execution_and_idempotent() -> None:
    queued = _run().transition_to(
        RunStatus.QUEUED, at=CREATED_AT + timedelta(seconds=1)
    )
    cancelled_at = CREATED_AT + timedelta(seconds=2)

    cancelled = queued.request_cancellation(
        at=cancelled_at,
        reason="User cancelled before dispatch.",
    )

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.completed_at == cancelled_at
    assert cancelled.status_reason == "User cancelled before dispatch."
    assert (
        cancelled.request_cancellation(
            at=CREATED_AT + timedelta(seconds=3),
            reason="Duplicate delivery.",
        )
        == cancelled
    )


def test_active_cancellation_requires_acknowledgement_before_terminal_state() -> None:
    running = (
        _run()
        .transition_to(RunStatus.QUEUED, at=CREATED_AT + timedelta(seconds=1))
        .transition_to(RunStatus.RUNNING, at=CREATED_AT + timedelta(seconds=2))
    )
    cancelling_at = CREATED_AT + timedelta(seconds=3)
    cancelled_at = CREATED_AT + timedelta(seconds=4)

    cancelling = running.request_cancellation(
        at=cancelling_at,
        reason="User requested cancellation.",
    )
    cancelled = cancelling.transition_to(
        RunStatus.CANCELLED,
        at=cancelled_at,
        reason=cancelling.status_reason,
    )

    assert cancelling.status is RunStatus.CANCELLING
    assert cancelling.completed_at is None
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.completed_at == cancelled_at


@pytest.mark.parametrize("entity", [_task(), _attempt()])
def test_unstarted_tasks_and_attempts_cancel_without_running(
    entity: Task | Attempt,
) -> None:
    cancelled = entity.request_cancellation(
        at=CREATED_AT + timedelta(seconds=1),
        reason="Parent run cancelled.",
    )

    assert cancelled.status.value == "cancelled"
    assert cancelled.completed_at == CREATED_AT + timedelta(seconds=1)


def test_cancellation_cannot_rewrite_a_completed_outcome() -> None:
    completed = (
        _task()
        .transition_to(TaskStatus.READY, at=CREATED_AT + timedelta(seconds=1))
        .transition_to(TaskStatus.RUNNING, at=CREATED_AT + timedelta(seconds=2))
        .transition_to(TaskStatus.SUCCEEDED, at=CREATED_AT + timedelta(seconds=3))
    )

    with pytest.raises(InvalidTransitionError, match="terminal"):
        completed.request_cancellation(
            at=CREATED_AT + timedelta(seconds=4),
            reason="Too late.",
        )


def test_failure_transitions_require_a_reason() -> None:
    running = _attempt().transition_to(
        AttemptStatus.RUNNING, at=CREATED_AT + timedelta(seconds=1)
    )

    with pytest.raises(ValueError, match="reason"):
        running.transition_to(
            AttemptStatus.FAILED,
            at=CREATED_AT + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    "entity_factory",
    [
        lambda: _run(status=RunStatus.RUNNING),
        lambda: _task(status=TaskStatus.RUNNING),
        lambda: _attempt(status=AttemptStatus.RUNNING),
    ],
)
def test_running_records_require_a_started_timestamp(
    entity_factory: Any,
) -> None:
    with pytest.raises(ValidationError, match="started_at"):
        entity_factory()


def _transition_cases() -> list[
    tuple[
        str,
        type[RunStatus] | type[TaskStatus] | type[AttemptStatus],
        RunStatus | TaskStatus | AttemptStatus,
        RunStatus | TaskStatus | AttemptStatus,
        bool,
    ]
]:
    cases: list[
        tuple[
            str,
            type[RunStatus] | type[TaskStatus] | type[AttemptStatus],
            RunStatus | TaskStatus | AttemptStatus,
            RunStatus | TaskStatus | AttemptStatus,
            bool,
        ]
    ] = []
    configurations = (
        ("run", RunStatus, RUN_LEGAL_TRANSITION_PAIRS),
        ("task", TaskStatus, TASK_LEGAL_TRANSITION_PAIRS),
        ("attempt", AttemptStatus, ATTEMPT_LEGAL_TRANSITION_PAIRS),
    )
    for entity_name, status_type, legal_pairs in configurations:
        for current in status_type:
            for target in status_type:
                cases.append(
                    (
                        f"{entity_name}-{current.value}-to-{target.value}",
                        status_type,
                        current,
                        target,
                        (current, target) in legal_pairs,
                    )
                )
    return cases


@pytest.mark.parametrize(
    ("case_id", "status_type", "current", "target", "is_legal"),
    _transition_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_every_lifecycle_state_pair_enforces_the_declared_transition_table(
    case_id: str,
    status_type: type[RunStatus] | type[TaskStatus] | type[AttemptStatus],
    current: RunStatus | TaskStatus | AttemptStatus,
    target: RunStatus | TaskStatus | AttemptStatus,
    is_legal: bool,
) -> None:
    del case_id
    factories = {
        RunStatus: _run_at_status,
        TaskStatus: _task_at_status,
        AttemptStatus: _attempt_at_status,
    }
    entity = factories[status_type](current)  # type: ignore[arg-type]
    if not is_legal:
        with pytest.raises(InvalidTransitionError):
            entity.transition_to(  # type: ignore[arg-type]
                target,
                at=TRANSITION_AT,
                reason="Transition reason.",
            )
        return

    transitioned = entity.transition_to(  # type: ignore[arg-type]
        target,
        at=TRANSITION_AT,
        reason="Transition reason.",
    )

    assert transitioned.status is target
    assert type(entity).model_validate(transitioned.model_dump()) == transitioned


class _ForeignStatus(StrEnum):
    CANCELLING = "cancelling"
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"


@pytest.mark.parametrize(
    ("entity", "raw_target", "foreign_target"),
    [
        (_run(), "queued", _ForeignStatus.QUEUED),
        (_task(), "ready", _ForeignStatus.READY),
        (_attempt(), "running", _ForeignStatus.RUNNING),
    ],
)
def test_transition_methods_strictly_reject_raw_and_foreign_enum_targets(
    entity: Run | Task | Attempt,
    raw_target: str,
    foreign_target: _ForeignStatus,
) -> None:
    for invalid_target in (raw_target, foreign_target):
        with pytest.raises(TypeError, match="status type"):
            entity.transition_to(  # type: ignore[arg-type]
                invalid_target,
                at=TRANSITION_AT,
            )


@pytest.mark.parametrize(
    ("entity", "raw_current", "target"),
    [
        (_run(), "created", RunStatus.QUEUED),
        (_task(), "pending", TaskStatus.READY),
        (_attempt(), "created", AttemptStatus.RUNNING),
    ],
)
def test_transition_methods_reject_snapshots_with_bypassed_status_validation(
    entity: Run | Task | Attempt,
    raw_current: str,
    target: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    invalid_snapshot = entity.model_copy(update={"status": raw_current})

    with pytest.raises(TypeError, match="status type"):
        invalid_snapshot.transition_to(  # type: ignore[arg-type]
            target,
            at=TRANSITION_AT,
        )


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.RUNNING),
        _task_at_status(TaskStatus.RUNNING),
        _attempt_at_status(AttemptStatus.RUNNING),
    ],
)
def test_cancellation_requests_reject_bypassed_duplicate_status_values(
    entity: Run | Task | Attempt,
) -> None:
    for invalid_status in ("cancelling", _ForeignStatus.CANCELLING):
        invalid_snapshot = entity.model_copy(update={"status": invalid_status})

        with pytest.raises(TypeError, match="status type"):
            invalid_snapshot.request_cancellation(
                at=TRANSITION_AT,
                reason="Must not return an invalid snapshot.",
            )


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.CANCELLING),
        _run_at_status(RunStatus.CANCELLED),
        _task_at_status(TaskStatus.CANCELLING),
        _task_at_status(TaskStatus.CANCELLED),
        _attempt_at_status(AttemptStatus.CANCELLING),
        _attempt_at_status(AttemptStatus.CANCELLED),
    ],
)
def test_duplicate_cancellation_requests_revalidate_the_existing_snapshot(
    entity: Run | Task | Attempt,
) -> None:
    invalid_snapshot = entity.model_copy(update={"idempotency_key": ""})

    with pytest.raises(ValidationError, match="at least 1 character"):
        invalid_snapshot.request_cancellation(
            at=TRANSITION_AT,
            reason="Duplicate delivery.",
        )


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.CANCELLING),
        _task_at_status(TaskStatus.CANCELLING),
        _attempt_at_status(AttemptStatus.CANCELLING),
    ],
)
def test_duplicate_cancellation_returns_the_canonical_validated_snapshot(
    entity: Run | Task | Attempt,
) -> None:
    assert entity.cancellation_requested_at is not None
    invalid_snapshot = entity.model_copy(
        update={
            "cancellation_requested_at": entity.cancellation_requested_at.isoformat()
        }
    )

    canonical = invalid_snapshot.request_cancellation(
        at=TRANSITION_AT,
        reason="Duplicate delivery.",
    )

    assert canonical is not invalid_snapshot
    assert type(canonical.cancellation_requested_at) is datetime
    assert type(entity).model_validate(canonical.model_dump()) == canonical


@pytest.mark.parametrize(
    ("entity", "target"),
    [
        (_run(), RunStatus.QUEUED),
        (_task(), TaskStatus.READY),
        (_attempt(), AttemptStatus.RUNNING),
    ],
)
def test_transition_methods_fully_revalidate_returned_snapshots(
    entity: Run | Task | Attempt,
    target: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    invalid_snapshot = entity.model_copy(update={"idempotency_key": ""})

    with pytest.raises(ValidationError, match="at least 1 character"):
        invalid_snapshot.transition_to(  # type: ignore[arg-type]
            target,
            at=TRANSITION_AT,
        )


@pytest.mark.parametrize(
    ("entity", "cancelled_status"),
    [
        (_run(), RunStatus.CANCELLED),
        (_task(), TaskStatus.CANCELLED),
        (_attempt(), AttemptStatus.CANCELLED),
    ],
)
def test_immediate_cancellation_preserves_first_request_provenance(
    entity: Run | Task | Attempt,
    cancelled_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    requested_at = CREATED_AT + timedelta(seconds=1)
    cancelled = entity.request_cancellation(
        at=requested_at,
        reason="  First cancellation reason.  ",
    )

    assert cancelled.status is cancelled_status
    assert cancelled.cancellation_requested_at == requested_at
    assert cancelled.cancellation_reason == "First cancellation reason."
    assert (
        cancelled.request_cancellation(
            at=CREATED_AT + timedelta(seconds=2),
            reason="Duplicate after acknowledgement.",
        )
        == cancelled
    )
    assert cancelled.cancellation_requested_at == requested_at
    assert cancelled.cancellation_reason == "First cancellation reason."


@pytest.mark.parametrize(
    ("running_status", "cancelling_status", "cancelled_status"),
    [
        (
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
            RunStatus.CANCELLED,
        ),
        (
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
            TaskStatus.CANCELLED,
        ),
        (
            AttemptStatus.RUNNING,
            AttemptStatus.CANCELLING,
            AttemptStatus.CANCELLED,
        ),
    ],
)
def test_active_cancellation_preserves_first_provenance_through_duplicates_and_ack(
    running_status: RunStatus | TaskStatus | AttemptStatus,
    cancelling_status: RunStatus | TaskStatus | AttemptStatus,
    cancelled_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    factories = {
        RunStatus: _run_at_status,
        TaskStatus: _task_at_status,
        AttemptStatus: _attempt_at_status,
    }
    entity = factories[type(running_status)](running_status)  # type: ignore[arg-type]
    requested_at = CREATED_AT + timedelta(seconds=3)
    cancelling = entity.request_cancellation(
        at=requested_at,
        reason="First cancellation reason.",
    )

    assert cancelling.status is cancelling_status
    assert cancelling.cancellation_requested_at == requested_at
    assert cancelling.cancellation_reason == "First cancellation reason."
    assert (
        cancelling.request_cancellation(
            at=CREATED_AT + timedelta(seconds=4),
            reason="Duplicate before acknowledgement.",
        )
        == cancelling
    )

    cancelled = cancelling.transition_to(  # type: ignore[arg-type]
        cancelled_status,
        at=CREATED_AT + timedelta(seconds=5),
        reason="Executor acknowledged cancellation.",
    )

    assert cancelled.status is cancelled_status
    assert cancelled.status_reason == "Executor acknowledged cancellation."
    assert cancelled.cancellation_requested_at == requested_at
    assert cancelled.cancellation_reason == "First cancellation reason."
    assert (
        cancelled.request_cancellation(
            at=CREATED_AT + timedelta(seconds=6),
            reason="Duplicate after acknowledgement.",
        )
        == cancelled
    )


@pytest.mark.parametrize(
    ("running_status", "failed_status"),
    [
        (RunStatus.RUNNING, RunStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (AttemptStatus.RUNNING, AttemptStatus.FAILED),
    ],
)
def test_cancellation_cleanup_failure_preserves_first_request_provenance(
    running_status: RunStatus | TaskStatus | AttemptStatus,
    failed_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    factories = {
        RunStatus: _run_at_status,
        TaskStatus: _task_at_status,
        AttemptStatus: _attempt_at_status,
    }
    requested_at = CREATED_AT + timedelta(seconds=3)
    cancelling = factories[type(running_status)](  # type: ignore[arg-type]
        running_status
    ).request_cancellation(
        at=requested_at,
        reason="First cancellation reason.",
    )

    failed = cancelling.transition_to(  # type: ignore[arg-type]
        failed_status,
        at=CREATED_AT + timedelta(seconds=4),
        reason="Executor cleanup failed.",
    )

    assert failed.status is failed_status
    assert failed.status_reason == "Executor cleanup failed."
    assert failed.cancellation_requested_at == requested_at
    assert failed.cancellation_reason == "First cancellation reason."


@pytest.mark.parametrize(
    ("running_status", "cancelling_status"),
    [
        (RunStatus.RUNNING, RunStatus.CANCELLING),
        (TaskStatus.RUNNING, TaskStatus.CANCELLING),
        (AttemptStatus.RUNNING, AttemptStatus.CANCELLING),
    ],
)
def test_cancelling_snapshots_require_complete_cancellation_provenance(
    running_status: RunStatus | TaskStatus | AttemptStatus,
    cancelling_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    factories = {
        RunStatus: _run_at_status,
        TaskStatus: _task_at_status,
        AttemptStatus: _attempt_at_status,
    }
    entity = factories[type(running_status)](running_status)  # type: ignore[arg-type]
    payload = entity.model_dump()
    payload |= {
        "status": cancelling_status,
        "updated_at": CREATED_AT + timedelta(seconds=3),
        "status_reason": "Cancellation requested.",
    }

    with pytest.raises(ValidationError, match="cancellation provenance"):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [_run(), _task(), _attempt()],
)
def test_partial_or_out_of_order_cancellation_provenance_is_rejected(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["cancellation_requested_at"] = CREATED_AT

    with pytest.raises(ValidationError, match="provided together"):
        type(entity).model_validate(payload)

    payload["cancellation_reason"] = "Cancellation requested."
    payload["cancellation_requested_at"] = CREATED_AT - timedelta(seconds=1)

    with pytest.raises(ValidationError, match="cannot precede created_at"):
        type(entity).model_validate(payload)

    payload["cancellation_requested_at"] = CREATED_AT + timedelta(seconds=1)

    with pytest.raises(ValidationError, match="cannot follow updated_at"):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.CANCELLED),
        _task_at_status(TaskStatus.CANCELLED),
        _attempt_at_status(AttemptStatus.CANCELLED),
    ],
)
def test_terminal_cancellation_request_cannot_follow_completion(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["updated_at"] = CREATED_AT + timedelta(seconds=5)
    payload["cancellation_requested_at"] = CREATED_AT + timedelta(seconds=4)

    with pytest.raises(ValidationError, match="cannot follow completed_at"):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.RUNNING),
        _task_at_status(TaskStatus.RUNNING),
        _attempt_at_status(AttemptStatus.RUNNING),
    ],
)
def test_updated_at_cannot_precede_started_at(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["updated_at"] = CREATED_AT

    with pytest.raises(ValidationError, match="updated_at cannot precede started_at"):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.RUNNING),
        _task_at_status(TaskStatus.RUNNING),
        _attempt_at_status(AttemptStatus.RUNNING),
    ],
)
def test_updated_at_may_equal_started_at(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["updated_at"] = payload["started_at"]

    validated = type(entity).model_validate(payload)

    assert validated.updated_at == validated.started_at


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.SUCCEEDED),
        _task_at_status(TaskStatus.SUCCEEDED),
        _attempt_at_status(AttemptStatus.SUCCEEDED),
    ],
)
def test_updated_at_cannot_precede_completed_at(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["updated_at"] = payload["completed_at"] - timedelta(microseconds=1)

    with pytest.raises(ValidationError, match="updated_at cannot precede completed_at"):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.SUCCEEDED),
        _task_at_status(TaskStatus.SUCCEEDED),
        _attempt_at_status(AttemptStatus.SUCCEEDED),
    ],
)
def test_updated_at_may_equal_completed_at(
    entity: Run | Task | Attempt,
) -> None:
    validated = type(entity).model_validate(entity.model_dump())

    assert validated.updated_at == validated.completed_at


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.CANCELLING),
        _task_at_status(TaskStatus.CANCELLING),
        _attempt_at_status(AttemptStatus.CANCELLING),
    ],
)
def test_active_cancellation_cannot_precede_started_at(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["cancellation_requested_at"] = payload["started_at"] - timedelta(
        microseconds=1
    )

    with pytest.raises(
        ValidationError,
        match="cancellation_requested_at cannot precede started_at",
    ):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    "entity",
    [
        _run_at_status(RunStatus.CANCELLING),
        _task_at_status(TaskStatus.CANCELLING),
        _attempt_at_status(AttemptStatus.CANCELLING),
    ],
)
def test_active_cancellation_may_equal_started_at(
    entity: Run | Task | Attempt,
) -> None:
    payload = entity.model_dump()
    payload["cancellation_requested_at"] = payload["started_at"]

    validated = type(entity).model_validate(payload)

    assert validated.cancellation_requested_at == validated.started_at


@pytest.mark.parametrize(
    ("entity", "failed_status"),
    [
        (_run(), RunStatus.FAILED),
        (_task(), TaskStatus.FAILED),
        (_attempt(), AttemptStatus.FAILED),
    ],
)
def test_failed_cancellation_cleanup_requires_started_work(
    entity: Run | Task | Attempt,
    failed_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    payload = entity.model_dump()
    payload |= {
        "status": failed_status,
        "updated_at": CREATED_AT + timedelta(seconds=2),
        "completed_at": CREATED_AT + timedelta(seconds=2),
        "status_reason": "Cancellation cleanup failed.",
        "cancellation_requested_at": CREATED_AT + timedelta(seconds=1),
        "cancellation_reason": "Cancellation requested.",
    }

    with pytest.raises(
        ValidationError,
        match="Failed lifecycle cancellation provenance requires started_at",
    ):
        type(entity).model_validate(payload)


@pytest.mark.parametrize(
    ("entity", "failed_status"),
    [
        (_run(), RunStatus.FAILED),
        (_task(), TaskStatus.FAILED),
        (_attempt(), AttemptStatus.FAILED),
    ],
)
def test_failed_cancellation_cleanup_accepts_started_work(
    entity: Run | Task | Attempt,
    failed_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    started_at = CREATED_AT + timedelta(seconds=1)
    payload = entity.model_dump()
    payload |= {
        "status": failed_status,
        "started_at": started_at,
        "updated_at": CREATED_AT + timedelta(seconds=2),
        "completed_at": CREATED_AT + timedelta(seconds=2),
        "status_reason": "Cancellation cleanup failed.",
        "cancellation_requested_at": started_at,
        "cancellation_reason": "Cancellation requested.",
    }

    validated = type(entity).model_validate(payload)

    assert validated.started_at == validated.cancellation_requested_at


@pytest.mark.parametrize(
    ("entity", "failed_status"),
    [
        (_run(), RunStatus.FAILED),
        (_task(), TaskStatus.FAILED),
        (_attempt(), AttemptStatus.FAILED),
    ],
)
def test_failure_before_start_remains_valid_without_cancellation_provenance(
    entity: Run | Task | Attempt,
    failed_status: RunStatus | TaskStatus | AttemptStatus,
) -> None:
    completed_at = CREATED_AT + timedelta(seconds=1)
    payload = entity.model_dump()
    payload |= {
        "status": failed_status,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "status_reason": "Failed before work started.",
    }

    validated = type(entity).model_validate(payload)

    assert validated.started_at is None
    assert validated.cancellation_requested_at is None
