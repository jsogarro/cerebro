"""A checkpoint belongs to one tenant, one project and (when known) one run.

Checkpoints are diagnostic snapshots of a run's state, including its output, so
the identity they are stored and read under decides who can see that output. The
tenant is the project's organization; a run recorded against the checkpoint must
be a run of that same organization, and the run's own version pins are copied
from the durable run row rather than taken from the caller.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.models.db.research_project import ProjectStatus, ResearchProject
from src.repositories.checkpoint_repository import (
    CheckpointIdentityError,
    CheckpointRepository,
)
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
DATA = {"status": "completed", "final_output": {"secret": "org A output"}}


def _project(org: uuid.UUID) -> ResearchProject:
    return ResearchProject(
        title="p",
        query='{"text": "q", "domains": ["ai"]}',
        domains=["ai"],
        status=ProjectStatus.IN_PROGRESS,
        user_id=uuid.uuid4(),
        organization_id=org,
        project_metadata={},
    )


@pytest_asyncio.fixture(name="world")
async def world_fixture(test_engine: AsyncEngine) -> dict[str, uuid.UUID]:
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        project_a, project_b = _project(ORG_A), _project(ORG_B)
        session.add_all([project_a, project_b])
        await session.flush()
        await seed_run_task_attempt(session, organization_id=ORG_A, run_id="run-a1")
        await seed_run_task_attempt(
            session,
            organization_id=ORG_A,
            run_id="run-a2",
            task_id="task-a2",
            attempt_id="attempt-a2",
        )
        await seed_run_task_attempt(
            session,
            organization_id=ORG_B,
            run_id="run-b",
            task_id="task-b",
            attempt_id="attempt-b",
        )
        await session.commit()
    return {"project_a": project_a.id, "project_b": project_b.id}


@pytest_asyncio.fixture(name="session")
async def session_fixture(
    test_engine: AsyncEngine, world: dict[str, uuid.UUID]
) -> AsyncSession:
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


async def _create(
    session: AsyncSession,
    project_id: uuid.UUID,
    org: uuid.UUID,
    run_id: str | None,
    workflow_id: str = "wf-1",
):
    return await CheckpointRepository(session).create_checkpoint(
        workflow_id=workflow_id,
        project_id=project_id,
        checkpoint_data=DATA,
        phase="done",
        run_id=run_id,
        organization_id=org,
    )


@pytest.mark.asyncio
async def test_a_run_of_another_tenant_cannot_be_recorded_on_a_checkpoint(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(CheckpointIdentityError):
        await _create(session, world["project_a"], ORG_A, "run-b")


@pytest.mark.asyncio
async def test_a_project_of_another_tenant_cannot_be_checkpointed(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(CheckpointIdentityError):
        await _create(session, world["project_b"], ORG_A, None)


@pytest.mark.asyncio
async def test_an_unknown_run_cannot_be_recorded_on_a_checkpoint(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(CheckpointIdentityError):
        await _create(session, world["project_a"], ORG_A, "no-such-run")


@pytest.mark.asyncio
async def test_the_runs_version_pins_come_from_the_durable_run(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    checkpoint = await _create(session, world["project_a"], ORG_A, "run-a1")

    assert checkpoint.run_id == "run-a1"
    assert checkpoint.recovery_metadata["identity"] == {
        "organization_id": str(ORG_A),
        "run_id": "run-a1",
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
    }


@pytest.mark.asyncio
async def test_another_tenant_reads_nothing_through_any_lookup(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    repository = CheckpointRepository(session)
    checkpoint = await _create(session, world["project_a"], ORG_A, "run-a1")
    await session.flush()

    assert (
        await repository.get_recovery_point(world["project_a"], organization_id=ORG_B)
        is None
    )
    assert await repository.get_latest("wf-1", organization_id=ORG_B) is None
    assert await repository.get_by_phase("wf-1", "done", organization_id=ORG_B) is None
    assert await repository.list_checkpoints("wf-1", organization_id=ORG_B) == []
    assert (
        await repository.restore_from_checkpoint(checkpoint.id, organization_id=ORG_B)
        is None
    )
    assert (
        await repository.cleanup_old("wf-1", keep_count=0, organization_id=ORG_B) == 0
    )
    owner = await repository.restore_from_checkpoint(
        checkpoint.id, organization_id=ORG_A
    )
    assert owner is not None and owner["checkpoint_data"] == DATA


@pytest.mark.asyncio
async def test_a_checkpoint_of_one_run_is_not_the_recovery_point_of_another(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    repository = CheckpointRepository(session)
    first = await _create(session, world["project_a"], ORG_A, "run-a1")
    await session.commit()  # distinct created_at, so "latest" is well defined
    await _create(session, world["project_a"], ORG_A, "run-a2", workflow_id="wf-2")
    await session.commit()

    found = await repository.get_recovery_point(
        world["project_a"], organization_id=ORG_A, run_id="run-a1"
    )

    assert found is not None and found.id == first.id


@pytest.mark.asyncio
async def test_a_checkpoint_predating_run_linkage_stays_readable(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    legacy = await _create(session, world["project_a"], ORG_A, None)

    found = await CheckpointRepository(session).get_recovery_point(
        world["project_a"], organization_id=ORG_A
    )

    assert found is not None and found.id == legacy.id
    assert legacy.run_id is None


@pytest.mark.asyncio
async def test_an_error_checkpoint_keeps_its_identity_and_records_the_error(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    checkpoint = await CheckpointRepository(session).mark_error_checkpoint(
        workflow_id="wf-1",
        project_id=world["project_a"],
        checkpoint_data=DATA,
        phase="failed",
        error_info={"code": "boom"},
        organization_id=ORG_A,
        run_id="run-a1",
    )
    await session.commit()
    await session.refresh(checkpoint)

    assert checkpoint.recovery_metadata["error_info"] == {"code": "boom"}
    assert checkpoint.recovery_metadata["identity"]["run_id"] == "run-a1"


@pytest.mark.asyncio
async def test_statistics_count_only_the_callers_tenant(
    session: AsyncSession, world: dict[str, uuid.UUID]
) -> None:
    repository = CheckpointRepository(session)
    await _create(session, world["project_a"], ORG_A, None)
    await session.flush()

    other = await repository.get_checkpoint_statistics(organization_id=ORG_B)
    own = await repository.get_checkpoint_statistics(organization_id=ORG_A)

    assert other["total_checkpoints"] == 0
    assert other["recoverable_checkpoints"] == 0
    assert other["error_checkpoints"] == 0
    assert other["automatic_checkpoints"] == 0
    assert other["type_distribution"] == {}
    assert other["phase_distribution"] == {}
    assert own["total_checkpoints"] == 1
    assert own["recoverable_checkpoints"] == 1


FULL = {"status": "running", "progress_percentage": 40.0, "current_phase": "x"}


def _service(test_engine: AsyncEngine):
    from unittest.mock import AsyncMock

    from src.api.services.direct_execution_service import DirectExecutionService

    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory, DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=AsyncMock(),
        event_publisher=AsyncMock(),
        session_factory=factory,
    )


async def _checkpoint_run(factory, world, run_id: str) -> None:
    async with factory() as session:
        await CheckpointRepository(session).create_checkpoint(
            workflow_id=f"wf-{run_id}",
            project_id=world["project_a"],
            checkpoint_data=FULL,
            phase="p",
            run_id=run_id,
            organization_id=ORG_A,
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_resumed_execution_keeps_the_run_its_checkpoint_belongs_to(
    test_engine: AsyncEngine, world: dict[str, uuid.UUID]
) -> None:
    factory, service = _service(test_engine)
    await _checkpoint_run(factory, world, "run-a1")
    await _checkpoint_run(factory, world, "run-a2")

    execution_id = await service.resume_execution(
        world["project_a"], organization_id=str(ORG_A)
    )

    assert execution_id == "wf-run-a2"
    assert service.active_executions[execution_id].run_id == "run-a2"


@pytest.mark.asyncio
async def test_a_checkpoint_whose_pins_disagree_with_its_run_is_not_resumed(
    test_engine: AsyncEngine, world: dict[str, uuid.UUID]
) -> None:
    from sqlalchemy import text

    factory, service = _service(test_engine)
    await _checkpoint_run(factory, world, "run-a1")
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET workflow_definition_version = '2' "
                "WHERE run_id = 'run-a1'"
            )
        )
        await session.commit()

    assert (
        await service.resume_execution(world["project_a"], organization_id=str(ORG_A))
        is None
    )
    assert service.active_executions == {}
