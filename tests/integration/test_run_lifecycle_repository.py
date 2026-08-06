"""Integration tests for ``RunLifecycleRepository`` against real Postgres.

Reuses the shared testcontainers fixtures from ``tests/integration/conftest.py``
(``test_engine``, ``db_session``) rather than standing up new infrastructure.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Attempt, AttemptStatus, Run, RunStatus, Task, TaskStatus
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _make_run(**overrides: object) -> Run:
    values: dict[str, object] = {
        "run_id": "run-1",
        "tenant_id": str(ORG_ID),
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "idempotency_key": "submit-1",
        "requested_by": "user-1",
        "status": RunStatus.CREATED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Run(**values)


def _make_task(**overrides: object) -> Task:
    values: dict[str, object] = {
        "task_id": "task-1",
        "run_id": "run-1",
        "task_key": "literature-review",
        "task_type": "research",
        "objective": "Survey the field",
        "idempotency_key": "task-key-1",
        "status": TaskStatus.PENDING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Task(**values)


def _make_attempt(**overrides: object) -> Attempt:
    values: dict[str, object] = {
        "attempt_id": "attempt-1",
        "task_id": "task-1",
        "ordinal": 1,
        "idempotency_key": "attempt-key-1",
        "status": AttemptStatus.CREATED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Attempt(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> RunLifecycleRepository:
    return RunLifecycleRepository(db_session)


# --- runs --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_run_persists_the_contract_fields(
    repo: RunLifecycleRepository,
) -> None:
    run = _make_run()

    row = await repo.create_run(run, organization_id=ORG_ID)

    assert row.run_id == "run-1"
    assert row.tenant_id == str(ORG_ID)
    assert row.organization_id == ORG_ID
    assert row.status == RunStatus.CREATED.value
    assert row.idempotency_key == "submit-1"


@pytest.mark.asyncio
async def test_create_run_fails_closed_without_an_org_context(
    repo: RunLifecycleRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.create_run(_make_run(), organization_id=None)


@pytest.mark.asyncio
async def test_create_run_rejects_a_mismatched_tenant(
    repo: RunLifecycleRepository,
) -> None:
    with pytest.raises(TenantMismatchError):
        await repo.create_run(_make_run(), organization_id=OTHER_ORG_ID)


@pytest.mark.asyncio
async def test_record_run_transition_updates_the_existing_row(
    repo: RunLifecycleRepository,
) -> None:
    run = _make_run()
    await repo.create_run(run, organization_id=ORG_ID)

    queued = run.transition_to(RunStatus.QUEUED, at=NOW)
    running = queued.transition_to(RunStatus.RUNNING, at=NOW)

    row = await repo.record_run_transition(running, organization_id=ORG_ID)

    assert row.status == RunStatus.RUNNING.value
    assert row.started_at == NOW


@pytest.mark.asyncio
async def test_record_run_transition_requires_an_existing_row(
    repo: RunLifecycleRepository,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        await repo.record_run_transition(_make_run(), organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_list_non_terminal_runs_excludes_terminal_statuses(
    repo: RunLifecycleRepository,
) -> None:
    active = _make_run(run_id="run-active", idempotency_key="submit-active")
    done = _make_run(
        run_id="run-done",
        idempotency_key="submit-done",
        status=RunStatus.FAILED,
        status_reason="boom",
        completed_at=NOW,
    )
    await repo.create_run(active, organization_id=ORG_ID)
    await repo.create_run(done, organization_id=ORG_ID)

    non_terminal = await repo.list_non_terminal_runs(organization_id=ORG_ID)

    run_ids = {row.run_id for row in non_terminal}
    assert run_ids == {"run-active"}


@pytest.mark.asyncio
async def test_list_non_terminal_runs_scans_every_tenant_when_unscoped(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(
        _make_run(
            run_id="run-org-a", idempotency_key="submit-a", tenant_id=str(ORG_ID)
        ),
        organization_id=ORG_ID,
    )
    await repo.create_run(
        _make_run(
            run_id="run-org-b", idempotency_key="submit-b", tenant_id=str(OTHER_ORG_ID)
        ),
        organization_id=OTHER_ORG_ID,
    )

    every_tenant = await repo.list_non_terminal_runs()
    scoped_to_a = await repo.list_non_terminal_runs(organization_id=ORG_ID)

    assert {row.run_id for row in every_tenant} == {"run-org-a", "run-org-b"}
    assert {row.run_id for row in scoped_to_a} == {"run-org-a"}


# --- tasks ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_copies_organization_id_from_the_parent_run(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)

    row = await repo.create_task(_make_task(), organization_id=ORG_ID)

    assert row.run_id == "run-1"
    assert row.organization_id == ORG_ID


@pytest.mark.asyncio
async def test_create_task_rejects_an_org_context_that_disagrees_with_the_run(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)

    with pytest.raises(TenantMismatchError):
        await repo.create_task(_make_task(), organization_id=OTHER_ORG_ID)


@pytest.mark.asyncio
async def test_create_task_requires_the_parent_run_to_exist(
    repo: RunLifecycleRepository,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        await repo.create_task(_make_task(), organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_record_task_transition_updates_the_existing_row(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    task = _make_task()
    await repo.create_task(task, organization_id=ORG_ID)

    ready = task.transition_to(TaskStatus.READY, at=NOW)
    running = ready.transition_to(TaskStatus.RUNNING, at=NOW)

    row = await repo.record_task_transition(running, organization_id=ORG_ID)

    assert row.status == TaskStatus.RUNNING.value
    assert row.started_at == NOW


@pytest.mark.asyncio
async def test_get_tasks_for_run_returns_every_task(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    await repo.create_task(_make_task(), organization_id=ORG_ID)
    await repo.create_task(
        _make_task(
            task_id="task-2", task_key="synthesis", idempotency_key="task-key-2"
        ),
        organization_id=ORG_ID,
    )

    tasks = await repo.get_tasks_for_run("run-1", organization_id=ORG_ID)

    assert {row.task_id for row in tasks} == {"task-1", "task-2"}


# --- attempts --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_attempt_copies_organization_id_from_the_parent_run(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    await repo.create_task(_make_task(), organization_id=ORG_ID)

    row = await repo.create_attempt(
        _make_attempt(), run_id="run-1", organization_id=ORG_ID
    )

    assert row.run_id == "run-1"
    assert row.task_id == "task-1"
    assert row.organization_id == ORG_ID


@pytest.mark.asyncio
async def test_create_attempt_rejects_an_org_context_that_disagrees_with_the_run(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    await repo.create_task(_make_task(), organization_id=ORG_ID)

    with pytest.raises(TenantMismatchError):
        await repo.create_attempt(
            _make_attempt(), run_id="run-1", organization_id=OTHER_ORG_ID
        )


@pytest.mark.asyncio
async def test_write_journaled_result_persists_the_nondeterministic_payload(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    await repo.create_task(_make_task(), organization_id=ORG_ID)
    await repo.create_attempt(_make_attempt(), run_id="run-1", organization_id=ORG_ID)

    result = {"provider_response_id": "resp-123", "output": "synthesized text"}
    row = await repo.write_journaled_result(
        "attempt-1", organization_id=ORG_ID, result=result
    )

    assert row.journaled_result == result


@pytest.mark.asyncio
async def test_get_attempts_for_task_orders_by_ordinal(
    repo: RunLifecycleRepository,
) -> None:
    await repo.create_run(_make_run(), organization_id=ORG_ID)
    await repo.create_task(_make_task(), organization_id=ORG_ID)
    await repo.create_attempt(
        _make_attempt(
            attempt_id="attempt-2", ordinal=2, idempotency_key="attempt-key-2"
        ),
        run_id="run-1",
        organization_id=ORG_ID,
    )
    await repo.create_attempt(_make_attempt(), run_id="run-1", organization_id=ORG_ID)

    attempts = await repo.get_attempts_for_task("task-1", organization_id=ORG_ID)

    assert [row.ordinal for row in attempts] == [1, 2]
