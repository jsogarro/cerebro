"""Real-Postgres production role tests for authority templates and minting.

Verifies that under the production PostgreSQL roles (``cerebro_app`` with
NOBYPASSRLS + tenant RLS, and ``cerebro_maint`` with read-only maintenance
policies):
- ``mint_authority_from_template`` uses ``write_session_factory`` (``cerebro_app``)
  and succeeds with ``tenant_session``, whereas attempting to mint with
  ``cerebro_maint`` fails permission checks.
- ``warm_from_snapshots`` succeeds as ``cerebro_maint`` and skips minted rows
  in SQL.
- ``DirectExecutionService.start_research_execution`` admits runs using a
  minted authority end-to-end under ``cerebro_app``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    PersistedExecutionAuthorityResolver,
)
from src.core.contracts import RunStatus
from src.models.db.session import tenant_session
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.execution_authority_repository import ExecutionAuthorityRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import (
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(autouse=True)
async def cleanup_database() -> AsyncIterator[None]:
    """Override integration conftest's default Postgres fixture (this module manages its own)."""
    yield


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis() -> AsyncIterator[None]:
    """Override integration conftest's Redis fixture (not used here)."""
    yield


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
PASSWORDS = {
    "cerebro_migrator": "migrator-pw",
    "cerebro_app": "app-pw",
    "cerebro_maint": "maint-pw",
}


class _CountingBridge:
    def __init__(self) -> None:
        self.execute_calls = 0

    def admit_execution_plan(self, _plan: Any) -> None:
        return None

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


@pytest.fixture(name="pg_server", scope="module")
def pg_server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="role_engines", scope="module", loop_scope="module")
async def role_engines_fixture(
    pg_server: PostgresContainer,
) -> AsyncIterator[tuple[AsyncEngine, AsyncEngine]]:
    host = pg_server.get_container_host_ip()
    port = pg_server.get_exposed_port(5432)
    admin_url = (
        f"postgresql://{pg_server.username}:{pg_server.password}"
        f"@{host}:{port}/{pg_server.dbname}"
    )
    _apply_roles(admin_url)
    _run_cmd([str(ALEMBIC), "upgrade", "head"], admin_url)
    _apply_roles(admin_url)

    rest = admin_url.split("@", 1)[1]
    app_engine = create_async_engine(
        f"postgresql+asyncpg://cerebro_app:{PASSWORDS['cerebro_app']}@{rest}",
        pool_size=4,
    )
    maint_engine = create_async_engine(
        f"postgresql+asyncpg://cerebro_maint:{PASSWORDS['cerebro_maint']}@{rest}",
        pool_size=4,
    )
    try:
        yield app_engine, maint_engine
    finally:
        await app_engine.dispose()
        await maint_engine.dispose()


@pytest.mark.asyncio(loop_scope="module")
async def test_mint_under_app_role_and_warm_under_maint_role(
    role_engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    app_engine, maint_engine = role_engines
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    maint_factory = async_sessionmaker(maint_engine, expire_on_commit=False)

    # Confirm distinct production roles are active.
    async with app_factory() as session:
        assert (await session.execute(text("SELECT current_user"))).scalar_one() == (
            "cerebro_app"
        )
    async with maint_factory() as session:
        assert (await session.execute(text("SELECT current_user"))).scalar_one() == (
            "cerebro_maint"
        )

    org_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    template = dataclasses.replace(
        make_binding(
            "rls-tpl-seed",
            authority_id="rls-tpl-auth",
            org_id=org_id,
            deadline=now + timedelta(hours=1),
        ),
        run=make_binding(
            "rls-tpl-seed",
            authority_id="rls-tpl-auth",
            org_id=org_id,
            deadline=now + timedelta(hours=1),
        ).run.model_copy(update={"created_at": now, "updated_at": now}),
    )
    async with tenant_session(app_factory, org_id) as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=org_id, is_template=True
        )
        await session.commit()

    tpl_ref = ExecutionAuthorityReference(
        authority_id="rls-tpl-auth", authority_version="1"
    )

    # A resolver wired ONLY with cerebro_maint cannot INSERT minted rows.
    maint_only_resolver = PersistedExecutionAuthorityResolver(
        session_factory=maint_factory
    )
    with pytest.raises(ProgrammingError):
        await maint_only_resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=org_id,
            user_id="user-rls",
            request_id="req-maint-fail",
            now=datetime.now(UTC),
        )

    # Production wiring: maint for reads/warming, app for tenant-scoped writes.
    resolver = PersistedExecutionAuthorityResolver(
        session_factory=maint_factory,
        write_session_factory=app_factory,
    )
    minted = await resolver.mint_authority_from_template(
        tpl_ref,
        organization_id=org_id,
        user_id="user-rls",
        request_id="req-rls-1",
        now=datetime.now(UTC),
    )

    bridge = _CountingBridge()
    service = DirectExecutionService(
        masr_router=_router(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=app_factory,
        execution_authority_resolver=resolver,
    )
    try:
        exec_id = await service.start_research_execution(
            make_project("RLS minted run"),
            authority_reference=ExecutionAuthorityReference(
                authority_id=minted.authority_id,
                authority_version=minted.authority_version,
            ),
            organization_id=org_id,
        )
        assert isinstance(exec_id, str)
        assert resolver._bindings == {}

        for _ in range(200):
            async with tenant_session(app_factory, org_id) as session:
                row = await RunLifecycleRepository(session).get_run(
                    minted.run.run_id, organization_id=org_id
                )
            if row is not None and row.status == RunStatus.SUCCEEDED.value:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("minted run did not reach SUCCEEDED under cerebro_app")
        assert bridge.execute_calls == 1
    finally:
        await service.close()

    # Cold restart warming runs as cerebro_maint and must skip the minted snapshot.
    cold_resolver = PersistedExecutionAuthorityResolver(
        session_factory=maint_factory,
        write_session_factory=app_factory,
    )
    async with maint_factory() as warm_session:
        warmed = await cold_resolver.warm_from_snapshots(warm_session)
    assert warmed == 0
    assert cold_resolver._bindings == {}
