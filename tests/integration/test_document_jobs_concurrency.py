"""Admission is atomic across sessions, and a background run carries its tenant context."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.document_critique import DOCUMENT_CRITIQUES, DocumentCritique
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.models.db.user import User
from src.repositories.critique_repository import active_model_jobs
from src.services.document_jobs import (
    MODEL_ADMIT_LOCK,
    MODEL_JOB_KINDS,
    JobLimits,
    QuotaExceeded,
    admit,
    run_job,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

Factory = async_sessionmaker[AsyncSession]
ORG, OTHER_ORG = uuid.uuid4(), uuid.uuid4()
USERS = {ORG: uuid.uuid4(), OTHER_ORG: uuid.uuid4()}


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(server: PostgresContainer) -> AsyncIterator[Factory]:
    url = (
        f"postgresql+asyncpg://{server.username}:{server.password}@"
        f"{server.get_container_host_ip()}:{server.get_exposed_port(5432)}/{server.dbname}"
    )
    engine = create_async_engine(url, pool_size=10)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for user in USERS.values():
            session.add(
                User(
                    id=user,
                    email=f"{user}@example.com",
                    username=str(user),
                    hashed_password="x",
                )
            )
        await session.commit()
    yield sessions
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def document_for(factory: Factory, org: uuid.UUID) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            UploadedDocument(
                id=document_id,
                organization_id=org,
                user_id=USERS[org],
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


async def add_critique(
    session: AsyncSession,
    document_id: uuid.UUID,
    org: uuid.UUID,
    status: str = "queued",
) -> uuid.UUID:
    row = DocumentCritique(
        document_id=document_id,
        organization_id=org,
        user_id=USERS[org],
        status=status,
        model_id="m",
        temperature=0.2,
        prompt_versions={},
        consent_text_version="v",
        consent_text_sha256="0",
        consent_recorded_at=datetime.now(UTC),
        reserved_units=1,
        role_outcomes={},
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row.id


async def admit_critique(
    factory: Factory,
    document_id: uuid.UUID,
    limits: JobLimits,
    *,
    org: uuid.UUID = ORG,
    units: int = 10,
    kind: str = "critique",
    **kw: Any,
) -> bool:
    """The route's sequence: admit, insert the job row, commit."""
    async with factory() as session:
        try:
            await admit(
                session,
                kind=kind,
                organization_id=org,
                user_id=USERS[org],
                units=units,
                limits=limits,
                count_active=kw.pop("count_active", active_model_jobs),
                **kw,
            )
        except QuotaExceeded:
            await session.commit()
            return False
        await add_critique(session, document_id, org)
        await session.commit()
        return True


async def ledger_rows(factory: Factory) -> int:
    async with factory() as session:
        return int(
            await session.scalar(select(func.count()).select_from(UsageLedger)) or 0
        )


async def test_two_admissions_one_slot_below_the_concurrency_limit_admit_exactly_one(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(per_org_concurrent=2)
    assert await admit_critique(factory, document, limits)
    results = await asyncio.gather(
        *(admit_critique(factory, document, limits) for _ in range(2))
    )
    assert results.count(True) == 1


async def test_two_admissions_one_below_the_daily_limit_admit_exactly_one(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(per_user_daily=3)
    for _ in range(2):
        assert await admit_critique(factory, document, limits)
    results = await asyncio.gather(
        *(admit_critique(factory, document, limits) for _ in range(2))
    )
    assert results.count(True) == 1


async def test_two_admissions_whose_units_exceed_the_global_budget_admit_one(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(global_daily_units=15)
    results = await asyncio.gather(
        *(admit_critique(factory, document, limits) for _ in range(2))
    )
    assert results.count(True) == 1


async def test_deleting_the_job_and_document_does_not_reset_the_quota(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(per_user_daily=1)
    assert await admit_critique(factory, document, limits)
    async with factory() as session:
        for row in (await session.execute(select(DocumentCritique))).scalars():
            await session.delete(row)
        for doc in (await session.execute(select(UploadedDocument))).scalars():
            await session.delete(doc)
        await session.commit()
    fresh = await document_for(factory, ORG)
    assert not await admit_critique(factory, fresh, limits)


async def test_a_refused_admission_leaves_the_ledger_unchanged_even_if_the_session_commits(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(per_user_daily=1)
    assert await admit_critique(factory, document, limits)
    before = await ledger_rows(factory)
    assert not await admit_critique(factory, document, limits)
    assert await ledger_rows(factory) == before


async def test_a_global_count_limit_holds_across_orgs_admitting_concurrently(
    factory: Factory,
) -> None:
    documents = {org: await document_for(factory, org) for org in USERS}
    limits = JobLimits(global_daily_count=1)
    results = await asyncio.gather(
        *(admit_critique(factory, documents[org], limits, org=org) for org in USERS)
    )
    assert results.count(True) == 1


async def count_both_kinds_unblocked(
    session: AsyncSession, org: uuid.UUID, now: datetime
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(UsageLedger)
            .where(
                UsageLedger.organization_id == org,
                UsageLedger.kind.in_(MODEL_JOB_KINDS),
            )
        )
        or 0
    )


def counting_both_kinds_after_a_meeting() -> Any:
    """Counts both kinds, but only after both admissions have reached the count.

    With one shared lock the second admission cannot reach it until the first
    commits, so the wait times out and the race is decided by the lock; without
    one, both arrive together and both see a free slot.
    """
    meeting = asyncio.Barrier(2)

    async def count(session: AsyncSession, org: uuid.UUID, now: datetime) -> int:
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(meeting.wait(), 3)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(
                    UsageLedger.organization_id == org,
                    UsageLedger.kind.in_(MODEL_JOB_KINDS),
                )
            )
            or 0
        )

    return count


async def test_a_critique_and_a_revision_racing_for_the_last_slot_admit_exactly_one(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    limits = JobLimits(per_org_concurrent=2)
    shared = {"lock": MODEL_ADMIT_LOCK, "budget_kinds": MODEL_JOB_KINDS}
    assert await admit_critique(
        factory, document, limits, count_active=count_both_kinds_unblocked, **shared
    )
    count_active = counting_both_kinds_after_a_meeting()
    results = await asyncio.gather(
        admit_critique(
            factory,
            document,
            limits,
            kind="critique",
            count_active=count_active,
            **shared,
        ),
        admit_critique(
            factory,
            document,
            limits,
            kind="revision",
            count_active=count_active,
            **shared,
        ),
    )
    assert results.count(True) == 1


async def test_revision_units_count_against_a_critiques_org_budget_not_its_user_limit(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    async with factory() as session:
        for _ in range(5):
            session.add(
                UsageLedger(
                    organization_id=ORG, user_id=USERS[ORG], kind="revision", units=10
                )
            )
        await session.commit()
    shared = {"lock": MODEL_ADMIT_LOCK, "budget_kinds": MODEL_JOB_KINDS}
    assert await admit_critique(
        factory,
        document,
        JobLimits(per_user_daily=1, per_org_daily_units=100),
        **shared,
    )
    assert not await admit_critique(
        factory, document, JobLimits(per_org_daily_units=100), units=45, **shared
    )


async def test_the_background_session_carries_the_rows_tenant_context(
    factory: Factory,
) -> None:
    document = await document_for(factory, ORG)
    async with factory() as session:
        job_id = await add_critique(session, document, ORG)
        await session.commit()
    seen: list[str | None] = []

    async def work() -> None:
        return None

    async def persist(
        session: AsyncSession, result: None
    ) -> tuple[str, dict[str, Any]]:
        seen.append(
            await session.scalar(
                text("SELECT current_setting('app.current_org_id', true)")
            )
        )
        return "succeeded", {}

    await run_job(
        table=DOCUMENT_CRITIQUES,
        job_id=job_id,
        organization_id=ORG,
        created_at=datetime.now(UTC),
        deadline_s=30,
        session_factory=factory,
        semaphore=asyncio.Semaphore(1),
        work=work,
        persist=persist,
    )
    assert seen == [str(ORG)]
    async with factory() as session:
        assert (await session.get(DocumentCritique, job_id)).status == "succeeded"  # type: ignore[union-attr]


async def test_two_runs_of_one_row_run_work_once(factory: Factory) -> None:
    document = await document_for(factory, ORG)
    async with factory() as session:
        job_id = await add_critique(session, document, ORG)
        await session.commit()
    calls = 0

    async def work() -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

    async def persist(
        session: AsyncSession, result: None
    ) -> tuple[str, dict[str, Any]]:
        return "succeeded", {}

    await asyncio.gather(
        *(
            run_job(
                table=DOCUMENT_CRITIQUES,
                job_id=job_id,
                organization_id=ORG,
                created_at=datetime.now(UTC),
                deadline_s=30,
                session_factory=factory,
                semaphore=asyncio.Semaphore(1),
                work=work,
                persist=persist,
            )
            for _ in range(2)
        )
    )
    assert calls == 1
