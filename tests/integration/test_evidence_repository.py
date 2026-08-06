"""Integration tests for ``EvidenceRepository`` against real Postgres.

The evidence/artifact digest cross-check is the guarantee 4A named as its own
non-guarantee (``Evidence.content_sha256`` is not cross-checked against its
snapshot artifact's digest by the contract layer, and cannot be a database
CHECK either, since a CHECK constraint cannot see another table's columns) —
this is the one place it is enforced.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Evidence, PromptBinding, TrustClassification
from src.repositories.evidence_repository import (
    EvidenceDigestMismatchError,
    EvidenceRepository,
)
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

BINDING = PromptBinding(
    prompt_id="prompt-1",
    prompt_version="1.0",
    template_sha256="b" * 64,
    rendered_sha256="c" * 64,
)


def _make_evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "evidence_id": "evidence-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "source_type": "web_page",
        "source_uri": "https://example.org/paper",
        "snapshot_artifact_id": "artifact-1",
        "content_sha256": "a" * 64,
        "locator": "char:0-120",
        "trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "prompt_binding": BINDING,
        "acquired_at": NOW,
    }
    values.update(overrides)
    return Evidence(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> EvidenceRepository:
    return EvidenceRepository(db_session)


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)
    await seed_artifact(db_session, organization_id=ORG_ID, content_sha256="a" * 64)


@pytest.mark.asyncio
async def test_record_evidence_persists_the_contract_fields(
    repo: EvidenceRepository,
) -> None:
    row = await repo.record_evidence(_make_evidence(), organization_id=ORG_ID)

    assert row.evidence_id == "evidence-1"
    assert row.organization_id == ORG_ID
    assert row.content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_record_evidence_fails_closed_without_an_org_context(
    repo: EvidenceRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.record_evidence(_make_evidence(), organization_id=None)


@pytest.mark.asyncio
async def test_record_evidence_rejects_a_mismatched_tenant(
    repo: EvidenceRepository,
) -> None:
    with pytest.raises(TenantMismatchError):
        await repo.record_evidence(_make_evidence(), organization_id=OTHER_ORG_ID)


@pytest.mark.asyncio
async def test_a_digest_that_disagrees_with_the_snapshot_artifact_is_rejected(
    repo: EvidenceRepository,
) -> None:
    """The failing-first test for the cross-table digest guarantee.

    The seeded artifact's `content_sha256` is `"a" * 64`; an evidence row
    citing a different digest for the same snapshot must never be written —
    it would claim "the source said X, verified by hash Y" for a Y that does
    not match what was actually snapshotted.
    """
    mismatched = _make_evidence(content_sha256="f" * 64)

    with pytest.raises(EvidenceDigestMismatchError):
        await repo.record_evidence(mismatched, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_a_digest_that_agrees_with_the_snapshot_artifact_is_accepted(
    repo: EvidenceRepository,
) -> None:
    row = await repo.record_evidence(
        _make_evidence(content_sha256="a" * 64), organization_id=ORG_ID
    )

    assert row.content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_record_evidence_rejects_a_nonexistent_snapshot_artifact(
    repo: EvidenceRepository,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        await repo.record_evidence(
            _make_evidence(snapshot_artifact_id="artifact-missing"),
            organization_id=ORG_ID,
        )


@pytest.mark.asyncio
async def test_the_same_span_cannot_be_recorded_twice(
    repo: EvidenceRepository,
) -> None:
    await repo.record_evidence(_make_evidence(), organization_id=ORG_ID)

    with pytest.raises(IntegrityError):
        await repo.record_evidence(
            _make_evidence(evidence_id="evidence-2"), organization_id=ORG_ID
        )


@pytest.mark.asyncio
async def test_list_evidence_for_run_scopes_to_the_run(
    repo: EvidenceRepository,
) -> None:
    await repo.record_evidence(_make_evidence(), organization_id=ORG_ID)

    rows = await repo.list_evidence_for_run("run-1", organization_id=ORG_ID)

    assert [row.evidence_id for row in rows] == ["evidence-1"]
