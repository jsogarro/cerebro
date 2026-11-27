"""Database roles and forced row-level security, exercised as the real roles.

Two organizations are seeded as the superuser; every assertion then runs over a
real login as ``cerebro_app`` or ``cerebro_maint``, so grants, policies and the
pooled-connection reset are all the production ones.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.models.db.execution_authority import AgentExecutionAuthority
from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
from src.models.db.run_lifecycle import AgentRun
from src.models.db.user import User

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
PASSWORDS = {
    "cerebro_migrator": "migrator-pw",
    "cerebro_app": "app-pw",
    "cerebro_maint": "maint-pw",
}

# Every table whose policy keys on app.current_org_id. A new tenant table must be
# added here, forced, and given a maintenance policy or a place in NO_MAINTENANCE.
TENANT_TABLES = frozenset(
    {
        "research_projects",
        "agent_runs",
        "agent_run_tasks",
        "agent_task_attempts",
        "agent_run_config_snapshots",
        "agent_run_events",
        "agent_run_event_outbox",
        "agent_capability_grants",
        "agent_capability_approvals",
        "agent_artifacts",
        "agent_tool_invocations",
        "agent_evidence",
        "agent_claim_supports",
        "agent_execution_authorities",
        "uploaded_documents",
        "usage_ledger",
        "document_critiques",
        "critique_findings",
        "similarity_checks",
        "similarity_matches",
        "egress_events",
    }
)
NO_MAINTENANCE = frozenset(
    {
        "research_projects",
        "agent_run_events",
        "agent_capability_grants",
        "agent_capability_approvals",
        "agent_artifacts",
        "agent_tool_invocations",
        "agent_evidence",
        "agent_claim_supports",
        "usage_ledger",
        "document_critiques",
        "critique_findings",
        "similarity_checks",
        "similarity_matches",
    }
)
ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


def _run(command: list[str], url: str, **env: str) -> None:
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
    _run(
        [sys.executable, "docker/apply_roles.py"],
        url,
        ROLES_ADMIN_DATABASE_URL=url,
        CEREBRO_MIGRATOR_PASSWORD=PASSWORDS["cerebro_migrator"],
        CEREBRO_APP_PASSWORD=PASSWORDS["cerebro_app"],
        CEREBRO_MAINT_PASSWORD=PASSWORDS["cerebro_maint"],
    )


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest.fixture(name="admin_url", scope="module")
def admin_url_fixture(server: PostgresContainer) -> str:
    host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
    return f"postgresql://{server.username}:{server.password}@{host}:{port}/{server.dbname}"


def _role_url(admin_url: str, role: str) -> str:
    rest = admin_url.split("@", 1)[1]
    return f"postgresql+asyncpg://{role}:{PASSWORDS[role]}@{rest}"


@pytest_asyncio.fixture(name="seeded", scope="module", loop_scope="module")
async def seeded_fixture(admin_url: str) -> str:
    """Roles first, then migrations, as a deployment runs them; two orgs of data."""
    _apply_roles(admin_url)
    _run([str(ALEMBIC), "upgrade", "head"], admin_url)
    _apply_roles(admin_url)  # second pass: idempotent, and now grants existing tables
    engine = create_async_engine(admin_url.replace("postgresql://", "postgresql+asyncpg://"))
    async with engine.begin() as connection:
        for org in (ORG_A, ORG_B):
            user_id = uuid.uuid4()
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.com",
                        username=str(user_id),
                        hashed_password="x",
                    )
                )
                await session.flush()
            await connection.execute(
                text(
                    "INSERT INTO research_projects (id, title, query, domains, status, "
                    "metadata, user_id, organization_id) "
                    "VALUES (gen_random_uuid(), 't', 'q', '[]', 'DRAFT', '{}', :u, :o)"
                ),
                {"u": user_id, "o": org},
            )
            run_id = f"run-{org}"
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                session.add(_run_row(org, run_id))
                await session.flush()
                session.add(
                    AgentRunEvent(
                        event_id=f"evt-{org}",
                        run_id=run_id,
                        organization_id=org,
                        aggregate_type="run",
                        aggregate_id=run_id,
                        sequence=1,
                        event_type="run.admitted",
                        event_type_version="1",
                        occurred_at=datetime.now(UTC),
                        producer="test",
                        deduplication_key=f"evt-{org}",
                        payload={},
                    )
                )
                await session.flush()
                session.add(
                    AgentRunEventOutbox(
                        event_id=f"evt-{org}",
                        run_id=run_id,
                        organization_id=org,
                        destination="redis",
                        idempotency_key=f"evt-{org}",
                        payload={},
                    )
                )
                session.add(
                    AgentExecutionAuthority(
                        organization_id=org,
                        authority_id=f"a-{org}",
                        authority_version="1",
                        configuration={},
                        content_sha256="0" * 64,
                    )
                )
                await session.flush()
            await connection.execute(
                text(
                    "INSERT INTO uploaded_documents (id, organization_id, user_id, "
                    "filename_enc, media_type, source_sha256, text_sha256, char_count, "
                    "pages, extractor_version, invisible_chars_removed, text_enc, "
                    "declared_authors_enc, expires_at) VALUES (gen_random_uuid(), :o, :u, "
                    "'f', 'pdf', 's', 't', 5, '[]', 'v', 0, 'x', 'y', now() - interval '1 day'), "
                    "(gen_random_uuid(), :o, :u, 'f', 'pdf', 's', 't', 5, '[]', 'v', 0, 'x', "
                    "'y', now() + interval '1 day')"
                ),
                {"o": org, "u": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO egress_events (id, organization_id, user_id, host, "
                    "request_class, term_count, bytes_received, duration_ms, status, "
                    "created_at) VALUES (gen_random_uuid(), :o, :u, 'arxiv.org', 'pdf', 0, "
                    "1, 1, '200', now() - interval '400 days')"
                ),
                {"o": org, "u": user_id},
            )
    await engine.dispose()
    return admin_url


@pytest_asyncio.fixture(name="app_engine", loop_scope="module")
async def app_engine_fixture(seeded: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_role_url(seeded, "cerebro_app"), pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(name="maint_engine", loop_scope="module")
async def maint_engine_fixture(seeded: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_role_url(seeded, "cerebro_maint"), pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


def _run_row(org: uuid.UUID, run_id: str) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        tenant_id=str(org),
        organization_id=org,
        workflow_definition_id="w",
        workflow_definition_version="1",
        routing_policy_id="p",
        routing_policy_version="1",
        idempotency_key=run_id,
        requested_by="u",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.begin() as connection:
        return int(await connection.scalar(text(f"SELECT count(*) FROM {sql}")) or 0)


pytestmark.append(pytest.mark.asyncio(loop_scope="module"))


async def test_catalog_roles_and_forced_tables(seeded: str) -> None:
    engine = create_async_engine(seeded.replace("postgresql://", "postgresql+asyncpg://"))
    async with engine.connect() as connection:
        roles = {
            row.rolname: row
            for row in await connection.execute(
                text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname LIKE 'cerebro\\_%'")
            )
        }
        owned = (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                    "WHERE r.rolname IN ('cerebro_app', 'cerebro_maint')"
                )
            )
        )
        rls = {
            row.relname: row.relforcerowsecurity
            for row in await connection.execute(
                text("SELECT relname, relforcerowsecurity FROM pg_class WHERE relrowsecurity AND relkind = 'r'")
            )
        }
        maint_tables = {
            row.tablename
            for row in await connection.execute(
                text("SELECT tablename FROM pg_policies WHERE 'cerebro_maint' = ANY (roles)")
            )
        }
    await engine.dispose()

    assert not roles["cerebro_app"].rolsuper and not roles["cerebro_app"].rolbypassrls
    assert not roles["cerebro_maint"].rolsuper and not roles["cerebro_maint"].rolbypassrls
    assert roles["cerebro_migrator"].rolbypassrls
    assert owned == 0
    tenant = {name for name in rls if name in TENANT_TABLES}
    assert tenant == TENANT_TABLES, f"tenant tables without RLS: {TENANT_TABLES - tenant}"
    assert all(rls[name] for name in TENANT_TABLES), "a tenant table is not FORCEd"
    assert maint_tables == TENANT_TABLES - NO_MAINTENANCE


async def test_every_tenant_policy_tolerates_an_empty_setting(app_engine: AsyncEngine) -> None:
    """A pooled connection leaves ``''`` behind; a bare ::uuid cast would raise."""
    async with app_engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(ORG_A)}
        )
    for table in sorted(TENANT_TABLES):
        assert await _count(app_engine, table) == 0, table


async def test_app_role_without_tenant_context_sees_and_claims_nothing(
    app_engine: AsyncEngine,
) -> None:
    from src.api.services.outbox_relay import OutboxRelay

    assert await _count(app_engine, "agent_runs") == 0
    relay = OutboxRelay(
        session_factory=async_sessionmaker(app_engine, expire_on_commit=False),
        event_publisher=AsyncMock(),
    )
    assert await relay.run_once() == 0


async def test_app_role_with_a_tenant_sees_only_it_and_cannot_write_another(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(ORG_A)}
        )
        seen = {
            row[0]
            for row in await connection.execute(text("SELECT organization_id FROM agent_runs"))
        }
        assert seen == {ORG_A}
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            session.add(_run_row(ORG_B, "intruder"))
            with pytest.raises(DBAPIError, match="row-level security"):
                await session.flush()


async def test_maintenance_role_reads_every_tenants_runs_and_authorities(
    maint_engine: AsyncEngine,
) -> None:
    assert await _count(maint_engine, "agent_runs") == 2
    assert await _count(maint_engine, "agent_execution_authorities") == 2
    # Granted and policy-admitted even when empty.
    for table in ("agent_run_tasks", "agent_task_attempts", "agent_run_config_snapshots"):
        await _count(maint_engine, table)


async def test_maintenance_role_sees_nothing_it_has_no_policy_for(
    maint_engine: AsyncEngine,
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await _count(maint_engine, "research_projects")
    # Granted for the purge, so only the row policy keeps unexpired uploads out.
    assert await _count(maint_engine, "uploaded_documents WHERE expires_at > now()") == 0


async def test_maintenance_role_cannot_write_runs(maint_engine: AsyncEngine) -> None:
    async with maint_engine.begin() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(text("UPDATE agent_runs SET requested_by = 'x'"))


async def test_the_outbox_relay_claims_every_tenants_rows_as_the_maintenance_role(
    maint_engine: AsyncEngine,
) -> None:
    from src.api.services.outbox_relay import OutboxRelay

    publisher = AsyncMock()
    publisher.publish_run_event.return_value = True
    relay = OutboxRelay(
        session_factory=async_sessionmaker(maint_engine, expire_on_commit=False),
        event_publisher=publisher,
    )
    assert await relay.run_once() == 2


async def test_the_purge_deletes_expired_uploads_and_egress_rows_across_tenants(
    maint_engine: AsyncEngine, seeded: str
) -> None:
    from src.repositories.uploaded_document_repository import UploadedDocumentRepository

    async with async_sessionmaker(maint_engine, expire_on_commit=False)() as session:
        purged = await UploadedDocumentRepository(session, None).purge_expired(datetime.now(UTC))
        await session.commit()
    assert purged == 2
    admin = create_async_engine(seeded.replace("postgresql://", "postgresql+asyncpg://"))
    assert await _count(admin, "uploaded_documents") == 2
    assert await _count(admin, "egress_events") == 0
    await admin.dispose()


async def test_tenant_session_scopes_every_transaction_it_opens(
    app_engine: AsyncEngine,
) -> None:
    from src.models.db.session import tenant_session

    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with tenant_session(factory, str(ORG_B)) as session:
        first = (await session.execute(text("SELECT count(*) FROM agent_runs"))).scalar_one()
        await session.commit()
        # A second transaction on the same session keeps the tenant.
        second = (
            await session.execute(text("SELECT count(DISTINCT organization_id) FROM agent_runs"))
        ).scalar_one()
    assert (first, second) == (1, 1)
    assert await _count(app_engine, "agent_runs") == 0
