"""A foreign tenant reads nothing back from the Wave 4 tables.

Every Wave 4 repository already refuses a *write* without an organization
context and refuses one whose contract tenant disagrees with it. Nothing
covered the read side, and the read side is where the boundary is claimed:
row-level security is installed as posture only — the application connects as
the Postgres table owner, which bypasses policies — so the repository's
``organization_id`` filter is the enforcement, and it was the enforcement
nobody exercised.

How that was found is worth recording, because it is this wave's own lesson
turned on the wave itself. Replacing ``scope_to_organization`` with
``return query`` — read scoping switched off entirely across all five
repositories — left **47 repository tests green**. Not one failed. A control
that cannot fail is not evidence, and a suite that passes with the control
removed was never testing it.

Every case here is paired with a positive control: the owning tenant reads the
same row through the same method in the same test. A repository that returned
``None`` to everyone would satisfy the denial half and is what the control is
there to catch.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    TrustClassification,
)
from src.repositories.artifact_repository import ArtifactRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
OWNER_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
FOREIGN_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


@pytest_asyncio.fixture(name="owned_artifact", autouse=True)
async def owned_artifact_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=OWNER_ORG)
    await ArtifactRepository(db_session).create_artifact(
        Artifact(
            artifact_id="artifact-owned",
            run_id="run-1",
            kind="source_snapshot",
            media_type="text/html",
            storage_uri="s3://bucket/key",
            content_sha256="a" * 64,
            status=ArtifactStatus.FINAL,
            trust=TrustClassification.EXTERNAL_UNTRUSTED,
            producer="acquisition-tool",
            created_at=NOW,
        ),
        organization_id=OWNER_ORG,
    )


async def test_a_foreign_tenant_cannot_read_an_artifact_by_id(
    db_session: AsyncSession,
) -> None:
    repo = ArtifactRepository(db_session)

    assert (
        await repo.get_artifact("artifact-owned", organization_id=FOREIGN_ORG) is None
    )
    # Positive control: the same call, the same row, the owning tenant.
    owned = await repo.get_artifact("artifact-owned", organization_id=OWNER_ORG)
    assert owned is not None, (
        "the owning tenant cannot read its own artifact either, so the denial "
        "above demonstrates nothing about tenant scoping"
    )


async def test_a_foreign_tenant_lists_no_artifacts_for_the_run(
    db_session: AsyncSession,
) -> None:
    repo = ArtifactRepository(db_session)

    assert await repo.list_artifacts_for_run("run-1", organization_id=FOREIGN_ORG) == []
    owned = await repo.list_artifacts_for_run("run-1", organization_id=OWNER_ORG)
    assert [row.artifact_id for row in owned] == ["artifact-owned"]


async def test_an_unscoped_read_still_crosses_tenants_by_design(
    db_session: AsyncSession,
) -> None:
    """The permissive default, pinned so it cannot become one by accident.

    ``organization_id=None`` means "no org context" — a boot-time recovery
    scan across every tenant — and returns the row. That is deliberate and is
    the opposite of the write path, which fails closed. It is pinned here so
    the asymmetry is a decision the tests record rather than something a
    reader has to infer, and so narrowing it later is a visible change.
    """

    repo = ArtifactRepository(db_session)

    assert await repo.get_artifact("artifact-owned") is not None
