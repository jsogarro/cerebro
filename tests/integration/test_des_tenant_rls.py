"""The execution service under the real roles and forced row-level security.

Every assertion runs over a real login as ``cerebro_app`` or ``cerebro_maint``
against a schema built by the deployment path (roles, then migrations), so a
session that forgets its tenant reads nothing and cannot insert.
"""

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    DurableWriteOutcome,
    ExecutionStatus,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.repositories.model_call_reservation_repository import (
    ModelCallReservationRepository,
)
from src.repositories.run_lifecycle_repository import ResearchRunJournalOwner
from tests.integration.crash_fixtures import make_binding, make_project
from tests.integration.rls_fixtures import (  # noqa: F401
    Factory,
    admin_fixture,
    app_fixture,
    engines_fixture,
    maint_fixture,
    server_fixture,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


def _service(app: Factory, maint: Factory | None = None) -> DirectExecutionService:
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=app,
        maintenance_session_factory=maint,
    )


async def _admit(service: DirectExecutionService, run_id: str, org: str):
    status = ExecutionStatus(
        execution_id=f"exec-{run_id}", project_id=str(uuid.uuid4()), status="running"
    )
    await service._admit_run(status, make_binding(run_id, org_id=org), make_project())
    service.active_executions[status.execution_id] = status
    return status


async def _start(service: DirectExecutionService, status: ExecutionStatus):
    return await service._persist_transition(
        status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )


async def _scalar(admin: Factory, sql: str, **params: Any) -> Any:
    async with admin() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _expire_all(admin: Factory) -> None:
    async with admin() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET lease_expires_at = "
                "clock_timestamp() - interval '1 second'"
            )
        )
        await session.commit()


async def _reset(admin: Factory) -> None:
    async with admin() as session:
        await session.execute(
            text(
                "TRUNCATE agent_runs, agent_run_events, agent_run_event_outbox, "
                "agent_run_tasks, agent_task_attempts, agent_run_config_snapshots, "
                "agent_capability_grants, model_call_reservations CASCADE"
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def clean(admin: Factory) -> None:
    await _reset(admin)


pytestmark.append(pytest.mark.asyncio(loop_scope="module"))


def _orm_default(column: Any) -> str:
    arg = column.server_default.arg
    return str(getattr(arg, "text", arg)).split("::")[0].strip("'")


def _db_default(rendered: str) -> str:
    """``'1.0'::character varying`` -> ``1.0``; ``now()`` stays as it is."""
    return rendered.split("::")[0].strip("'")


async def test_the_migrated_schema_carries_every_orm_server_default(
    admin: Factory,
) -> None:
    from src.models.db.base import Base

    async with admin() as session:
        rows = await session.execute(
            text(
                "SELECT table_name, column_name, column_default "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
        in_db = {(table, column): default for table, column, default in rows}
    wrong = sorted(
        (
            table.name,
            column.name,
            _orm_default(column),
            in_db[(table.name, column.name)],
        )
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.server_default is not None
        and (table.name, column.name) in in_db
        and _db_default(in_db[(table.name, column.name)] or "") != _orm_default(column)
    )

    assert not wrong


async def test_a_run_lives_its_whole_life_under_the_app_role(
    app: Factory, admin: Factory
) -> None:
    service = _service(app)
    status = await _admit(service, "own-run", ORG_A)

    assert await _start(service, status) is DurableWriteOutcome.RECORDED
    await service._record_attempt_failure(status, error="transient")
    await service._renew_lease_now(status)
    outcome = await service._persist_transition(
        status,
        run_target=RunStatus.FAILED,
        task_target=TaskStatus.FAILED,
        attempt_target=AttemptStatus.FAILED,
        event_type="run.failed",
        payload={},
        reason="boom",
    )

    assert outcome is DurableWriteOutcome.RECORDED
    assert (
        await _scalar(admin, "SELECT status FROM agent_runs WHERE run_id = 'own-run'")
        == "failed"
    )
    assert await _scalar(
        admin,
        "SELECT array_agg(event_type ORDER BY sequence) FROM agent_run_events "
        "WHERE run_id = 'own-run'",
    ) == ["run.admitted", "run.started", "run.attempt_failed", "run.failed"]


async def test_a_cancellation_request_is_recorded_under_the_app_role(
    app: Factory, admin: Factory
) -> None:
    service = _service(app)
    status = await _admit(service, "cancel-run", ORG_A)
    await _start(service, status)

    assert await service._persist_cancellation(status) is DurableWriteOutcome.RECORDED
    assert (
        await _scalar(
            admin, "SELECT status FROM agent_runs WHERE run_id = 'cancel-run'"
        )
        == "cancelling"
    )


async def test_the_journal_of_a_run_works_under_the_app_role(app: Factory) -> None:
    service = _service(app)
    status = await _admit(service, "journal-run", ORG_A)
    journal = ModelCallReservationRepository(
        app,
        organization_id=ORG_A,
        owner=ResearchRunJournalOwner(
            "journal-run", max_tokens=1000, max_cost_usd=Decimal(1)
        ),
    )

    await journal.reserve(
        "call-1",
        tokens=100,
        cost_usd=Decimal("0.01"),
        price_digest="d" * 64,
        fencing_token=status._fencing_token,
    )


async def test_the_service_of_one_org_cannot_see_or_write_the_rows_of_another(
    app: Factory, admin: Factory
) -> None:
    service = _service(app)
    status_a = await _admit(service, "iso-a", ORG_A)
    await _admit(service, "iso-b", ORG_B)
    await _start(service, status_a)

    async with service._tenant_session(ORG_A) as session:
        for table in (
            "agent_runs",
            "agent_run_tasks",
            "agent_task_attempts",
            "agent_run_events",
        ):
            foreign = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"),
                {"o": ORG_B},
            )
            own = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"),
                {"o": ORG_A},
            )
            assert (foreign, own > 0) == (0, True), table

        hidden = await session.execute(
            text("UPDATE agent_runs SET status_reason = 'x' WHERE run_id = 'iso-b'")
        )
        assert hidden.rowcount == 0
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO agent_run_events (event_id, run_id, organization_id, "
                    "aggregate_type, aggregate_id, sequence, event_type, "
                    "event_type_version, occurred_at, producer, deduplication_key, "
                    "payload) VALUES ('forged', 'iso-b', :o, 'run', 'iso-b', 99, "
                    "'run.failed', '1', now(), 'x', 'forged', '{}')"
                ),
                {"o": ORG_B},
            )
    assert (
        await _scalar(
            admin, "SELECT status_reason FROM agent_runs WHERE run_id = 'iso-b'"
        )
        is None
    )


async def _interrupted_runs(app: Factory, admin: Factory) -> DirectExecutionService:
    dead_owner = _service(app)
    await _admit(dead_owner, "dead-a", ORG_A)
    await _admit(dead_owner, "dead-b", ORG_B)
    await _expire_all(admin)
    return dead_owner


async def _statuses(admin: Factory) -> list[str]:
    async with admin() as session:
        rows = await session.execute(
            text("SELECT status FROM agent_runs ORDER BY run_id")
        )
        return [row[0] for row in rows]


async def test_reconciliation_runs_on_the_maintenance_role_across_orgs(
    app: Factory, maint: Factory, admin: Factory
) -> None:
    await _interrupted_runs(app, admin)

    # The app role sees no run without a tenant, so reconciling on it finds none.
    assert await _service(app, app).reconcile_expired_runs() == 0
    assert await _statuses(admin) == ["queued", "queued"]

    assert await _service(app, maint).reconcile_expired_runs() == 2
    assert await _statuses(admin) == ["failed", "failed"]
    assert (
        await _scalar(
            admin,
            "SELECT count(*) FROM agent_run_events WHERE event_type = 'run.interrupted'",
        )
        == 2
    )


async def test_startup_recovery_runs_on_the_maintenance_role_across_orgs(
    app: Factory, maint: Factory, admin: Factory
) -> None:
    await _interrupted_runs(app, admin)

    assert await _service(app, app).restore_active_executions() == 0
    assert await _statuses(admin) == ["queued", "queued"]

    restarted = _service(app, maint)
    assert await restarted.restore_active_executions() == 2
    assert await _statuses(admin) == ["failed", "failed"]
    assert sorted(
        e.organization_id or "" for e in restarted.active_executions.values()
    ) == sorted([ORG_A, ORG_B])


async def test_a_live_run_of_another_replica_is_restored_not_failed(
    app: Factory, maint: Factory, admin: Factory
) -> None:
    owner = _service(app)
    await _admit(owner, "live-a", ORG_A)

    restarted = _service(app, maint)
    assert await restarted.restore_active_executions() == 1
    assert await _statuses(admin) == ["queued"]


async def _orphan_run(admin: Factory) -> None:
    """An interrupted run with no organization, which no tenant scope can reach."""
    async with admin() as session:
        await session.execute(
            text(
                "INSERT INTO agent_runs (id, run_id, tenant_id, organization_id, "
                "workflow_definition_id, workflow_definition_version, routing_policy_id, "
                "routing_policy_version, idempotency_key, requested_by, status, "
                "owner_instance_id, lease_expires_at) VALUES (gen_random_uuid(), "
                "'orphan', 't', NULL, 'w', '1', 'p', '1', 'k', 'u', 'running', 'dead', "
                "clock_timestamp() - interval '1 second')"
            )
        )
        await session.commit()


def _skipped() -> float:
    return REGISTRY.get_sample_value("cerebro_run_recovery_skipped_total") or 0.0


async def test_a_recovery_scan_counts_a_run_with_no_organization(
    app: Factory, maint: Factory, admin: Factory
) -> None:
    await _orphan_run(admin)
    before = _skipped()

    assert await _service(app, maint).reconcile_expired_runs() == 0
    assert await _service(app, maint).restore_active_executions() == 0

    assert _skipped() == before + 2
    assert await _statuses(admin) == ["running"]
