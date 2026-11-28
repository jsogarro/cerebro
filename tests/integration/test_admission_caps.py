"""Real-Postgres concurrency and production-role RLS tests for run admission caps."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.config import settings
from src.core.contracts import RunStatus
from src.models.db.run_lifecycle import AgentRun
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_lifecycle_repository import (
    RunCapExceededError,
    RunLifecycleRepository,
)
from tests.integration.crash_fixtures import (
    ORG_ID,
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
PASSWORDS = {
    "cerebro_migrator": "migrator-pw",
    "cerebro_app": "app-pw",
    "cerebro_maint": "maint-pw",
}


class _CountingBridge:
    def __init__(self) -> None:
        self.admit_calls = 0
        self.execute_calls = 0

    def admit_execution_plan(self, _plan: Any) -> None:
        self.admit_calls += 1

    async def execute_execution_plan(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        await asyncio.sleep(0)
        return Mock(output={"result": "done"}, workers_used=1)


def _router() -> AsyncMock:
    router = AsyncMock()

    async def _route(*_args: Any, **_kwargs: Any) -> _RoutingDecisionStub:
        await asyncio.sleep(0)
        return _RoutingDecisionStub()

    router.route.side_effect = _route
    return router


def _service(
    session_factory: Any, resolver: Any, bridge: _CountingBridge
) -> DirectExecutionService:
    return DirectExecutionService(
        masr_router=_router(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )


async def _await_terminal(
    session_factory: Any,
    run_id: str,
    *,
    org_id: str,
    set_tenant: bool = False,
) -> AgentRun:
    for _ in range(200):
        async with session_factory() as session:
            if set_tenant:
                await session.execute(
                    text("SELECT set_config('app.current_org_id', :org, true)"),
                    {"org": org_id},
                )
            row = await RunLifecycleRepository(session).get_run(
                run_id, organization_id=org_id
            )
        if row is not None and row.status in (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
        ):
            return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached terminal status")


@pytest.mark.asyncio
async def test_concurrent_distinct_admissions_at_cap_admit_only_one(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two distinct admissions racing at cap=1 leave 1 durable run, 1 cap error,
    and 1 workflow execution.

    A 2-party barrier holds both transactions right after ``create_run`` so they
    enter ``enforce_admission_caps`` simultaneously, and a short yield before
    commit ensures both would see count=1 and succeed if the advisory lock were
    removed.
    """
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", 1)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", 100)

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    entered_cap = 0
    both_entered = asyncio.Event()
    original_enforce = RunLifecycleRepository.enforce_admission_caps
    original_transition = RunLifecycleRepository.record_run_transition

    async def _barrier_enforce(
        self: RunLifecycleRepository, *args: Any, **kwargs: Any
    ) -> None:
        nonlocal entered_cap
        entered_cap += 1
        if entered_cap == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5.0)
        await original_enforce(self, *args, **kwargs)

    async def _delayed_transition(
        self: RunLifecycleRepository, *args: Any, **kwargs: Any
    ) -> Any:
        result = await original_transition(self, *args, **kwargs)
        await asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(
        RunLifecycleRepository, "enforce_admission_caps", _barrier_enforce
    )
    monkeypatch.setattr(
        RunLifecycleRepository, "record_run_transition", _delayed_transition
    )

    binding_a = make_binding("cap-race-a", authority_id="cap-race-auth-a")
    binding_b = make_binding("cap-race-b", authority_id="cap-race-auth-b")
    resolver = MappingExecutionAuthorityResolver(
        {
            ("cap-race-auth-a", "1"): binding_a,
            ("cap-race-auth-b", "1"): binding_b,
        }
    )
    bridge = _CountingBridge()
    service = _service(session_factory, resolver, bridge)
    ref_a = ExecutionAuthorityReference(
        authority_id="cap-race-auth-a", authority_version="1"
    )
    ref_b = ExecutionAuthorityReference(
        authority_id="cap-race-auth-b", authority_version="1"
    )

    try:
        results = await asyncio.gather(
            service.start_research_execution(
                make_project("Run A"), authority_reference=ref_a, organization_id=ORG_ID
            ),
            service.start_research_execution(
                make_project("Run B"), authority_reference=ref_b, organization_id=ORG_ID
            ),
            return_exceptions=True,
        )

        winners = [r for r in results if isinstance(r, str)]
        errors = [r for r in results if isinstance(r, RunCapExceededError)]
        unexpected = [
            r
            for r in results
            if isinstance(r, BaseException) and not isinstance(r, RunCapExceededError)
        ]
        assert unexpected == []
        assert len(winners) == 1
        assert len(errors) == 1
        assert errors[0].code == "run_concurrency_cap_exceeded"

        async with session_factory() as session:
            run_ids = list((await session.scalars(select(AgentRun.run_id))).all())
        assert len(run_ids) == 1

        await _await_terminal(session_factory, run_ids[0], org_id=ORG_ID)
        assert bridge.execute_calls == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_same_run_at_cap_coalesces_without_cap_error(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two replicas racing to admit the exact same run at cap=1 coalesce onto
    one execution id with no cap error because insert precedes cap check."""
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", 1)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", 100)

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "same-run-at-cap"
    binding = make_binding(run_id, authority_id="same-run-cap-auth")
    resolver = MappingExecutionAuthorityResolver({("same-run-cap-auth", "1"): binding})
    ref = ExecutionAuthorityReference(
        authority_id="same-run-cap-auth", authority_version="1"
    )

    bridge_a = _CountingBridge()
    bridge_b = _CountingBridge()
    service_a = _service(session_factory, resolver, bridge_a)
    service_b = _service(session_factory, resolver, bridge_b)

    admit_entered = 0
    both_ready = asyncio.Event()
    original_admit = DirectExecutionService._admit_run

    async def _synced_admit(
        self: DirectExecutionService, *args: Any, **kwargs: Any
    ) -> None:
        nonlocal admit_entered
        admit_entered += 1
        if admit_entered == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5.0)
        await original_admit(self, *args, **kwargs)

    monkeypatch.setattr(DirectExecutionService, "_admit_run", _synced_admit)

    try:
        results = await asyncio.gather(
            service_a.start_research_execution(
                make_project(), authority_reference=ref, organization_id=ORG_ID
            ),
            service_b.start_research_execution(
                make_project(), authority_reference=ref, organization_id=ORG_ID
            ),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, BaseException):
                raise result

        exec_a, exec_b = results
        assert exec_a == exec_b

        await _await_terminal(session_factory, run_id, org_id=ORG_ID)
        assert bridge_a.execute_calls + bridge_b.execute_calls == 1
    finally:
        await service_a.close()
        await service_b.close()


def _run_cmd(command: list[str], url: str, **env: str) -> None:
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url, **env},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _apply_roles(url: str) -> None:
    _run_cmd(
        [sys.executable, "docker/apply_roles.py"],
        url,
        ROLES_ADMIN_DATABASE_URL=url,
        CEREBRO_MIGRATOR_PASSWORD=PASSWORDS["cerebro_migrator"],
        CEREBRO_APP_PASSWORD=PASSWORDS["cerebro_app"],
        CEREBRO_MAINT_PASSWORD=PASSWORDS["cerebro_maint"],
    )


@pytest.fixture(name="rls_server", scope="module")
def rls_server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="app_role_engine", scope="module", loop_scope="module")
async def app_role_engine_fixture(
    rls_server: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    host = rls_server.get_container_host_ip()
    port = rls_server.get_exposed_port(5432)
    admin_url = (
        f"postgresql://{rls_server.username}:{rls_server.password}"
        f"@{host}:{port}/{rls_server.dbname}"
    )
    _apply_roles(admin_url)
    _run_cmd([str(ALEMBIC), "upgrade", "head"], admin_url)
    _apply_roles(admin_url)

    rest = admin_url.split("@", 1)[1]
    app_url = f"postgresql+asyncpg://cerebro_app:{PASSWORDS['cerebro_app']}@{rest}"
    engine = create_async_engine(app_url, pool_size=6, max_overflow=4)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_admissions_under_production_app_role_enforce_caps_with_rls(
    app_role_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Risk R1: under the production ``cerebro_app`` role (NOBYPASSRLS + FORCE
    RLS on ``agent_runs``), concurrent admission cap enforcement must see the
    org's runs and refuse the second admission rather than failing open."""
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", 1)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", 100)

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    session_factory = async_sessionmaker(app_role_engine, expire_on_commit=False)

    # Confirm the connection truly runs as cerebro_app with no RLS bypass.
    async with session_factory() as session:
        role_row = (
            await session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        assert role_row == ("cerebro_app", False, False)

    # First admit a run in org_b so we also verify RLS isolates orgs properly.
    binding_other = make_binding("rls-org-b", authority_id="rls-auth-b", org_id=org_b)
    binding_a1 = make_binding("rls-org-a-1", authority_id="rls-auth-a1", org_id=org_a)
    binding_a2 = make_binding("rls-org-a-2", authority_id="rls-auth-a2", org_id=org_a)
    resolver = MappingExecutionAuthorityResolver(
        {
            ("rls-auth-b", "1"): binding_other,
            ("rls-auth-a1", "1"): binding_a1,
            ("rls-auth-a2", "1"): binding_a2,
        }
    )

    bridge = _CountingBridge()
    service = _service(session_factory, resolver, bridge)

    await service.start_research_execution(
        make_project("Org B"),
        authority_reference=ExecutionAuthorityReference(
            authority_id="rls-auth-b", authority_version="1"
        ),
        organization_id=org_b,
    )

    entered_cap = 0
    both_entered = asyncio.Event()
    original_enforce = RunLifecycleRepository.enforce_admission_caps
    original_transition = RunLifecycleRepository.record_run_transition

    async def _barrier_enforce(
        self: RunLifecycleRepository, *args: Any, **kwargs: Any
    ) -> None:
        nonlocal entered_cap
        entered_cap += 1
        if entered_cap == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5.0)
        await original_enforce(self, *args, **kwargs)

    async def _delayed_transition(
        self: RunLifecycleRepository, *args: Any, **kwargs: Any
    ) -> Any:
        result = await original_transition(self, *args, **kwargs)
        await asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(
        RunLifecycleRepository, "enforce_admission_caps", _barrier_enforce
    )
    monkeypatch.setattr(
        RunLifecycleRepository, "record_run_transition", _delayed_transition
    )

    results = await asyncio.gather(
        service.start_research_execution(
            make_project("Org A 1"),
            authority_reference=ExecutionAuthorityReference(
                authority_id="rls-auth-a1", authority_version="1"
            ),
            organization_id=org_a,
        ),
        service.start_research_execution(
            make_project("Org A 2"),
            authority_reference=ExecutionAuthorityReference(
                authority_id="rls-auth-a2", authority_version="1"
            ),
            organization_id=org_a,
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, str)]
    errors = [r for r in results if isinstance(r, RunCapExceededError)]
    unexpected = [
        r
        for r in results
        if isinstance(r, BaseException) and not isinstance(r, RunCapExceededError)
    ]
    assert unexpected == []
    assert len(winners) == 1
    assert len(errors) == 1
    assert errors[0].code == "run_concurrency_cap_exceeded"

    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": org_a},
        )
        org_a_runs = list((await session.scalars(select(AgentRun.run_id))).all())
    assert len(org_a_runs) == 1

    await _await_terminal(session_factory, org_a_runs[0], org_id=org_a, set_tenant=True)
    await _await_terminal(session_factory, "rls-org-b", org_id=org_b, set_tenant=True)
    # 1 execution for org_b + 1 execution for org_a (second org_a admission refused)
    assert bridge.execute_calls == 2

    await service.close()
