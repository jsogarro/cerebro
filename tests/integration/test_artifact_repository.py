"""Integration tests for ``ArtifactRepository`` against real Postgres."""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Artifact, ArtifactStatus, TrustClassification
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _make_artifact(**overrides: object) -> Artifact:
    values: dict[str, object] = {
        "artifact_id": "artifact-1",
        "run_id": "run-1",
        "kind": "source_snapshot",
        "media_type": "text/html",
        "storage_uri": "s3://bucket/key",
        "content_sha256": "a" * 64,
        "status": ArtifactStatus.FINAL,
        "trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "producer": "acquisition-tool",
        "created_at": NOW,
    }
    values.update(overrides)
    return Artifact(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> ArtifactRepository:
    return ArtifactRepository(db_session)


@pytest_asyncio.fixture(name="seeded_run", autouse=True)
async def seeded_run_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_create_artifact_persists_the_contract_fields(
    repo: ArtifactRepository,
) -> None:
    artifact = _make_artifact()

    row = await repo.create_artifact(artifact, organization_id=ORG_ID)

    assert row.artifact_id == "artifact-1"
    assert row.organization_id == ORG_ID
    assert row.status == ArtifactStatus.FINAL.value
    assert row.content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_create_artifact_fails_closed_without_an_org_context(
    repo: ArtifactRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.create_artifact(_make_artifact(), organization_id=None)


@pytest.mark.asyncio
async def test_record_status_transition_updates_the_existing_row(
    repo: ArtifactRepository,
) -> None:
    await repo.create_artifact(
        _make_artifact(status=ArtifactStatus.DRAFT), organization_id=ORG_ID
    )

    row = await repo.record_status_transition(
        "artifact-1", organization_id=ORG_ID, status=ArtifactStatus.FINAL
    )

    assert row.status == ArtifactStatus.FINAL.value


@pytest.mark.asyncio
async def test_record_status_transition_rejects_a_mismatched_tenant(
    repo: ArtifactRepository,
) -> None:
    await repo.create_artifact(_make_artifact(), organization_id=ORG_ID)

    with pytest.raises(TenantMismatchError):
        await repo.record_status_transition(
            "artifact-1",
            organization_id=OTHER_ORG_ID,
            status=ArtifactStatus.INVALIDATED,
        )


@pytest.mark.asyncio
async def test_list_artifacts_for_run_scopes_to_the_run(
    repo: ArtifactRepository,
) -> None:
    await repo.create_artifact(_make_artifact(), organization_id=ORG_ID)
    await seed_run_task_attempt(
        repo.session,
        organization_id=ORG_ID,
        run_id="run-other",
        task_id="task-other",
        attempt_id="attempt-other",
    )
    await repo.create_artifact(
        _make_artifact(artifact_id="artifact-2", run_id="run-other"),
        organization_id=ORG_ID,
    )

    rows = await repo.list_artifacts_for_run("run-1", organization_id=ORG_ID)

    assert [row.artifact_id for row in rows] == ["artifact-1"]


@pytest.mark.asyncio
async def test_a_write_never_commits_the_caller_s_transaction(
    repo: ArtifactRepository, db_session: AsyncSession
) -> None:
    """``create_artifact`` flushes, but the caller owns the commit.

    A rollback of the caller's transaction after a repository write must
    leave no trace — proving the repository never calls ``session.commit()``
    itself.
    """
    await repo.create_artifact(_make_artifact(), organization_id=ORG_ID)

    await db_session.rollback()

    from sqlalchemy import select

    from src.models.db.artifact import AgentArtifact

    result = await db_session.execute(
        select(AgentArtifact).where(AgentArtifact.artifact_id == "artifact-1")
    )
    assert result.scalar_one_or_none() is None
