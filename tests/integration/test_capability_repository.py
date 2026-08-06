"""Integration tests for ``CapabilityRepository`` against real Postgres.

Reuses the shared testcontainers fixtures from ``tests/integration/conftest.py``
(``test_engine``, ``db_session``), matching ``test_run_lifecycle_repository.py``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    ApprovalRef,
    CapabilityGrant,
    SensitivityClass,
    TrustClassification,
)
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.tenant_scope import MissingOrganizationContextError
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _make_grant(**overrides: object) -> CapabilityGrant:
    values: dict[str, object] = {
        "grant_id": "grant-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "capability_scope": "search",
        "tool_name": "academic_search",
        "tool_versions": ("1.0",),
        "sensitivity": SensitivityClass.READ_ONLY,
        "max_input_trust": TrustClassification.USER_SUPPLIED,
        "requires_approval": False,
        "issued_at": NOW,
        "expires_at": LATER,
    }
    values.update(overrides)
    return CapabilityGrant(**values)


def _make_approval(**overrides: object) -> ApprovalRef:
    values: dict[str, object] = {
        "approval_id": "approval-1",
        "grant_id": "grant-1",
        "request_fingerprint": "a" * 64,
        "approved_by": "user-1",
        "approved_at": NOW,
        "expires_at": LATER,
    }
    values.update(overrides)
    return ApprovalRef(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> CapabilityRepository:
    return CapabilityRepository(db_session)


@pytest_asyncio.fixture(name="seeded_run")
async def seeded_run_fixture(db_session: AsyncSession) -> None:
    """A run with two tasks, so grants can be scoped per-task."""
    from src.core.contracts import Task, TaskStatus

    await seed_run_task_attempt(db_session, organization_id=ORG_ID)

    run_repo = RunLifecycleRepository(db_session)
    second_task = Task(
        task_id="task-2",
        run_id="run-1",
        task_key="second-task",
        task_type="research",
        objective="A second, independent task",
        idempotency_key="key-task-2",
        status=TaskStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    await run_repo.create_task(second_task, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_create_grant_persists_the_contract_fields(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    grant = _make_grant()

    row = await repo.create_grant(grant, organization_id=ORG_ID)

    assert row.grant_id == "grant-1"
    assert row.organization_id == ORG_ID
    assert row.sensitivity == SensitivityClass.READ_ONLY.value
    assert row.tool_versions == ["1.0"]


@pytest.mark.asyncio
async def test_create_grant_fails_closed_without_an_org_context(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.create_grant(_make_grant(), organization_id=None)


@pytest.mark.asyncio
async def test_a_sensitive_grant_that_waives_approval_is_rejected_at_construction(
    seeded_run: None,
) -> None:
    """The contract layer rejects this before it ever reaches the repository."""
    with pytest.raises(ValueError, match="cannot waive"):
        _make_grant(
            sensitivity=SensitivityClass.EXTERNAL_WRITE, requires_approval=False
        )


@pytest.mark.asyncio
async def test_the_database_backstops_a_waiving_grant_that_bypasses_the_contract(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    """Proves ``ck_agent_capability_grant_approval_required`` at the database level.

    The contract's own constructor validator already rejects a waiving grant
    (see the test above), so the only way to reach the database CHECK is to
    bypass Pydantic validation entirely via ``model_construct`` — the same
    technique 4A used to reach the decision-time approval backstop in
    ``decide_capability``. If the CHECK were ever dropped, this is the test
    that would go red.
    """
    invalid_grant = CapabilityGrant.model_construct(
        grant_id="grant-invalid",
        run_id="run-1",
        task_id="task-1",
        capability_scope="search",
        tool_name="academic_search",
        tool_versions=("1.0",),
        sensitivity=SensitivityClass.EXFILTRATION,
        max_input_trust=TrustClassification.USER_SUPPLIED,
        requires_approval=False,
        issued_at=NOW,
        expires_at=LATER,
    )

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await repo.create_grant(invalid_grant, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_create_approval_copies_the_tenant_from_the_grant(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    await repo.create_grant(_make_grant(), organization_id=ORG_ID)

    row = await repo.create_approval(_make_approval(), organization_id=ORG_ID)

    assert row.organization_id == ORG_ID


@pytest.mark.asyncio
async def test_create_approval_rejects_a_mismatched_tenant(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    from src.repositories.tenant_scope import TenantMismatchError

    await repo.create_grant(_make_grant(), organization_id=ORG_ID)

    with pytest.raises(TenantMismatchError):
        await repo.create_approval(_make_approval(), organization_id=OTHER_ORG_ID)


@pytest.mark.asyncio
async def test_a_grant_cannot_reuse_a_request_fingerprint(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    from sqlalchemy.exc import IntegrityError

    await repo.create_grant(_make_grant(), organization_id=ORG_ID)
    await repo.create_approval(_make_approval(), organization_id=ORG_ID)

    with pytest.raises(IntegrityError):
        await repo.create_approval(
            _make_approval(approval_id="approval-2"), organization_id=ORG_ID
        )


@pytest.mark.asyncio
async def test_list_grants_for_task_scopes_by_run_and_task(
    repo: CapabilityRepository, seeded_run: None
) -> None:
    await repo.create_grant(_make_grant(), organization_id=ORG_ID)
    await repo.create_grant(
        _make_grant(grant_id="grant-2", task_id="task-2"), organization_id=ORG_ID
    )

    grants = await repo.list_grants_for_task("run-1", "task-1", organization_id=ORG_ID)

    assert [g.grant_id for g in grants] == ["grant-1"]
