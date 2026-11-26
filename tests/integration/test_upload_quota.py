"""Concurrent upload reservations admit exactly the remaining quota."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.uploaded_document import UsageLedger
from src.models.db.user import User
from src.repositories.uploaded_document_repository import UsageLedgerRepository

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ORG, USER = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    server: PostgresContainer,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = (
        f"postgresql+asyncpg://{server.username}:{server.password}@"
        f"{server.get_container_host_ip()}:{server.get_exposed_port(5432)}/{server.dbname}"
    )
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            User(
                id=USER,
                email="quota@example.com",
                username="quota",
                hashed_password="x",
            )
        )
        await session.commit()
    yield sessions
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _reserve(factory: async_sessionmaker[AsyncSession], limit: int) -> bool:
    async with factory() as session:
        admitted = await UsageLedgerRepository(session).reserve(
            organization_id=ORG, user_id=USER, kind="upload", limit=limit
        )
        await session.commit()
        return admitted


async def test_upload_quota_admits_exactly_the_remaining_slots_under_concurrency(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(15):
        assert await _reserve(factory, 20)
    results = await asyncio.gather(*(_reserve(factory, 20) for _ in range(12)))
    assert results.count(True) == 5
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(UsageLedger))
    assert count == 20
