"""Integration tests for ``ToolInvocationRepository`` against real Postgres."""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityDenialReason,
    CapabilityGrant,
    SensitivityClass,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.tenant_scope import TenantMismatchError
from src.repositories.tool_invocation_repository import ToolInvocationRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

ALLOW_DECISION = CapabilityDecision(
    effect=CapabilityDecisionEffect.ALLOW,
    request_fingerprint="a" * 64,
    grant_id="grant-1",
    decided_at=NOW,
)
DENY_DECISION = CapabilityDecision(
    effect=CapabilityDecisionEffect.DENY,
    request_fingerprint="a" * 64,
    denial_reason=CapabilityDenialReason.NO_MATCHING_GRANT,
    decided_at=NOW,
)


def _make_invocation(**overrides: object) -> ToolInvocation:
    values: dict[str, object] = {
        "tool_invocation_id": "invocation-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tool_name": "academic_search",
        "tool_version": "1.0",
        "status": ToolInvocationStatus.SUCCEEDED,
        "capability_scope": "search",
        "idempotency_key": "invocation-key-1",
        "input": {"query": "graph neural networks"},
        "input_trust": TrustClassification.USER_SUPPLIED,
        "output": {"results": []},
        "output_trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "requested_at": NOW,
        "completed_at": NOW,
    }
    values.update(overrides)
    return ToolInvocation(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> ToolInvocationRepository:
    return ToolInvocationRepository(db_session)


@pytest_asyncio.fixture(name="seeded_run", autouse=True)
async def seeded_run_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)

    # `ALLOW_DECISION` names `grant-1`; the FK from
    # `agent_tool_invocations.capability_grant_id` requires the row to exist.
    capability_repo = CapabilityRepository(db_session)
    grant = CapabilityGrant(
        grant_id="grant-1",
        run_id="run-1",
        task_id="task-1",
        capability_scope="search",
        tool_name="academic_search",
        tool_versions=("1.0",),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.USER_SUPPLIED,
        requires_approval=False,
        issued_at=NOW,
        expires_at=NOW.replace(year=NOW.year + 1),
    )
    await capability_repo.create_grant(grant, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_create_tool_invocation_persists_the_contract_fields(
    repo: ToolInvocationRepository,
) -> None:
    row = await repo.create_tool_invocation(
        _make_invocation(),
        organization_id=ORG_ID,
        capability_decision=ALLOW_DECISION,
    )

    assert row.tool_invocation_id == "invocation-1"
    assert row.organization_id == ORG_ID
    assert row.capability_decision_effect == "allow"
    assert row.capability_grant_id == "grant-1"


@pytest.mark.asyncio
async def test_a_denied_decision_requires_a_denied_status(
    repo: ToolInvocationRepository,
) -> None:
    """Proves ``ck_agent_tool_invocation_capability_denial`` at the database
    level: a `DENY` decision cannot be paired with a non-denied invocation."""
    invocation = _make_invocation(
        status=ToolInvocationStatus.SUCCEEDED, output={"results": []}
    )

    with pytest.raises(IntegrityError):
        await repo.create_tool_invocation(
            invocation, organization_id=ORG_ID, capability_decision=DENY_DECISION
        )


@pytest.mark.asyncio
async def test_a_denied_decision_with_a_denied_status_is_accepted(
    repo: ToolInvocationRepository,
) -> None:
    invocation = _make_invocation(
        status=ToolInvocationStatus.DENIED,
        error_code="capability_denied",
        output=None,
        output_trust=None,
    )

    row = await repo.create_tool_invocation(
        invocation, organization_id=ORG_ID, capability_decision=DENY_DECISION
    )

    assert row.status == ToolInvocationStatus.DENIED.value
    assert row.capability_decision_effect == "deny"
    assert row.capability_grant_id is None


@pytest.mark.asyncio
async def test_an_attempt_cannot_reuse_an_invocation_idempotency_key(
    repo: ToolInvocationRepository,
) -> None:
    await repo.create_tool_invocation(
        _make_invocation(), organization_id=ORG_ID, capability_decision=ALLOW_DECISION
    )

    with pytest.raises(IntegrityError):
        await repo.create_tool_invocation(
            _make_invocation(tool_invocation_id="invocation-2"),
            organization_id=ORG_ID,
            capability_decision=ALLOW_DECISION,
        )


@pytest.mark.asyncio
async def test_record_transition_updates_the_existing_row(
    repo: ToolInvocationRepository,
) -> None:
    await repo.create_tool_invocation(
        _make_invocation(
            status=ToolInvocationStatus.RUNNING,
            output=None,
            output_trust=None,
            completed_at=None,
        ),
        organization_id=ORG_ID,
        capability_decision=ALLOW_DECISION,
    )

    updated = _make_invocation(status=ToolInvocationStatus.SUCCEEDED)
    row = await repo.record_transition(updated, organization_id=ORG_ID)

    assert row.status == ToolInvocationStatus.SUCCEEDED.value
    assert row.output == {"results": []}


@pytest.mark.asyncio
async def test_record_transition_rejects_a_mismatched_tenant(
    repo: ToolInvocationRepository,
) -> None:
    await repo.create_tool_invocation(
        _make_invocation(), organization_id=ORG_ID, capability_decision=ALLOW_DECISION
    )

    with pytest.raises(TenantMismatchError):
        await repo.record_transition(_make_invocation(), organization_id=OTHER_ORG_ID)
