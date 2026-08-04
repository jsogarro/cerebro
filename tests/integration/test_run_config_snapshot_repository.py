"""Integration tests for ``RunConfigSnapshotRepository`` against real Postgres.

Covers immutability enforcement (the append-only guard fires through the
normal repository-returned row, not just in isolation) and the authority
lookup path the DB-backed resolver depends on.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    PinnedComponentKind,
    PinnedComponentVersion,
    PinnedVersions,
    Run,
    RunStatus,
)
from src.models.db.append_only import AppendOnlyViolationError
from src.repositories.run_config_snapshot_repository import (
    RunConfigSnapshotRepository,
    hash_configuration,
)
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

PINNED_VERSIONS = PinnedVersions(
    workflow_definition_id="research",
    workflow_definition_version="1",
    routing_policy_id="default",
    routing_policy_version="1",
    event_envelope_version="1.0",
    components=(
        PinnedComponentVersion(
            kind=PinnedComponentKind.PROMPT,
            component_id="research.synthesis",
            version="3",
        ),
    ),
)


def _make_run(**overrides: object) -> Run:
    values: dict[str, object] = {
        "run_id": "run-1",
        "tenant_id": str(ORG_ID),
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "idempotency_key": "submit-1",
        "requested_by": "user-1",
        "status": RunStatus.CREATED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Run(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> RunConfigSnapshotRepository:
    return RunConfigSnapshotRepository(db_session)


@pytest_asyncio.fixture(name="seeded_run")
async def seeded_run_fixture(db_session: AsyncSession) -> Run:
    run = _make_run()
    await RunLifecycleRepository(db_session).create_run(run, organization_id=ORG_ID)
    await db_session.flush()
    return run


def test_hash_configuration_is_deterministic_under_key_order() -> None:
    first = {"a": 1, "b": {"x": 1, "y": 2}}
    second = {"b": {"y": 2, "x": 1}, "a": 1}

    assert hash_configuration(first) == hash_configuration(second)
    assert len(hash_configuration(first)) == 64


@pytest.mark.asyncio
async def test_create_snapshot_persists_authority_and_pinned_versions(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    configuration = {"domains": ["research"]}

    row = await repo.create_snapshot(
        run=seeded_run,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration=configuration,
    )

    assert row.run_id == "run-1"
    assert row.authority_id == "authority-1"
    assert row.authority_version == "1"
    assert row.content_sha256 == hash_configuration(configuration)
    assert row.pinned_versions["workflow_definition_id"] == "research"


@pytest.mark.asyncio
async def test_create_snapshot_fails_closed_without_an_org_context(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.create_snapshot(
            run=seeded_run,
            organization_id=None,
            config_snapshot_id="snapshot-1",
            authority_id="authority-1",
            authority_version="1",
            pinned_versions=PINNED_VERSIONS,
            configuration={},
        )


@pytest.mark.asyncio
async def test_create_snapshot_rejects_a_mismatched_tenant(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    with pytest.raises(TenantMismatchError):
        await repo.create_snapshot(
            run=seeded_run,
            organization_id=OTHER_ORG_ID,
            config_snapshot_id="snapshot-1",
            authority_id="authority-1",
            authority_version="1",
            pinned_versions=PINNED_VERSIONS,
            configuration={},
        )


@pytest.mark.asyncio
async def test_get_by_run_returns_the_one_snapshot(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    await repo.create_snapshot(
        run=seeded_run,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={},
    )

    found = await repo.get_by_run("run-1", organization_id=ORG_ID)

    assert found is not None
    assert found.config_snapshot_id == "snapshot-1"


@pytest.mark.asyncio
async def test_find_by_authority_locates_the_snapshot_by_indexed_columns(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    await repo.create_snapshot(
        run=seeded_run,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={"domains": ["research"]},
    )

    found = await repo.find_by_authority(
        authority_id="authority-1", authority_version="1"
    )

    assert len(found) == 1
    assert found[0].run_id == "run-1"


@pytest.mark.asyncio
async def test_find_by_authority_misses_a_different_version(
    repo: RunConfigSnapshotRepository, seeded_run: Run
) -> None:
    await repo.create_snapshot(
        run=seeded_run,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={},
    )

    found = await repo.find_by_authority(
        authority_id="authority-1", authority_version="2"
    )

    assert found == []


@pytest.mark.asyncio
async def test_list_all_returns_every_snapshot(
    repo: RunConfigSnapshotRepository, db_session: AsyncSession
) -> None:
    lifecycle = RunLifecycleRepository(db_session)
    run_a = _make_run(run_id="run-a", idempotency_key="submit-a")
    run_b = _make_run(run_id="run-b", idempotency_key="submit-b")
    await lifecycle.create_run(run_a, organization_id=ORG_ID)
    await lifecycle.create_run(run_b, organization_id=ORG_ID)
    await repo.create_snapshot(
        run=run_a,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-a",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={},
    )
    await repo.create_snapshot(
        run=run_b,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-b",
        authority_id="authority-2",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={},
    )

    snapshots = await repo.list_all()

    assert {row.config_snapshot_id for row in snapshots} == {"snapshot-a", "snapshot-b"}


# --- immutability ------------------------------------------------------


@pytest.mark.asyncio
async def test_config_snapshot_is_immutable_via_the_repository_returned_row(
    repo: RunConfigSnapshotRepository, seeded_run: Run, db_session: AsyncSession
) -> None:
    row = await repo.create_snapshot(
        run=seeded_run,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        authority_id="authority-1",
        authority_version="1",
        pinned_versions=PINNED_VERSIONS,
        configuration={"domains": ["research"]},
    )

    row.configuration = {"domains": ["tampered"]}
    with pytest.raises(AppendOnlyViolationError):
        await db_session.flush()
