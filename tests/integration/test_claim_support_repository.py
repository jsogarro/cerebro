"""Integration tests for ``ClaimSupportRepository`` against real Postgres.

Proves the repository — not the caller — computes ``evidence_count``, and
that a re-evaluation of the same claim appends a new row rather than
replacing the old one.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    PromptBinding,
)
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

BINDING = PromptBinding(
    prompt_id="prompt-2",
    prompt_version="1.0",
    template_sha256="d" * 64,
    rendered_sha256="e" * 64,
)


def _make_claim(**overrides: object) -> ClaimSupport:
    values: dict[str, object] = {
        "claim_support_id": "claim-support-1",
        "run_id": "run-1",
        "artifact_id": "artifact-1",
        "claim_id": "claim-1",
        "claim_text": "The model improves accuracy by 5%.",
        "status": ClaimSupportStatus.SUPPORTED,
        "evidence_ids": ("evidence-1", "evidence-2"),
        "evaluator_id": "evaluator-1",
        "evaluator_version": "1.0",
        "prompt_binding": BINDING,
        "explanation": "Table 3 reports a 5% accuracy gain.",
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return ClaimSupport(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> ClaimSupportRepository:
    return ClaimSupportRepository(db_session)


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)
    await seed_artifact(db_session, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_record_claim_support_computes_evidence_count(
    repo: ClaimSupportRepository,
) -> None:
    row = await repo.record_claim_support(_make_claim(), organization_id=ORG_ID)

    assert row.evidence_count == 2
    assert row.evidence_ids == ["evidence-1", "evidence-2"]


@pytest.mark.asyncio
async def test_evidence_count_tracks_the_contract_s_evidence_ids_not_a_caller_value(
    repo: ClaimSupportRepository,
) -> None:
    """There is no parameter on ``record_claim_support`` a caller could use to
    supply a mismatched count — this test documents that by construction:
    changing the number of ``evidence_ids`` is the only way to change
    ``evidence_count``, and the two always move together."""
    single_evidence = await repo.record_claim_support(
        _make_claim(
            claim_support_id="claim-support-single", evidence_ids=("evidence-1",)
        ),
        organization_id=ORG_ID,
    )
    no_evidence = await repo.record_claim_support(
        _make_claim(
            claim_support_id="claim-support-none",
            claim_id="claim-2",
            status=ClaimSupportStatus.UNSUPPORTED,
            evidence_ids=(),
            absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED,
        ),
        organization_id=ORG_ID,
    )

    assert single_evidence.evidence_count == 1
    assert no_evidence.evidence_count == 0


@pytest.mark.asyncio
async def test_record_claim_support_fails_closed_without_an_org_context(
    repo: ClaimSupportRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.record_claim_support(_make_claim(), organization_id=None)


@pytest.mark.asyncio
async def test_record_claim_support_rejects_a_mismatched_tenant(
    repo: ClaimSupportRepository,
) -> None:
    with pytest.raises(TenantMismatchError):
        await repo.record_claim_support(_make_claim(), organization_id=OTHER_ORG_ID)


@pytest.mark.asyncio
async def test_a_reevaluation_appends_a_new_row(repo: ClaimSupportRepository) -> None:
    await repo.record_claim_support(_make_claim(), organization_id=ORG_ID)
    await repo.record_claim_support(
        _make_claim(
            claim_support_id="claim-support-2",
            status=ClaimSupportStatus.DISPUTED,
            evaluated_at=LATER,
        ),
        organization_id=ORG_ID,
    )

    rows = await repo.list_claim_supports_for_claim("claim-1", organization_id=ORG_ID)

    assert [row.claim_support_id for row in rows] == [
        "claim-support-1",
        "claim-support-2",
    ]
    assert [row.status for row in rows] == ["supported", "disputed"]
