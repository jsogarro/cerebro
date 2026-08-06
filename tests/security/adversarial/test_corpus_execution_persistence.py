"""The two corpus scenarios that need a database, run against a real one.

``crosstenant-02`` and ``poisoned-05`` both assert that an ``Evidence`` row
cannot borrow legitimacy from an ``Artifact`` outside its own run or tenant.
Neither is reachable from the in-process harness — no tool path holds an
``AsyncSession``, so no mediated call writes an evidence row — and the
in-process exercisers say exactly that rather than claiming a pass. This module
is where they are actually exercised, against the repository that performs the
check.

Both scenarios below were originally ``xfail(strict=True)``, documenting a
real gap: ``EvidenceRepository._get_artifact_row`` selected by ``artifact_id``
alone, with no organization or run filter, so a cross-tenant or cross-run
artifact reference resolved successfully. The gap is now closed (the lookup
is scoped to the caller's organization and run), so both are ordinary
regression tests -- ``xfail(strict=True)`` would report the fix itself as a
test failure (XPASS under strict mode), which is exactly backwards once the
defect it was recording no longer exists.

It carries the ``integration`` marker, like every other repository test here.
Note that the marker does not exclude it: this project's ``addopts`` deselects
only ``live_eval``, so a default ``uv run pytest`` runs this module and starts a
Postgres container. That is the existing convention rather than a choice made
here, but it does mean adding this file moves the suite's totals.

**Why the digests matching is not a mitigation**, which is what made this
worth exercising at all. ``record_evidence`` does compare
``Evidence.content_sha256`` against the referenced artifact's digest, and at
first reading that looks like it would stop a cross-tenant reference. It does
not: artifacts are content-addressed, so two tenants that acquire the same
public source produce byte-identical content and therefore an identical digest.
The check agrees by construction, and pointing at the other tenant's row
requires no secret knowledge — only fetching the same page.

**Both guarantees now hold.** They did not when this module was written: the
artifact lookup selected on ``artifact_id`` alone. It is now scoped to the
citing evidence's own organization *and* run, so neither reference resolves and
both fail the pre-existing "does not exist" path — which deliberately does not
distinguish wrong-tenant from wrong-run from never-existed, so no read leaks
which case applies. The two tests below are kept as plain assertions rather
than deleted: they are the regression cover for a hole that was invisible
behind a digest check that looked like it was doing the work.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

from src.core.contracts import Evidence, ProducerKind, TrustClassification
from src.models.db.base import Base
from src.repositories.evidence_repository import EvidenceRepository
from src.repositories.tenant_scope import TenantMismatchError
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]


# The engine fixtures are defined in this module rather than in a conftest on
# purpose. A conftest under tests/security/adversarial/ would shadow the root
# `db_session` for the *whole* directory, and the in-process harness next door
# neither needs a database nor should pay for one. Defining them here keeps
# them visible to this file only.
#
# This matters more than it looks: the first version of this module inherited
# the root conftest's SQLite `db_session`, whose schema creation dies on a
# Postgres-only ARRAY column. Both defect tests still reported xfail — from the
# fixture error, not from the defect — which is exactly the false result a
# strict xfail is supposed to rule out.


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _postgres() -> AsyncGenerator[PostgresContainer, None]:
    container = PostgresContainer(
        image="postgres:16-alpine",
        username="test_user",
        password="test_password",
        dbname="test_research_db",
    )
    container.start()
    yield container
    container.stop()


@pytest_asyncio.fixture
async def _engine(_postgres: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    url = _postgres.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A Postgres-backed session, shadowing the root conftest's SQLite one."""

    factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
SHARED_DIGEST = "c" * 64
"""One digest, because both tenants acquired the same public source."""


def _evidence(
    *,
    evidence_id: str,
    run_id: str,
    task_id: str,
    snapshot_artifact_id: str,
    content_sha256: str = SHARED_DIGEST,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        task_id=task_id,
        source_type="web_page",
        source_uri="https://example.com/paper.html",
        snapshot_artifact_id=snapshot_artifact_id,
        content_sha256=content_sha256,
        locator="bytes:0-128",
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer_kind=ProducerKind.SYSTEM,
        acquired_at=NOW,
    )


async def _seed_both_tenants(session: AsyncSession) -> None:
    await seed_run_task_attempt(
        session,
        organization_id=ORG_A,
        run_id="run-tenant-a",
        task_id="task-tenant-a",
        attempt_id="attempt-tenant-a",
    )
    await seed_artifact(
        session,
        organization_id=ORG_A,
        artifact_id="artifact-tenant-a",
        run_id="run-tenant-a",
        content_sha256=SHARED_DIGEST,
    )
    await seed_run_task_attempt(
        session,
        organization_id=ORG_B,
        run_id="run-tenant-b",
        task_id="task-tenant-b",
        attempt_id="attempt-tenant-b",
    )
    await seed_artifact(
        session,
        organization_id=ORG_B,
        artifact_id="artifact-tenant-b",
        run_id="run-tenant-b",
        content_sha256=SHARED_DIGEST,
    )


class TestTheControlsThatDoWork:
    """The paired allows, so a rejection below means the check discriminated.

    Without these, a repository that rejected every write would look identical
    to one enforcing tenancy correctly.
    """

    async def test_an_evidence_row_within_one_tenant_is_accepted(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_both_tenants(db_session)
        repository = EvidenceRepository(db_session)
        row = await repository.record_evidence(
            _evidence(
                evidence_id="evidence-ok",
                run_id="run-tenant-a",
                task_id="task-tenant-a",
                snapshot_artifact_id="artifact-tenant-a",
            ),
            organization_id=ORG_A,
        )
        assert row.organization_id == ORG_A

    async def test_the_run_tenant_check_does_fire(
        self, db_session: AsyncSession
    ) -> None:
        """Tenancy IS enforced on the parent run, which is why the gap below
        is specifically about the artifact edge rather than about tenancy
        being absent from this repository."""

        await _seed_both_tenants(db_session)
        repository = EvidenceRepository(db_session)
        with pytest.raises(TenantMismatchError):
            await repository.record_evidence(
                _evidence(
                    evidence_id="evidence-wrong-org",
                    run_id="run-tenant-a",
                    task_id="task-tenant-a",
                    snapshot_artifact_id="artifact-tenant-a",
                ),
                organization_id=ORG_B,
            )


async def test_evidence_cannot_reference_another_tenants_artifact(
    db_session: AsyncSession,
) -> None:
    """crosstenant-02, now a regression test rather than an xfail.

    ``EvidenceRepository.record_evidence`` used to never compare the
    referenced artifact's owning organization against the evidence row's own
    -- ``_get_artifact_row`` selected by ``artifact_id`` alone, with no
    organization or run filter. Because artifacts are content-addressed, the
    digest cross-check that does run was satisfied by both tenants acquiring
    the same public source, so no secret knowledge was needed to name the
    other tenant's artifact row. The lookup is now scoped by organization and
    run.
    """
    await _seed_both_tenants(db_session)
    repository = EvidenceRepository(db_session)
    with pytest.raises((TenantMismatchError, ValueError)):
        await repository.record_evidence(
            _evidence(
                evidence_id="evidence-cross-tenant",
                run_id="run-tenant-a",
                task_id="task-tenant-a",
                snapshot_artifact_id="artifact-tenant-b",
            ),
            organization_id=ORG_A,
        )


async def test_evidence_cannot_reference_another_runs_artifact(
    db_session: AsyncSession,
) -> None:
    """poisoned-05, now a regression test rather than an xfail.

    The same missing check, reached through the poisoned tool-output path.
    An artifact belonging to a different run inside the same tenant used to
    be accepted as an evidence row's snapshot, so a tool output naming
    another run's artifact id created a provenance edge that crossed a run
    boundary -- ``Evidence.validate_provenance_chain`` checks the locator
    grammar and the parent chain, not the artifact's run. The artifact
    lookup is now scoped by run as well as organization.
    """
    await seed_run_task_attempt(
        db_session,
        organization_id=ORG_A,
        run_id="run-one",
        task_id="task-one",
        attempt_id="attempt-one",
    )
    await seed_run_task_attempt(
        db_session,
        organization_id=ORG_A,
        run_id="run-two",
        task_id="task-two",
        attempt_id="attempt-two",
    )
    await seed_artifact(
        db_session,
        organization_id=ORG_A,
        artifact_id="artifact-of-run-two",
        run_id="run-two",
        content_sha256=SHARED_DIGEST,
    )
    repository = EvidenceRepository(db_session)
    with pytest.raises(ValueError):
        await repository.record_evidence(
            _evidence(
                evidence_id="evidence-cross-run",
                run_id="run-one",
                task_id="task-one",
                snapshot_artifact_id="artifact-of-run-two",
            ),
            organization_id=ORG_A,
        )
