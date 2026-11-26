"""Shared fixtures and builders for the similarity service and API tests."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.acquisition.pdf_text import ExtractedText, PageSpan
from src.core.acquisition.sources import LicenseDeclaration, SourceLicense
from src.core.config import settings
from src.core.memory_encryption import document_cipher
from src.models.db.base import Base
from src.models.db.similarity_check import SIMILARITY_CHECKS
from src.repositories.similarity_repository import (
    SIMILARITY_DEADLINE_S,
    CheckView,
    SimilarityRepository,
)
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services import similarity_service as service
from src.services.document_jobs import run_job
from src.services.scholarly_retrieval import (
    EgressEvent,
    RetrievalResult,
    SourceCoverage,
)
from src.services.text_reuse.types import Candidate

KEY = Fernet.generate_key().decode()
ORG_A, USER_A = uuid.uuid4(), uuid.uuid4()
ORG_B, USER_B = uuid.uuid4(), uuid.uuid4()
SHARED = " ".join(f"shared{i}" for i in range(40))
LEAD = " ".join(f"lead{i}" for i in range(30))
TAIL = " ".join(f"tail{i}" for i in range(30))
TEXT = f"{LEAD} {SHARED} {TAIL}"
DEFAULT_OPTIONS: dict[str, Any] = {
    "own_uploads": False,
    "comparison_document_ids": [],
    "open_sources": False,
    "semantic": False,
}

Retrieve = Callable[..., Awaitable[RetrievalResult]]


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setattr(settings, "SIMILARITY_CHECKS_ENABLED", True)
    monkeypatch.setattr(settings, "SIMILARITY_OPEN_SOURCES_ENABLED", True)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "2026-11-26")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make_document(
    factory: async_sessionmaker[AsyncSession],
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
    text: str = TEXT,
    filename: str = "paper.txt",
    declared_authors: list[str] | None = None,
    expires_in: timedelta = timedelta(days=30),
) -> uuid.UUID:
    extracted = ExtractedText(text, (PageSpan(1, 0, len(text)),), "a" * 64, "b" * 64)
    async with factory() as session:
        row = await UploadedDocumentRepository(session, document_cipher()).create(
            organization_id=org,
            user_id=user,
            filename=filename,
            media_type="plain",
            extracted=extracted,
            extractor_version="t",
            invisible_chars_removed=0,
            declared_authors=declared_authors or [],
            expires_at=datetime.now(UTC) + expires_in,
        )
        await session.commit()
        return row.id


async def count(factory: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def candidate(
    wid: str = "W1",
    *,
    text: str = SHARED,
    licence: str | None = "cc-by",
    title: str = "A Source Paper",
    authors: tuple[str, ...] = (),
    full_text: bool = True,
    arxiv_id: str | None = None,
    year: int | None = 2019,
) -> Candidate:
    declared = (
        SourceLicense(licence, LicenseDeclaration.API_FIELD)
        if licence
        else SourceLicense(None, LicenseDeclaration.UNDECLARED)
    )
    return Candidate(
        source_id=wid,
        origin="openalex",
        title=title,
        authors=authors,
        year=year,
        doi=None,
        arxiv_id=arxiv_id,
        licence=declared,
        text=text,
        text_kind="full_text" if full_text else "abstract",
    )


def stub_retrieve(
    candidates: tuple[Candidate, ...] = (),
    *,
    openalex: SourceCoverage | None = None,
    arxiv: SourceCoverage | None = None,
    events: tuple[EgressEvent, ...] = (),
) -> tuple[Retrieve, list[str]]:
    calls: list[str] = []

    async def retrieve(text: str, **kwargs: Any) -> RetrievalResult:
        calls.append(text)
        for event in events:
            await kwargs["record_egress"](event)
        return RetrievalResult(
            candidates, openalex or SourceCoverage(), arxiv or SourceCoverage()
        )

    return retrieve, calls


async def start_check(
    factory: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    *,
    options: dict[str, Any] | None = None,
    retrieve: Retrieve | None = None,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
    work_wrapper: Callable[[Callable[[], Awaitable[Any]]], Callable[[], Awaitable[Any]]]
    | None = None,
) -> tuple[uuid.UUID, Callable[[], Awaitable[None]]]:
    """Create the check row as the route would; return its id and a coroutine that runs it."""
    merged = DEFAULT_OPTIONS | (options or {})
    async with factory() as session:
        row = await SimilarityRepository(session, document_cipher()).create(
            document_id=document_id,
            organization_id=org,
            user_id=user,
            options=merged,
            policy_versions=service.policy_versions(),
        )
        await session.commit()
        check_id, created = row.id, row.created_at
    stub, _ = stub_retrieve()
    work = service.make_work(
        session_factory=factory,
        check_id=check_id,
        document_id=document_id,
        organization_id=org,
        user_id=user,
        options=merged,
        retrieve=retrieve or stub,
    )

    async def go() -> None:
        await run_job(
            table=SIMILARITY_CHECKS,
            job_id=check_id,
            organization_id=org,
            created_at=created,
            deadline_s=SIMILARITY_DEADLINE_S,
            session_factory=factory,
            semaphore=asyncio.Semaphore(1),
            work=work_wrapper(work) if work_wrapper else work,
            persist=service.make_persist(
                check_id=check_id, organization_id=org, user_id=user
            ),
        )

    return check_id, go


async def run_check(
    factory: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
    **kwargs: Any,
) -> tuple[uuid.UUID, CheckView]:
    check_id, go = await start_check(factory, document_id, org=org, user=user, **kwargs)
    await go()
    return check_id, await read_check(factory, check_id, org=org, user=user)


async def read_check(
    factory: async_sessionmaker[AsyncSession],
    check_id: uuid.UUID,
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
) -> CheckView:
    async with factory() as session:
        view = await SimilarityRepository(session, document_cipher()).get(
            check_id, organization_id=org, user_id=user
        )
        await session.commit()
    assert view is not None
    return view
