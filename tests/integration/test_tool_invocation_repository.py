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
async def test_nested_input_and_output_payloads_persist_and_read_back(
    repo: ToolInvocationRepository,
) -> None:
    """``JsonObject`` freezes recursively into ``MappingProxyType`` at every
    nesting level, not just the top one. A shallow ``dict(invocation.input)``
    only thaws the top level, leaving nested mappings as ``mappingproxy``
    objects asyncpg cannot serialize -- this round-trips genuinely nested
    input/output through a real INSERT and SELECT, which is the only place
    the defect actually surfaced (constructing the contract, and even
    computing its digest, both succeeded on the broken code)."""
    nested_input = {"query": "gnn", "filters": {"year": {"gte": 2020}}}
    nested_output = {"results": [{"id": 1, "meta": {"score": 0.9}}]}

    row = await repo.create_tool_invocation(
        _make_invocation(input=nested_input, output=nested_output),
        organization_id=ORG_ID,
        capability_decision=ALLOW_DECISION,
    )
    await repo.session.flush()

    fetched = await repo.get_tool_invocation("invocation-1", organization_id=ORG_ID)

    assert fetched is not None
    assert fetched.input == nested_input
    assert fetched.output == nested_output
    assert row.input == nested_input
    assert row.output == nested_output


@pytest.mark.asyncio
async def test_an_invalid_input_row_persists_with_no_capability_decision(
    repo: ToolInvocationRepository,
) -> None:
    """Input validation runs before authorization, and must: a request whose
    input never parses has nothing for decide_capability to decide about and
    no CapabilityRequest to fingerprint. capability_decision=None is legal
    for exactly this case."""
    invocation = _make_invocation(
        status=ToolInvocationStatus.FAILED,
        error_code="invalid_input",
        output=None,
        output_trust=None,
    )

    row = await repo.create_tool_invocation(
        invocation, organization_id=ORG_ID, capability_decision=None
    )
    await repo.session.flush()

    fetched = await repo.get_tool_invocation("invocation-1", organization_id=ORG_ID)

    assert fetched is not None
    assert fetched.capability_decision_effect is None
    assert fetched.capability_grant_id is None
    assert fetched.capability_approval_id is None
    assert fetched.capability_denial_reason is None
    assert fetched.request_fingerprint is None
    assert row.error_code == "invalid_input"


@pytest.mark.asyncio
async def test_a_missing_decision_is_rejected_for_any_other_error_code(
    repo: ToolInvocationRepository,
) -> None:
    """The repository fails closed itself, before ever reaching the
    database: `error_code='invalid_input'` is the only door through which a
    row may omit its capability decision."""
    invocation = _make_invocation(
        status=ToolInvocationStatus.FAILED,
        error_code="timeout",
        output=None,
        output_trust=None,
    )

    with pytest.raises(ValueError, match="invalid_input"):
        await repo.create_tool_invocation(
            invocation, organization_id=ORG_ID, capability_decision=None
        )


@pytest.mark.asyncio
async def test_the_database_backstops_a_missing_decision_that_bypasses_the_repository(
    repo: ToolInvocationRepository,
) -> None:
    """Proves ``ck_agent_tool_invocation_decision_pair_or_invalid_input``
    at the database level, not just the repository's fail-closed guard: bypass
    the repository's check the same way 4A's capability-grant backstop test
    bypasses the Pydantic constructor, by inserting a row directly rather than
    through ``create_tool_invocation``."""
    from src.models.db.tool_invocation import AgentToolInvocation

    row = AgentToolInvocation(
        tool_invocation_id="invocation-bypass",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        tool_name="academic_search",
        tool_version="1.0",
        status=ToolInvocationStatus.FAILED.value,
        capability_scope="search",
        idempotency_key="invocation-key-bypass",
        input={"query": "gnn"},
        input_trust=TrustClassification.USER_SUPPLIED.value,
        input_sha256="a" * 64,
        output=None,
        output_trust=None,
        output_sha256=None,
        capability_decision_effect=None,
        capability_grant_id=None,
        capability_approval_id=None,
        capability_denial_reason=None,
        request_fingerprint=None,
        producer_kind="system",
        error_code="timeout",
        requested_at=NOW,
        completed_at=NOW,
        organization_id=ORG_ID,
    )
    repo.session.add(row)

    with pytest.raises(IntegrityError):
        await repo.session.flush()


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
