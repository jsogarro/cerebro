"""Operator-registered authorities are stored per tenant and resolved from there."""

import dataclasses
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
)
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.execution_authority_repository import (
    ExecutionAuthorityRepository,
)
from src.repositories.tenant_scope import TenantMismatchError
from tests.integration.crash_fixtures import ORG_ID, make_binding

pytestmark = [pytest.mark.integration]

OTHER_ORG = str(uuid.uuid4())


def _ref(binding) -> ExecutionAuthorityReference:
    return ExecutionAuthorityReference(
        authority_id=binding.authority_id,
        authority_version=binding.authority_version,
    )


@pytest.mark.asyncio
async def test_a_registered_authority_resolves_for_its_tenant_only(
    test_engine: AsyncEngine,
) -> None:
    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    binding = make_binding("store-run-1", authority_id="store-authority-1")
    async with factory() as session:
        await ExecutionAuthorityRepository(session).create(
            binding, organization_id=ORG_ID
        )
        await session.commit()

    resolver = PersistedExecutionAuthorityResolver(session_factory=factory)
    await resolver.load_authority(_ref(binding), organization_id=OTHER_ORG)
    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(_ref(binding), organization_id=OTHER_ORG)
    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(_ref(binding), organization_id=ORG_ID)

    await resolver.load_authority(_ref(binding), organization_id=ORG_ID)
    resolved = resolver.resolve(_ref(binding), organization_id=ORG_ID)
    assert resolved.run.run_id == "store-run-1"
    assert resolved.budget == binding.budget


@pytest.mark.asyncio
async def test_registering_requires_the_binding_to_belong_to_the_tenant(
    test_engine: AsyncEngine,
) -> None:
    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    binding = make_binding("store-run-2", authority_id="store-authority-2")
    async with factory() as session:
        with pytest.raises(TenantMismatchError):
            await ExecutionAuthorityRepository(session).create(
                binding, organization_id=OTHER_ORG
            )


@pytest.mark.asyncio
async def test_an_authority_identity_cannot_be_registered_twice(
    test_engine: AsyncEngine,
) -> None:
    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    binding = make_binding("store-run-3", authority_id="store-authority-3")
    again = dataclasses.replace(
        binding, run=binding.run.model_copy(update={"run_id": "store-run-3b"})
    )
    async with factory() as session:
        repo = ExecutionAuthorityRepository(session)
        await repo.create(binding, organization_id=ORG_ID)
        with pytest.raises(IntegrityError):
            await repo.create(again, organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_the_cli_store_commits_the_authority(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.cli.commands import authority as authority_commands
    from src.models.db import session as db_session

    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def noop() -> None:
        return None

    monkeypatch.setattr(db_session, "init_db", noop)
    monkeypatch.setattr(db_session, "close_db", noop)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    binding = make_binding("store-run-4", authority_id="store-authority-4")

    await authority_commands._store(binding, ORG_ID)

    async with factory() as session:
        row = await ExecutionAuthorityRepository(session).get(
            authority_id="store-authority-4",
            authority_version="1",
            organization_id=ORG_ID,
        )
    assert row is not None
