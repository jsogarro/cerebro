"""Unit and SQLite integration tests for authority templates and deterministic minting."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.api.routes import query_api
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
    RequestIdExpiredError,
    mint_run_binding,
    validate_template_binding,
)
from src.api.services.research_kernel import compose_application_research_kernel
from src.core.capabilities.issuer import PlanCapabilityIssuer
from src.core.config import settings
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    PinnedVersions,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    RunStatus,
    WorkerAssignment,
)
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.capability import AgentCapabilityGrant
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.execution_authority import AgentExecutionAuthority
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.repositories.execution_authority_repository import ExecutionAuthorityRepository
from src.repositories.run_config_snapshot_repository import RunConfigSnapshotRepository
from src.repositories.run_lifecycle_repository import (
    RunCapExceededError,
    RunLifecycleRepository,
)
from tests.fixtures.authenticated_caller import allow_authenticated
from tests.integration.crash_fixtures import (
    _RoutingDecisionStub,
    make_project,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PARENT_REVISION = "d4e8f1a3b7c2"
NEW_REVISION = "e6a3b9c4d1f2"

ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"
TEMPLATE_CREATED = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
TEMPLATE_DELTA = timedelta(minutes=30)
MINT_NOW = datetime(2026, 8, 4, 15, 0, 0, tzinfo=UTC)

_TABLES = [
    AgentExecutionAuthority.__table__,
    AgentRun.__table__,
    AgentRunTask.__table__,
    AgentTaskAttempt.__table__,
    AgentRunConfigSnapshot.__table__,
    AgentRunEvent.__table__,
    AgentRunEventOutbox.__table__,
    AgentCapabilityGrant.__table__,
    AgentArtifact.__table__,
    AgentEvidence.__table__,
    AgentClaimSupport.__table__,
]


def _make_template_binding(
    *,
    authority_id: str = "tpl-research",
    authority_version: str = "1",
    org_id: str = ORG_A,
    created_at: datetime = TEMPLATE_CREATED,
    delta: timedelta = TEMPLATE_DELTA,
    max_cost_usd: Decimal = Decimal("2.50"),
) -> ExecutionAuthorityBinding:
    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id=authority_id,
        authority_version=authority_version,
        run_id="template-seed-run",
        workflow_definition_id="workflow-research",
        routing_policy_id="policy-balanced",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=(
            WorkerAssignment(
                worker_id="worker-lit",
                worker_type="literature",
                objective="Find peer-reviewed literature",
                output_schema={"type": "object"},
                permission_scopes=("search:read",),
                tool_allowlist=("mcp.academic_search",),
                allowed_outbound_hosts=("api.semanticscholar.org", "export.arxiv.org"),
            ),
        ),
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id="worker-lit",
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.ORDERED_PROVIDER_MODEL,
            fallbacks=(
                ProviderModelRoute(provider="anthropic", model="claude-sonnet-4-6"),
            ),
            provider_allowlist=("gemini", "anthropic"),
            model_allowlist=("gemini-2.5-pro", "claude-sonnet-4-6"),
        ),
        budget=ExecutionBudget(
            max_cost_usd=max_cost_usd,
            max_total_tokens=25000,
            max_tool_invocations=12,
            max_parallel_tasks=1,
            max_attempts_per_task=2,
            task_timeout_seconds=120,
        ),
        stop_conditions=("complete", "budget_exhausted"),
        evaluator_requirements=("citation_grounding",),
        deadline=created_at + delta,
        compiled_at=created_at,
    )
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={
                "tenant_id": org_id,
                "idempotency_key": f"seed-{authority_id}-{authority_version}",
                "requested_by": "operator-admin",
            }
        ),
    )


def _pinned_versions(binding: ExecutionAuthorityBinding) -> PinnedVersions:
    return PinnedVersions(
        workflow_definition_id=binding.run.workflow_definition_id,
        workflow_definition_version=binding.run.workflow_definition_version,
        routing_policy_id=binding.run.routing_policy_id,
        routing_policy_version=binding.run.routing_policy_version,
        event_envelope_version="1.0",
    )


@pytest_asyncio.fixture(name="sqlite_factory")
async def sqlite_factory_fixture(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    db_path = tmp_path / "authority_mint.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


# --- 0. Migration + ORM parity -----------------------------------------------


def test_is_template_migration_parity() -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == [NEW_REVISION]

    rev = script.get_revision(NEW_REVISION)
    assert rev is not None
    assert rev.down_revision == PARENT_REVISION

    col = AgentExecutionAuthority.__table__.c.get("is_template")
    assert col is not None
    assert isinstance(col.type, Boolean)
    assert col.nullable is False
    assert col.default is not None and col.default.arg is False
    assert col.server_default is not None

    alembic_bin = Path(sys.executable).parent / "alembic"
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
    }
    upgrade = subprocess.run(
        [str(alembic_bin), "upgrade", f"{PARENT_REVISION}:{NEW_REVISION}", "--sql"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    downgrade = subprocess.run(
        [str(alembic_bin), "downgrade", f"{NEW_REVISION}:{PARENT_REVISION}", "--sql"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        "ADD COLUMN is_template BOOLEAN DEFAULT false NOT NULL" in upgrade.stdout
        or "ADD COLUMN is_template BOOLEAN NOT NULL DEFAULT false" in upgrade.stdout
    ), upgrade.stdout
    assert "DROP COLUMN is_template" in downgrade.stdout, downgrade.stdout


# --- 1. Pure deterministic minting -------------------------------------------


def test_mint_copies_policy_and_budget_verbatim() -> None:
    template = _make_template_binding(max_cost_usd=Decimal("3.75"))
    minted = mint_run_binding(
        template, user_id="user-42", request_id="req-1", now=MINT_NOW
    )

    assert minted.authority_id == template.authority_id
    assert minted.workflow_definition == template.workflow_definition
    assert minted.routing_policy == template.routing_policy
    assert minted.domains == template.domains
    assert minted.supervisor_id == template.supervisor_id
    assert minted.supervisor_type == template.supervisor_type
    assert minted.workers == template.workers
    assert minted.workers[0].allowed_outbound_hosts == (
        "api.semanticscholar.org",
        "export.arxiv.org",
    )
    assert minted.edges == template.edges
    assert minted.provider_model_policy == template.provider_model_policy
    assert minted.budget == template.budget
    assert minted.budget.max_cost_usd == Decimal("3.75")
    assert minted.stop_conditions == template.stop_conditions
    assert minted.evaluator_requirements == template.evaluator_requirements


def test_mint_is_deterministic_per_request_id() -> None:
    template = _make_template_binding()
    first = mint_run_binding(
        template, user_id="user-1", request_id="req-same", now=MINT_NOW
    )
    second = mint_run_binding(
        template, user_id="user-1", request_id="req-same", now=MINT_NOW
    )

    assert first.authority_version == second.authority_version
    assert first.run.run_id == second.run.run_id
    assert first.run.idempotency_key == second.run.idempotency_key
    assert first.authority_version != template.authority_version
    assert first.run.run_id != template.run.run_id
    assert len(first.run.idempotency_key) == 64

    # UUID formatting differences in template tenant_id normalize identically.
    uppercase_template = dataclasses.replace(
        template,
        run=template.run.model_copy(
            update={"tenant_id": ORG_A.upper().replace("-", "")}
        ),
    )
    normalized = mint_run_binding(
        uppercase_template, user_id="user-1", request_id="req-same", now=MINT_NOW
    )
    assert normalized.authority_version == first.authority_version
    assert normalized.run.run_id == first.run.run_id
    assert normalized.run.idempotency_key == first.run.idempotency_key


def test_mint_differs_across_request_id_user_org_and_template_version() -> None:
    base_tpl = _make_template_binding(authority_version="1", org_id=ORG_A)
    base = mint_run_binding(
        base_tpl, user_id="user-1", request_id="req-1", now=MINT_NOW
    )

    diff_req = mint_run_binding(
        base_tpl, user_id="user-1", request_id="req-2", now=MINT_NOW
    )
    diff_user = mint_run_binding(
        base_tpl, user_id="user-2", request_id="req-1", now=MINT_NOW
    )
    diff_org = mint_run_binding(
        _make_template_binding(authority_version="1", org_id=ORG_B),
        user_id="user-1",
        request_id="req-1",
        now=MINT_NOW,
    )
    diff_ver = mint_run_binding(
        _make_template_binding(authority_version="2", org_id=ORG_A),
        user_id="user-1",
        request_id="req-1",
        now=MINT_NOW,
    )

    for label, other in [
        ("request_id", diff_req),
        ("user_id", diff_user),
        ("org", diff_org),
        ("template_version", diff_ver),
    ]:
        assert other.authority_version != base.authority_version, label
        assert other.run.run_id != base.run.run_id, label
        assert other.run.idempotency_key != base.run.idempotency_key, label


def test_mint_tenant_from_template_requester_from_user() -> None:
    template = _make_template_binding(org_id=ORG_A)
    minted = mint_run_binding(
        template, user_id="caller-user-99", request_id="req-1", now=MINT_NOW
    )

    assert minted.run.tenant_id == ORG_A
    assert minted.run.requested_by == "caller-user-99"
    assert minted.run.created_at == MINT_NOW
    assert minted.run.updated_at == MINT_NOW

    with pytest.raises(ValueError, match="user_id"):
        mint_run_binding(template, user_id="", request_id="req-1", now=MINT_NOW)
    with pytest.raises(ValueError, match="request_id"):
        mint_run_binding(template, user_id="user-1", request_id="   ", now=MINT_NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        mint_run_binding(
            template,
            user_id="user-1",
            request_id="req-1",
            now=datetime(2026, 8, 4, 15, 0, 0),
        )


def test_mint_deadline_relative_to_now() -> None:
    template = _make_template_binding(
        created_at=TEMPLATE_CREATED, delta=timedelta(minutes=45)
    )
    minted = mint_run_binding(
        template, user_id="user-1", request_id="req-1", now=MINT_NOW
    )

    assert minted.deadline == MINT_NOW + timedelta(minutes=45)
    assert minted.deadline != template.deadline


@pytest.mark.parametrize("bad_delta", [timedelta(0), timedelta(seconds=-10)])
def test_mint_rejects_nonpositive_delta(bad_delta: timedelta) -> None:
    template = _make_template_binding(created_at=TEMPLATE_CREATED, delta=bad_delta)
    with pytest.raises(ValueError, match="deadline"):
        mint_run_binding(template, user_id="user-1", request_id="req-1", now=MINT_NOW)


def _matching_proposal() -> _RoutingDecisionStub:
    proposal = _RoutingDecisionStub()
    proposal.optimization_result = type(
        "_Opt",
        (),
        {
            "primary_model": type(
                "_M1", (), {"provider": "gemini", "model_name": "gemini-2.5-pro"}
            )(),
            "fallback_models": [
                type(
                    "_M2",
                    (),
                    {"provider": "anthropic", "model_name": "claude-sonnet-4-6"},
                )()
            ],
        },
    )()
    return proposal


def test_minted_binding_compiles_plan_and_issues_capabilities() -> None:
    """Risk R3: ExecutionPlanCompiler and PlanCapabilityIssuer accept a minted binding."""
    template = _make_template_binding(
        created_at=TEMPLATE_CREATED, delta=timedelta(minutes=30)
    )
    minted = dataclasses.replace(
        mint_run_binding(template, user_id="user-1", request_id="req-r3", now=MINT_NOW),
        clock=lambda: MINT_NOW + timedelta(seconds=1),
    )

    plan = ExecutionPlanCompiler().compile(_matching_proposal(), minted)
    assert plan.run_id == minted.run.run_id
    assert plan.deadline == MINT_NOW + timedelta(minutes=30)
    assert plan.compiled_at == MINT_NOW + timedelta(seconds=1)

    grants = PlanCapabilityIssuer.issue(
        plan,
        run_id=minted.run.run_id,
        task_id="task-1",
        issued_at=MINT_NOW + timedelta(seconds=2),
    )
    assert len(grants) == 1
    assert grants[0].allowed_hosts == ("api.semanticscholar.org", "export.arxiv.org")
    assert grants[0].expires_at <= plan.deadline


# --- 2. Persistence, template filtering, tenant isolation, caching -----------


@pytest.mark.asyncio
async def test_non_template_authority_unavailable_for_mint(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    binding = _make_template_binding(authority_id="one-off", authority_version="1")
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            binding, organization_id=ORG_A, is_template=False
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(
        session_factory=factory, clock=lambda: MINT_NOW
    )
    with pytest.raises(ExecutionAuthorityUnavailableError) as non_tpl_exc:
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(authority_id="one-off", authority_version="1"),
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-1",
        )
    with pytest.raises(ExecutionAuthorityUnavailableError) as missing_exc:
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(authority_id="missing", authority_version="1"),
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-1",
        )
    assert str(non_tpl_exc.value) == str(missing_exc.value)

    async with factory() as session:
        rows = (await session.execute(select(AgentExecutionAuthority))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_minted_row_is_not_a_template(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(authority_id="tpl-1", authority_version="1")
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(
        session_factory=factory, clock=lambda: MINT_NOW
    )
    minted = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-1", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-1",
    )

    async with factory() as session:
        row = await ExecutionAuthorityRepository(session).get(
            authority_id=minted.authority_id,
            authority_version=minted.authority_version,
            organization_id=ORG_A,
        )
        assert row is not None
        assert row.is_template is False

    with pytest.raises(ExecutionAuthorityUnavailableError):
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(
                authority_id=minted.authority_id,
                authority_version=minted.authority_version,
            ),
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-2",
        )


@pytest.mark.asyncio
async def test_minted_bindings_not_cached(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(authority_id="tpl-nocache", authority_version="1")
    operator_binding = _make_template_binding(
        authority_id="op-regular", authority_version="1"
    )
    operator_binding = dataclasses.replace(
        operator_binding,
        run=operator_binding.run.model_copy(
            update={"run_id": "op-run-1", "idempotency_key": "op-key-1"}
        ),
    )

    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(
        session_factory=factory, clock=lambda: MINT_NOW
    )

    # 1. mint_authority_from_template must not cache
    minted = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-nocache", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-nc-1",
    )
    minted_key = (minted.authority_id, minted.authority_version)
    minted_ref = ExecutionAuthorityReference(
        authority_id=minted.authority_id, authority_version=minted.authority_version
    )
    assert minted_key not in resolver._bindings

    # 2. load_authority returns the minted binding without caching it
    loaded = await resolver.load_authority(minted_ref, organization_id=ORG_A)
    assert loaded is not None
    assert loaded.run.run_id == minted.run.run_id
    assert minted_key not in resolver._bindings

    # 3. register persists the config snapshot but must not cache minted bindings
    async with factory() as session:
        await RunLifecycleRepository(session).create_run(
            minted.run, organization_id=ORG_A
        )
        await resolver.register(
            session,
            binding=minted,
            organization_id=ORG_A,
            config_snapshot_id=f"{minted.run.run_id}:config",
            pinned_versions=_pinned_versions(minted),
        )
        await RunLifecycleRepository(session).create_run(
            operator_binding.run, organization_id=ORG_A
        )
        await resolver.register(
            session,
            binding=operator_binding,
            organization_id=ORG_A,
            config_snapshot_id=f"{operator_binding.run.run_id}:config",
            pinned_versions=_pinned_versions(operator_binding),
        )
        await session.commit()

    assert minted_key not in resolver._bindings
    assert ("op-regular", "1") in resolver._bindings

    # Snapshot for minted run is still durable in DB
    async with factory() as session:
        snap = await RunConfigSnapshotRepository(session).get_by_run(
            minted.run.run_id, organization_id=ORG_A
        )
        assert snap is not None

    # 4. warm_from_snapshots skips minted rows and caches only operator rows
    cold_resolver = PersistedExecutionAuthorityResolver(
        session_factory=factory, clock=lambda: MINT_NOW
    )
    async with factory() as session:
        warmed_count = await cold_resolver.warm_from_snapshots(session)
    assert warmed_count == 1
    assert minted_key not in cold_resolver._bindings
    assert ("op-regular", "1") in cold_resolver._bindings


@pytest.mark.asyncio
async def test_foreign_template_unavailable(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="org-a-tpl", authority_version="1", org_id=ORG_A
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(
        session_factory=factory, clock=lambda: MINT_NOW
    )
    with pytest.raises(ExecutionAuthorityUnavailableError) as foreign_exc:
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(
                authority_id="org-a-tpl", authority_version="1"
            ),
            organization_id=ORG_B,
            user_id="user-b",
            request_id="req-1",
        )
    with pytest.raises(ExecutionAuthorityUnavailableError) as missing_exc:
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(
                authority_id="does-not-exist", authority_version="1"
            ),
            organization_id=ORG_B,
            user_id="user-b",
            request_id="req-1",
        )
    assert type(foreign_exc.value) is type(missing_exc.value)
    assert foreign_exc.value.code == missing_exc.value.code
    assert str(foreign_exc.value) == str(missing_exc.value)

    async with factory() as session:
        repo = ExecutionAuthorityRepository(session)
        assert (
            await repo.get_template(
                authority_id="org-a-tpl",
                authority_version="1",
                organization_id=ORG_B,
            )
            is None
        )
        assert (
            await repo.get(
                authority_id="org-a-tpl",
                authority_version="1",
                organization_id=ORG_B,
            )
            is None
        )
        rows = (await session.execute(select(AgentExecutionAuthority))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_remint_same_request_loads_existing_row(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(authority_id="tpl-remint", authority_version="1")
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    first = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-remint", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-same",
        now=MINT_NOW,
    )
    # Retry 5 minutes later (still before first.deadline): loads original row!
    second = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-remint", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-same",
        now=MINT_NOW + timedelta(minutes=5),
    )

    assert second.authority_version == first.authority_version
    assert second.run.run_id == first.run.run_id
    assert second.deadline == first.deadline
    assert second.run.created_at == first.run.created_at

    async with factory() as session:
        rows = (await session.execute(select(AgentExecutionAuthority))).scalars().all()
        assert len(rows) == 2  # 1 template + 1 minted row


@pytest.mark.asyncio
async def test_remint_fails_closed_when_org_filtered_reload_returns_none(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="tpl-collision", authority_version="1"
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    # Pre-seed the derived (authority_id, authority_version) under ORG_B so
    # ORG_A's create hits the global unique constraint and org-filtered reload
    # returns None — must fail closed with no retry.
    minted_for_a = mint_run_binding(
        template, user_id="user-1", request_id="req-collide", now=MINT_NOW
    )
    foreign_row_binding = dataclasses.replace(
        minted_for_a,
        run=minted_for_a.run.model_copy(update={"tenant_id": ORG_B}),
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            foreign_row_binding, organization_id=ORG_B, is_template=False
        )
        await session.commit()

    create_calls = 0
    original_create = ExecutionAuthorityRepository.create

    async def _counting_create(
        self: ExecutionAuthorityRepository, *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal create_calls
        create_calls += 1
        return await original_create(self, *args, **kwargs)

    monkeypatch.setattr(ExecutionAuthorityRepository, "create", _counting_create)

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    with pytest.raises(ExecutionAuthorityUnavailableError):
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(
                authority_id="tpl-collision", authority_version="1"
            ),
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-collide",
            now=MINT_NOW,
        )
    assert create_calls == 1


@pytest.mark.asyncio
async def test_retry_after_failed_admission_past_deadline_returns_request_id_expired(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="tpl-expire", authority_version="1", delta=timedelta(minutes=10)
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    minted = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-expire", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-orphan",
        now=MINT_NOW,
    )

    # First admission failed before creating AgentRun; retry arrives past deadline.
    with pytest.raises(RequestIdExpiredError) as expired_exc:
        await resolver.mint_authority_from_template(
            ExecutionAuthorityReference(
                authority_id="tpl-expire", authority_version="1"
            ),
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-orphan",
            now=minted.deadline + timedelta(seconds=1),
        )
    assert expired_exc.value.code == "REQUEST_ID_EXPIRED"

    # Route maps RequestIdExpiredError to HTTP 422 (4xx, never 500).
    class _ExpiredBackend:
        async def start_research_execution(self, *args: Any, **kwargs: Any) -> str:
            raise expired_exc.value

    test_app = FastAPI()
    test_app.include_router(query_api.router)
    allow_authenticated(test_app)
    test_app.dependency_overrides[query_api.get_application_research_kernel] = lambda: (
        compose_application_research_kernel(_ExpiredBackend())
    )
    test_app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1", organization_id=ORG_A
    )
    response = TestClient(test_app).post(
        "/api/v1/query/research",
        json={"query": "retry expired request"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "REQUEST_ID_EXPIRED"}}


@pytest.mark.asyncio
async def test_replay_existing_run_past_deadline_still_succeeds(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="tpl-replay-old",
        authority_version="1",
        delta=timedelta(minutes=10),
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    minted = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(
            authority_id="tpl-replay-old", authority_version="1"
        ),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-admitted",
        now=MINT_NOW,
    )
    async with factory() as session:
        await RunLifecycleRepository(session).create_run(
            minted.run, organization_id=ORG_A
        )
        await session.commit()

    reloaded = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(
            authority_id="tpl-replay-old", authority_version="1"
        ),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-admitted",
        now=minted.deadline + timedelta(hours=2),
    )
    assert reloaded.run.run_id == minted.run.run_id


# --- 3. End-to-end integration with DirectExecutionService -------------------


class _CountingBridge:
    def __init__(self) -> None:
        self.admit_calls = 0
        self.execute_calls = 0

    def admit_execution_plan(self, _plan: Any) -> None:
        self.admit_calls += 1

    async def execute_execution_plan(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        await asyncio.sleep(0)
        return Mock(output={"result": "ok"}, workers_used=1)


def _matching_router() -> AsyncMock:
    router = AsyncMock()

    async def _route(*_args: Any, **_kwargs: Any) -> _RoutingDecisionStub:
        return _matching_proposal()

    router.route.side_effect = _route
    return router


@pytest.mark.asyncio
async def test_integration_distinct_request_ids_give_two_runs_same_gives_one(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="tpl-e2e", authority_version="1", delta=timedelta(hours=1)
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    bridge = _CountingBridge()
    service = DirectExecutionService(
        masr_router=_matching_router(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=factory,
        execution_authority_resolver=resolver,
    )

    try:
        tpl_ref = ExecutionAuthorityReference(
            authority_id="tpl-e2e", authority_version="1"
        )
        now = datetime.now(UTC)

        minted_1 = await resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-1",
            now=now,
        )
        exec_1 = await service.start_research_execution(
            make_project("Run 1"),
            authority_reference=ExecutionAuthorityReference(
                authority_id=minted_1.authority_id,
                authority_version=minted_1.authority_version,
            ),
            organization_id=ORG_A,
        )

        # Replay same request_id -> same run, same execution_id
        minted_1_retry = await resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-1",
            now=datetime.now(UTC),
        )
        exec_1_retry = await service.start_research_execution(
            make_project("Run 1 retry"),
            authority_reference=ExecutionAuthorityReference(
                authority_id=minted_1_retry.authority_id,
                authority_version=minted_1_retry.authority_version,
            ),
            organization_id=ORG_A,
        )
        assert exec_1_retry == exec_1

        # Distinct request_id -> second distinct run
        minted_2 = await resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-2",
            now=datetime.now(UTC),
        )
        exec_2 = await service.start_research_execution(
            make_project("Run 2"),
            authority_reference=ExecutionAuthorityReference(
                authority_id=minted_2.authority_id,
                authority_version=minted_2.authority_version,
            ),
            organization_id=ORG_A,
        )

        assert exec_2 != exec_1
        assert minted_2.run.run_id != minted_1.run.run_id
        assert bridge.admit_calls == 2

        # Minted bindings must never populate the in-process cache
        assert resolver._bindings == {}

        async with factory() as session:
            runs = (await session.execute(select(AgentRun))).scalars().all()
            assert {r.run_id for r in runs} == {
                minted_1.run.run_id,
                minted_2.run.run_id,
            }
    finally:
        await service.close()


# --- 4. Review-hardening tests (roles, template execution guard, validation, SQL filter, cap retry) ---


@pytest.mark.asyncio
async def test_load_authority_rejects_template_row_as_runnable_authority(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(
        authority_id="tpl-not-runnable", authority_version="1"
    )
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    tpl_ref = ExecutionAuthorityReference(
        authority_id="tpl-not-runnable", authority_version="1"
    )

    loaded = await resolver.load_authority(tpl_ref, organization_id=ORG_A)
    assert loaded is None
    assert ("tpl-not-runnable", "1") not in resolver._bindings

    service = DirectExecutionService(
        masr_router=_matching_router(),
        supervisor_bridge=_CountingBridge(),
        supervisor_factory=Mock(),
        session_factory=factory,
        execution_authority_resolver=resolver,
    )
    try:
        with pytest.raises(ExecutionAuthorityUnavailableError):
            await service.start_research_execution(
                make_project("Direct template attempt"),
                authority_reference=tpl_ref,
                organization_id=ORG_A,
            )
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_validate_template_binding_forbids_non_created_and_mint_prefix(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    base = _make_template_binding(authority_id="tpl-val", authority_version="1")
    queued = dataclasses.replace(
        base,
        run=base.run.transition_to(
            RunStatus.QUEUED, at=base.run.created_at + timedelta(seconds=1)
        ),
    )
    mint_prefixed = dataclasses.replace(
        base, authority_version="mint:00000000-0000-0000-0000-000000000001"
    )

    assert validate_template_binding(base) == TEMPLATE_DELTA

    with pytest.raises(ValueError, match="created"):
        validate_template_binding(queued)
    with pytest.raises(ValueError, match="created"):
        mint_run_binding(queued, user_id="user-1", request_id="req-1", now=MINT_NOW)

    with pytest.raises(ValueError, match="mint:"):
        validate_template_binding(mint_prefixed)
    with pytest.raises(ValueError, match="mint:"):
        mint_run_binding(
            mint_prefixed, user_id="user-1", request_id="req-1", now=MINT_NOW
        )

    async with factory() as session:
        repo = ExecutionAuthorityRepository(session)
        with pytest.raises(ValueError, match="created"):
            await repo.create(queued, organization_id=ORG_A, is_template=True)
        with pytest.raises(ValueError, match="mint:"):
            await repo.create(mint_prefixed, organization_id=ORG_A, is_template=True)


@pytest.mark.asyncio
async def test_warm_from_snapshots_limit_skips_minted_in_sql(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(authority_id="tpl-limit", authority_version="1")
    minted = mint_run_binding(
        template, user_id="user-1", request_id="req-old", now=MINT_NOW
    )
    operator_binding = dataclasses.replace(
        _make_template_binding(authority_id="op-second", authority_version="1"),
        run=_make_template_binding(
            authority_id="op-second", authority_version="1"
        ).run.model_copy(update={"run_id": "op-run-second", "idempotency_key": "op-2"}),
    )

    writer = PersistedExecutionAuthorityResolver(session_factory=factory)
    async with factory() as session:
        # Insert minted snapshot FIRST (oldest), then operator snapshot SECOND.
        await RunLifecycleRepository(session).create_run(
            minted.run, organization_id=ORG_A
        )
        await writer.register(
            session,
            binding=minted,
            organization_id=ORG_A,
            config_snapshot_id=f"{minted.run.run_id}:config",
            pinned_versions=_pinned_versions(minted),
        )
        await RunLifecycleRepository(session).create_run(
            operator_binding.run, organization_id=ORG_A
        )
        await writer.register(
            session,
            binding=operator_binding,
            organization_id=ORG_A,
            config_snapshot_id=f"{operator_binding.run.run_id}:config",
            pinned_versions=_pinned_versions(operator_binding),
        )
        await session.commit()

    cold = PersistedExecutionAuthorityResolver(session_factory=factory)
    async with factory() as session:
        warmed = await cold.warm_from_snapshots(session, limit=1)
    assert warmed == 1
    assert ("op-second", "1") in cold._bindings
    assert (minted.authority_id, minted.authority_version) not in cold._bindings


@pytest.mark.asyncio
async def test_mint_uses_write_session_factory_separate_from_read_factory(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sqlite_factory
    template = _make_template_binding(authority_id="tpl-split", authority_version="1")
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await session.commit()

    read_calls = 0
    write_calls = 0

    def _read_factory() -> AsyncSession:
        nonlocal read_calls
        read_calls += 1
        return factory()

    def _write_factory() -> AsyncSession:
        nonlocal write_calls
        write_calls += 1
        return factory()

    resolver = PersistedExecutionAuthorityResolver(
        session_factory=_read_factory,
        write_session_factory=_write_factory,
    )
    minted = await resolver.mint_authority_from_template(
        ExecutionAuthorityReference(authority_id="tpl-split", authority_version="1"),
        organization_id=ORG_A,
        user_id="user-1",
        request_id="req-split",
        now=MINT_NOW,
    )
    assert write_calls == 1
    assert read_calls == 0

    loaded = await resolver.load_authority(
        ExecutionAuthorityReference(
            authority_id=minted.authority_id,
            authority_version=minted.authority_version,
        ),
        organization_id=ORG_A,
    )
    assert loaded is not None
    assert read_calls == 1


def test_main_lifespan_wires_durable_write_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from src.api import main
    from src.api.services import (
        agent_execution_service,
        component_catalog,
        direct_execution_service,
        masr_routing_service,
        outbox_relay,
        supervisor_coordination_service,
        talkhier_session_service,
    )
    from src.core.kernel import TypedRegistry
    from src.models.db import session as db_session

    class _DummySession:
        async def __aenter__(self) -> _DummySession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    durable_factory = Mock(side_effect=lambda: _DummySession())
    maint_factory = Mock(side_effect=lambda: _DummySession())

    class _Runtime:
        router = Mock()

        async def close(self) -> None:
            return None

    class _ServiceStub:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def close(self) -> None:
            return None

    exec_service = DirectExecutionService(
        masr_router=Mock(),
        supervisor_bridge=Mock(),
        supervisor_factory=Mock(),
    )
    exec_service.restore_active_executions = AsyncMock(return_value=0)  # type: ignore[method-assign]
    exec_service.start_expired_run_reconciliation = Mock()  # type: ignore[method-assign]

    monkeypatch.setattr(main.MASRRuntime, "create", AsyncMock(return_value=_Runtime()))
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.settings, "EVALUATION_DISPATCH_ENABLED", False)
    monkeypatch.setattr(main.event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(main.event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(db_session, "close_db", AsyncMock())
    monkeypatch.setattr(db_session, "get_session_factory", lambda: durable_factory)
    monkeypatch.setattr(
        db_session, "get_maintenance_session_factory", lambda: maint_factory
    )
    monkeypatch.setattr(
        PersistedExecutionAuthorityResolver,
        "warm_from_snapshots",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        outbox_relay,
        "OutboxRelay",
        lambda **_kwargs: SimpleNamespace(start=Mock(), stop=AsyncMock()),
    )
    monkeypatch.setattr(
        component_catalog, "build_application_component_registry", TypedRegistry
    )
    monkeypatch.setattr(
        direct_execution_service,
        "configure_direct_execution_service",
        lambda **_kwargs: exec_service,
    )
    monkeypatch.setattr(
        agent_execution_service,
        "configure_agent_execution_service",
        lambda **_kwargs: AsyncMock(),
    )
    monkeypatch.setattr(masr_routing_service, "MASRRoutingService", _ServiceStub)
    monkeypatch.setattr(
        talkhier_session_service, "TalkHierSessionService", _ServiceStub
    )
    monkeypatch.setattr(
        supervisor_coordination_service,
        "SupervisorCoordinationService",
        _ServiceStub,
    )

    test_app = FastAPI(lifespan=main.lifespan)
    with TestClient(test_app):
        resolver = exec_service.execution_authority_resolver
        assert isinstance(resolver, PersistedExecutionAuthorityResolver)
        assert resolver._session_factory is maint_factory
        assert resolver._write_session_factory is durable_factory


@pytest.mark.asyncio
async def test_pre_deadline_retry_after_cap_refusal_succeeds(
    sqlite_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = sqlite_factory
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", 1)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", 100)

    template = _make_template_binding(
        authority_id="tpl-cap-retry", authority_version="1", delta=timedelta(hours=1)
    )
    now = datetime.now(UTC)
    blocker_binding = _make_template_binding(
        authority_id="blocker", authority_version="1"
    )
    blocker_run = blocker_binding.run.model_copy(
        update={
            "run_id": "blocker-run",
            "idempotency_key": "blocker-key",
            "status": RunStatus.RUNNING,
            "created_at": now - timedelta(seconds=10),
            "updated_at": now - timedelta(seconds=5),
            "started_at": now - timedelta(seconds=5),
        }
    )

    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            template, organization_id=ORG_A, is_template=True
        )
        await RunLifecycleRepository(session).create_run(
            blocker_run, organization_id=ORG_A
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    bridge = _CountingBridge()
    service = DirectExecutionService(
        masr_router=_matching_router(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=factory,
        execution_authority_resolver=resolver,
    )

    try:
        tpl_ref = ExecutionAuthorityReference(
            authority_id="tpl-cap-retry", authority_version="1"
        )
        minted = await resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-cap-1",
            now=now,
        )
        minted_ref = ExecutionAuthorityReference(
            authority_id=minted.authority_id,
            authority_version=minted.authority_version,
        )
        with pytest.raises(RunCapExceededError):
            await service.start_research_execution(
                make_project("Blocked first attempt"),
                authority_reference=minted_ref,
                organization_id=ORG_A,
            )

        # Clear the concurrent cap before deadline by completing the blocking run.
        async with factory() as session:
            finished_blocker = blocker_run.transition_to(
                RunStatus.SUCCEEDED, at=datetime.now(UTC)
            )
            await RunLifecycleRepository(session).record_run_transition(
                finished_blocker, organization_id=ORG_A
            )
            await session.commit()

        # Retry same request_id before deadline -> reloads orphan row and admits cleanly.
        reloaded = await resolver.mint_authority_from_template(
            tpl_ref,
            organization_id=ORG_A,
            user_id="user-1",
            request_id="req-cap-1",
            now=datetime.now(UTC),
        )
        assert reloaded.run.run_id == minted.run.run_id
        exec_id = await service.start_research_execution(
            make_project("Succeeded retry"),
            authority_reference=ExecutionAuthorityReference(
                authority_id=reloaded.authority_id,
                authority_version=reloaded.authority_version,
            ),
            organization_id=ORG_A,
        )
        assert isinstance(exec_id, str)
        async with factory() as session:
            admitted_run = await RunLifecycleRepository(session).get_run(
                minted.run.run_id, organization_id=ORG_A
            )
            assert admitted_run is not None
        assert bridge.admit_calls == 2
    finally:
        await service.close()
