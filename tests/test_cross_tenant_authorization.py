"""Cross-tenant authorization on the project, authority, and execution reads.

Three surfaces used to authorize by presence of *any* authenticated caller
rather than by tenant:

* ``verify_project_access`` returned true for every authenticated user and
  every project, so any account could subscribe to any project's WebSocket.
* the execution authority resolver was keyed on a client-supplied
  ``(authority_id, authority_version)`` pair alone, and the durable write path
  takes its ``organization_id`` from the resolved binding — so resolving
  another tenant's reference ran and persisted work as that tenant.
* the execution status/results reads took an execution id and nothing else,
  from an endpoint that required no authentication at all.

Every test here asserts denial by tenant, not merely by authentication. A
cross-tenant caller must be answered exactly like a caller asking for
something that does not exist, so the id spaces cannot be probed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.api.auth.auth_router import provisioned_organization_id
from src.api.routes import query_api
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    MappingExecutionAuthorityResolver,
    PersistedExecutionAuthorityResolver,
)
from src.api.websocket.auth import verify_project_access
from src.core.config import settings
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
)
from src.middleware.tenant_context import TenantContext
from src.models.db.research_project import ProjectStatus, ResearchProject
from src.models.db.user import User
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
# User identifiers are UUIDs: research_projects.user_id is a uuid column with a
# foreign key to users.id, so a value like "user-a" is one the database cannot
# hold. Only their distinctness matters to anything asserted below.
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

AUTHORITY_REFERENCE = ExecutionAuthorityReference(
    authority_id="authority-a", authority_version="1"
)


@pytest.fixture(autouse=True)
def _production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the anonymous-development opt-in out of every test but its own."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", False)


@pytest_asyncio.fixture
async def project_session() -> AsyncGenerator[AsyncSession, None]:
    """SQLite session holding only the projects table.

    Deliberately narrower than the shared ``db_session`` fixture: importing
    this module registers models that use Postgres-only column types, and
    creating the whole metadata on SQLite fails on those.
    """
    from src.models.db.agent_task import AgentTask
    from src.models.db.research_result import ResearchResult
    from src.models.db.workflow_checkpoint import WorkflowCheckpoint

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        for model in (ResearchProject, AgentTask, ResearchResult, WorkflowCheckpoint):
            await connection.run_sync(model.__table__.create)

    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def _authority_binding(tenant_id: str) -> ExecutionAuthorityBinding:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    binding = ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-a",
        authority_version="1",
        run_id="run-a",
        workflow_definition_id="workflow-a",
        routing_policy_id="policy-a",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-a",
        supervisor_type="research",
        workers=(),
        edges=(),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=0,
            max_total_tokens=1,
            max_tool_invocations=0,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now.replace(year=2027),
        compiled_at=now,
    )
    return ExecutionAuthorityBinding(
        **{
            **binding.__dict__,
            "run": binding.run.model_copy(update={"tenant_id": tenant_id}),
        }
    )


async def _seed_project(
    session: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
) -> uuid.UUID:
    project = ResearchProject(
        title="Confidential project",
        query='{"text": "confidential", "domains": ["ai"]}',
        domains=["ai"],
        status=ProjectStatus.IN_PROGRESS,
        user_id=user_id,
        organization_id=uuid.UUID(organization_id),
        project_metadata={},
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project.id


# --- Defect 1: WebSocket project access ----------------------------------


@pytest.mark.asyncio
async def test_project_owner_may_subscribe(project_session: AsyncSession) -> None:
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            USER_A,
            str(project_id),
            organization_id=ORG_A,
            session=project_session,
        )
        is True
    )


@pytest.mark.asyncio
async def test_another_tenant_may_not_subscribe_to_a_project(
    project_session: AsyncSession,
) -> None:
    """The defect: any authenticated user was authorized for any project."""
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            USER_B,
            str(project_id),
            organization_id=ORG_B,
            session=project_session,
        )
        is False
    )


@pytest.mark.asyncio
async def test_same_user_id_in_another_organization_may_not_subscribe(
    project_session: AsyncSession,
) -> None:
    """Holding the owner's user id is not enough without the owner's tenant."""
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            USER_A,
            str(project_id),
            organization_id=ORG_B,
            session=project_session,
        )
        is False
    )


@pytest.mark.asyncio
async def test_another_user_in_the_same_organization_may_not_subscribe(
    project_session: AsyncSession,
) -> None:
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            USER_B,
            str(project_id),
            organization_id=ORG_A,
            session=project_session,
        )
        is False
    )


@pytest.mark.asyncio
async def test_missing_tenant_claim_denies_project_access(
    project_session: AsyncSession,
) -> None:
    """No org claim is a hard denial, never a silently unscoped lookup."""
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            USER_A,
            str(project_id),
            organization_id=None,
            session=project_session,
        )
        is False
    )


@pytest.mark.asyncio
async def test_missing_session_denies_project_access() -> None:
    assert (
        await verify_project_access(
            USER_A,
            str(uuid.uuid4()),
            organization_id=ORG_A,
            session=None,
        )
        is False
    )


@pytest.mark.asyncio
async def test_anonymous_caller_denied_project_access(
    project_session: AsyncSession,
) -> None:
    project_id = await _seed_project(
        project_session, user_id=USER_A, organization_id=ORG_A
    )

    assert (
        await verify_project_access(
            None,
            str(project_id),
            organization_id=ORG_A,
            session=project_session,
        )
        is False
    )


@pytest.mark.asyncio
async def test_explicit_development_opt_in_still_allows_project_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deliberate local-development opt-in keeps its exact semantics."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", True)

    assert (
        await verify_project_access(
            None,
            str(uuid.uuid4()),
            organization_id=None,
            session=None,
        )
        is True
    )


# --- Defect 2: execution authority resolution ----------------------------


def test_owning_tenant_resolves_its_authority() -> None:
    resolver = MappingExecutionAuthorityResolver(
        {("authority-a", "1"): _authority_binding(ORG_A)}
    )

    binding = resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_A)

    assert binding.run.tenant_id == ORG_A


def test_another_tenant_cannot_resolve_a_known_authority_reference() -> None:
    """The defect: knowing the id/version pair was the whole authorization."""
    resolver = MappingExecutionAuthorityResolver(
        {("authority-a", "1"): _authority_binding(ORG_A)}
    )

    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_B)


def test_missing_tenant_claim_cannot_resolve_an_authority() -> None:
    resolver = MappingExecutionAuthorityResolver(
        {("authority-a", "1"): _authority_binding(ORG_A)}
    )

    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(AUTHORITY_REFERENCE, organization_id=None)


def test_cross_tenant_and_unknown_authority_are_indistinguishable() -> None:
    """A denial must not reveal that another tenant's authority exists."""
    resolver = MappingExecutionAuthorityResolver(
        {("authority-a", "1"): _authority_binding(ORG_A)}
    )
    unknown = ExecutionAuthorityReference(
        authority_id="no-such-authority", authority_version="1"
    )

    with pytest.raises(ExecutionAuthorityUnavailableError) as cross_tenant:
        resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_B)
    with pytest.raises(ExecutionAuthorityUnavailableError) as not_found:
        resolver.resolve(unknown, organization_id=ORG_B)

    assert str(cross_tenant.value) == str(not_found.value)


def test_persisted_resolver_is_tenant_scoped() -> None:
    """A warmed cache spans tenants; the boundary is applied on the way out."""
    resolver = PersistedExecutionAuthorityResolver()
    resolver.cache_binding(_authority_binding(ORG_A))

    assert (
        resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_A).run.tenant_id
        == ORG_A
    )
    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_B)


def test_tenant_identity_is_compared_by_uuid_not_by_formatting() -> None:
    resolver = MappingExecutionAuthorityResolver(
        {("authority-a", "1"): _authority_binding(ORG_A.upper())}
    )

    assert resolver.resolve(AUTHORITY_REFERENCE, organization_id=ORG_A) is not None


def _service_with_resolver(
    resolver: MappingExecutionAuthorityResolver,
) -> DirectExecutionService:
    service = DirectExecutionService.__new__(DirectExecutionService)
    service.closed = False
    service.execution_authority_resolver = resolver
    service.active_executions = {}
    return service


@pytest.mark.asyncio
async def test_starting_an_execution_for_another_tenants_authority_is_refused() -> None:
    """Nothing is routed, admitted, or persisted for a foreign authority."""
    service = _service_with_resolver(
        MappingExecutionAuthorityResolver(
            {("authority-a", "1"): _authority_binding(ORG_A)}
        )
    )

    with pytest.raises(ExecutionAuthorityUnavailableError):
        await service.start_research_execution(
            object(),
            authority_reference=AUTHORITY_REFERENCE,
            organization_id=ORG_B,
        )

    assert service.active_executions == {}


@pytest.mark.asyncio
async def test_starting_an_execution_without_a_tenant_claim_is_refused() -> None:
    service = _service_with_resolver(
        MappingExecutionAuthorityResolver(
            {("authority-a", "1"): _authority_binding(ORG_A)}
        )
    )

    with pytest.raises(ExecutionAuthorityUnavailableError):
        await service.start_research_execution(
            object(),
            authority_reference=AUTHORITY_REFERENCE,
            organization_id=None,
        )

    assert service.active_executions == {}


# --- Defect 3: execution status and results reads ------------------------


def _service_with_execution() -> DirectExecutionService:
    service = DirectExecutionService.__new__(DirectExecutionService)
    service.active_executions = {
        "execution-a": ExecutionStatus(
            execution_id="execution-a",
            project_id="00000000-0000-0000-0000-0000000000aa",
            status="completed",
            organization_id=ORG_A,
            final_output={"secret": "org A confidential output"},
            agent_results={"research": {"finding": "org A confidential finding"}},
        )
    }
    return service


@pytest.mark.asyncio
async def test_owning_tenant_reads_its_execution() -> None:
    service = _service_with_execution()

    status = await service.get_execution_status("execution-a", organization_id=ORG_A)
    results = await service.get_execution_results("execution-a", organization_id=ORG_A)

    assert status is not None
    assert results == {"secret": "org A confidential output"}


@pytest.mark.asyncio
async def test_another_tenant_cannot_read_an_execution() -> None:
    """The defect: an execution id was the whole authorization for output."""
    service = _service_with_execution()

    assert await service.get_execution_status("execution-a", organization_id=ORG_B) is (
        None
    )
    assert (
        await service.get_execution_results("execution-a", organization_id=ORG_B)
        is None
    )


@pytest.mark.asyncio
async def test_missing_tenant_claim_cannot_read_an_execution() -> None:
    service = _service_with_execution()

    assert await service.get_execution_status("execution-a", organization_id=None) is (
        None
    )
    assert (
        await service.get_execution_results("execution-a", organization_id=None) is None
    )


@pytest.mark.asyncio
async def test_execution_with_no_recorded_owner_is_not_readable() -> None:
    """An unknown owner is not visible to everyone; it is visible to no one."""
    service = _service_with_execution()
    service.active_executions["execution-a"].organization_id = None

    assert await service.get_execution_status("execution-a", organization_id=ORG_A) is (
        None
    )


class _TenantScopedKernel:
    """Backend whose reads honour the caller's organization."""

    def __init__(self) -> None:
        self._service = _service_with_execution()

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> ExecutionStatus | None:
        return await self._service.get_execution_status(
            execution_id, organization_id=organization_id
        )

    async def get_execution_results(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        return await self._service.get_execution_results(
            execution_id, organization_id=organization_id
        )

    async def start_research_execution(
        self, project: Any, context: Any = None, **kwargs: Any
    ) -> str:
        raise AssertionError("not exercised")

    async def resume_execution(self, project_id: Any) -> str | None:
        return None


@pytest.mark.asyncio
async def test_status_endpoint_hides_another_tenants_execution() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await query_api.get_execution_status(
            "execution-a",
            _TenantScopedKernel(),
            TenantContext(user_id=USER_B, organization_id=ORG_B),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_results_endpoint_hides_another_tenants_execution() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await query_api.get_execution_results(
            "execution-a",
            _TenantScopedKernel(),
            TenantContext(user_id=USER_B, organization_id=ORG_B),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_status_and_results_endpoints_serve_the_owning_tenant() -> None:
    owner = TenantContext(user_id=USER_A, organization_id=ORG_A)

    status = await query_api.get_execution_status(
        "execution-a", _TenantScopedKernel(), owner
    )
    results = await query_api.get_execution_results(
        "execution-a", _TenantScopedKernel(), owner
    )

    assert status["status"] == "completed"
    assert results == {"secret": "org A confidential output"}


class _ForeignRunSessionFactory:
    """Session factory whose only run row belongs to another organization."""

    def __init__(self, run_organization_id: str) -> None:
        self.run_organization_id = run_organization_id
        self.rehydrated = False

    def __call__(self) -> _ForeignRunSessionFactory:
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_durable_admission_lookup_ignores_another_tenants_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign durable run must not be materialized into memory.

    ``_durably_recorded_execution_id`` copies ``final_output`` and
    ``agent_results`` off the durable record into ``active_executions``.
    Coalescing onto another tenant's run would hand that tenant's output back
    under a handle this caller holds.
    """
    import src.api.services.direct_execution_service as service_module

    class _Run:
        run_id = "run-a"
        organization_id = uuid.UUID(ORG_A)

    class _Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_run(self, run_id: str) -> _Run:
            return _Run()

    monkeypatch.setattr(service_module, "RunLifecycleRepository", _Repo)

    service = DirectExecutionService.__new__(DirectExecutionService)
    service.session_factory = _ForeignRunSessionFactory(ORG_A)
    service.active_executions = {}
    service._execution_ids_by_run_id = {}

    async def _fail_rehydrate(*args: object, **kwargs: object) -> None:
        raise AssertionError("a foreign run must not be rehydrated")

    service._rehydrate_execution = _fail_rehydrate  # type: ignore[method-assign]

    assert (
        await service._durably_recorded_execution_id("run-a", organization_id=ORG_B)
        is None
    )
    assert service.active_executions == {}


# --- Tenant organization provisioning ------------------------------------
#
# Every surface above fails closed without an ``organization_id`` claim, and
# before this change no live token carried one: register and login both called
# ``generate_token_pair`` without it and ``users.organization_id`` was never
# assigned. Provisioning it is what makes the authorization above enforceable
# rather than a blanket denial.


def test_registration_assigns_a_tenant_organization() -> None:
    user = User.create_with_password(
        email="new@example.com",
        username="new",
        password="irrelevant-for-this-assertion",
        organization_id=uuid.uuid4(),
    )

    assert provisioned_organization_id(user) == user.organization_id


def test_an_account_without_an_organization_is_given_one() -> None:
    """Accounts predating the tenant boundary get an organization on login."""
    user = User.create_with_password(
        email="legacy@example.com",
        username="legacy",
        password="irrelevant-for-this-assertion",
    )
    assert user.organization_id is None

    organization_id = provisioned_organization_id(user)

    assert isinstance(organization_id, uuid.UUID)
    assert user.organization_id == organization_id


def test_an_existing_organization_is_never_reassigned() -> None:
    """Provisioning is idempotent: a user's tenant does not move under it."""
    existing = uuid.uuid4()
    user = User.create_with_password(
        email="member@example.com",
        username="member",
        password="irrelevant-for-this-assertion",
        organization_id=existing,
    )

    assert provisioned_organization_id(user) == existing
    assert provisioned_organization_id(user) == existing


def test_the_free_text_organization_field_does_not_select_a_tenant() -> None:
    """Two accounts naming the same organization are still separate tenants.

    Deriving the tenant from client-supplied text would let anyone join an
    existing organization by typing its name.
    """
    first = User.create_with_password(
        email="a@example.com",
        username="a",
        password="irrelevant-for-this-assertion",
        organization="Acme",
        organization_id=uuid.uuid4(),
    )
    second = User.create_with_password(
        email="b@example.com",
        username="b",
        password="irrelevant-for-this-assertion",
        organization="Acme",
        organization_id=uuid.uuid4(),
    )

    assert provisioned_organization_id(first) != provisioned_organization_id(second)
