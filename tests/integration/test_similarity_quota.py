"""Quota admission calls the shared guard correctly under real concurrency."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.similarity_check import SimilarityCheck
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.models.db.user import User
from src.repositories.similarity_repository import active_similarity_checks
from src.services import similarity_service as service
from src.services.document_jobs import JobLimits, QuotaExceeded, admit

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()
USERS = {i: uuid.uuid4() for i in range(1, 5)}


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
        for number, user_id in USERS.items():
            session.add(
                User(
                    id=user_id,
                    email=f"q{number}@example.com",
                    username=f"q{number}",
                    hashed_password="x",
                )
            )
        await session.commit()
    yield sessions
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _admit(
    factory: async_sessionmaker[AsyncSession],
    *,
    org: uuid.UUID,
    user: uuid.UUID,
    kind: str,
    limits: JobLimits,
) -> bool:
    async with factory() as session:
        try:
            await admit(
                session,
                kind=kind,
                organization_id=org,
                user_id=user,
                units=1,
                limits=limits,
                count_active=active_similarity_checks,
            )
        except QuotaExceeded:
            await session.rollback()
            return False
        await session.commit()
        return True


async def _ledger(factory: async_sessionmaker[AsyncSession], kind: str) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.kind == kind)
            )
            or 0
        )


async def test_concurrent_checks_admit_exactly_the_remaining_daily_slots(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    limits = service.SIMILARITY_LIMITS
    user = USERS[1]
    for _ in range(service.SIMILARITY_PER_USER_DAILY - 1):
        assert await _admit(
            factory, org=ORG_A, user=user, kind="similarity_check", limits=limits
        )
    results = await asyncio.gather(
        *(
            _admit(
                factory, org=ORG_A, user=user, kind="similarity_check", limits=limits
            )
            for _ in range(4)
        )
    )
    assert results.count(True) == 1
    assert (
        await _ledger(factory, "similarity_check") == service.SIMILARITY_PER_USER_DAILY
    )


async def test_the_global_open_source_cap_holds_across_organisations(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    limits = JobLimits(global_daily_count=3)
    for _ in range(2):
        assert await _admit(
            factory,
            org=ORG_A,
            user=USERS[2],
            kind="similarity_open_sources",
            limits=limits,
        )
    results = await asyncio.gather(
        _admit(
            factory,
            org=ORG_A,
            user=USERS[2],
            kind="similarity_open_sources",
            limits=limits,
        ),
        _admit(
            factory,
            org=ORG_B,
            user=USERS[3],
            kind="similarity_open_sources",
            limits=limits,
        ),
    )
    assert results.count(True) == 1
    assert not await _admit(
        factory, org=ORG_B, user=USERS[3], kind="similarity_open_sources", limits=limits
    )
    assert await _ledger(factory, "similarity_open_sources") == 3


async def test_a_refused_admission_leaves_the_ledger_unchanged(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    before = await _ledger(factory, "similarity_open_sources")
    refused = await _admit(
        factory,
        org=ORG_A,
        user=USERS[4],
        kind="similarity_open_sources",
        limits=JobLimits(global_daily_count=0),
    )
    assert refused is False
    assert await _ledger(factory, "similarity_open_sources") == before


async def _document(
    factory: async_sessionmaker[AsyncSession], org: uuid.UUID
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            UploadedDocument(
                id=document_id,
                organization_id=org,
                user_id=USERS[1],
                filename_enc="f",
                media_type="plain",
                source_sha256="s",
                text_sha256="t",
                char_count=5,
                pages=[],
                extractor_version="v",
                text_enc="x",
                declared_authors_enc="y",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    return document_id


async def _admit_and_create(
    factory: async_sessionmaker[AsyncSession], org: uuid.UUID, document_id: uuid.UUID
) -> bool:
    """The route's order: admit and insert the row in one transaction, then commit."""
    async with factory() as session:
        try:
            await admit(
                session,
                kind="similarity_check",
                organization_id=org,
                user_id=USERS[4],
                units=1,
                limits=JobLimits(per_org_concurrent=2),
                count_active=active_similarity_checks,
            )
        except QuotaExceeded:
            await session.rollback()
            return False
        session.add(
            SimilarityCheck(
                document_id=document_id,
                organization_id=org,
                user_id=USERS[1],
                status="queued",
                options={},
                coverage={},
                policy_versions={},
            )
        )
        await session.commit()
        return True


async def test_two_concurrent_posts_at_one_running_check_admit_exactly_one(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    org = uuid.uuid4()
    document_id = await _document(factory, org)
    async with factory() as session:
        session.add(
            SimilarityCheck(
                document_id=document_id,
                organization_id=org,
                user_id=USERS[1],
                status="running",
                options={},
                coverage={},
                policy_versions={},
            )
        )
        await session.commit()
    results = await asyncio.gather(
        _admit_and_create(factory, org, document_id),
        _admit_and_create(factory, org, document_id),
    )
    assert results.count(True) == 1
