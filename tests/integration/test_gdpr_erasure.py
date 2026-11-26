"""GDPR erasure against real Postgres: FK-dependent rows and token revocation."""

import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import AsyncGenerator, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import JWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routes import users
from src.auth.jwt_service import JWTService, JWTServiceUnavailableError
from src.middleware.auth_middleware import get_current_active_user, get_jwt_service
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.session import get_session
from src.models.db.similarity_check import EgressEvent, SimilarityCheck, SimilarityMatch
from src.models.db.uploaded_document import UploadedDocument
from src.models.db.user import User
from src.models.db.workflow_checkpoint import WorkflowCheckpoint

pytestmark = [pytest.mark.integration]

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
TABLES = (
    User,
    ResearchProject,
    ResearchResult,
    WorkflowCheckpoint,
    GeneratedReport,
    ReportFormat,
)


async def _seed_account(
    sessions: async_sessionmaker[AsyncSession], tag: str
) -> uuid.UUID:
    """A user with a project and a row in every FK-dependent table."""
    user_id = uuid.uuid4()
    async with sessions() as s:
        s.add(
            User(
                id=user_id,
                email=f"{tag}@example.com",
                username=tag,
                hashed_password="x",
            )
        )
        await s.flush()
        project = ResearchProject(
            title=tag,
            query="q",
            user_id=str(user_id),
            domains=["privacy"],
            status="DRAFT",
        )
        s.add(project)
        await s.flush()
        s.add(ResearchResult(project_id=project.id, result_type="summary", content={}))
        s.add(
            WorkflowCheckpoint(
                workflow_id=f"wf-{tag}",
                project_id=project.id,
                checkpoint_data={},
                phase="p",
            )
        )
        # One report linked to the project and user, one to the user alone.
        for project_id in (project.id, None):
            report = GeneratedReport(
                project_id=project_id,
                user_id=user_id,
                organization_id=ORG_ID,
                title=tag,
                report_type="full",
                query="q",
            )
            s.add(report)
            await s.flush()
            s.add(
                ReportFormat(
                    report_id=report.id,
                    format_type="md",
                    mime_type="text/markdown",
                    file_extension="md",
                )
            )
        await s.commit()
    return user_id


async def _counts(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with sessions() as s:
        return {
            m.__tablename__: (
                await s.execute(select(func.count()).select_from(m))
            ).scalar_one()
            for m in TABLES
        }


@pytest.fixture
def sessions(test_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture
def make_client(
    sessions: async_sessionmaker[AsyncSession],
) -> Callable[[uuid.UUID, JWTService], AsyncClient]:
    """Client for the erasure route; the session commits/rolls back like ``get_session``."""

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def build(caller_id: uuid.UUID, jwt_service: JWTService) -> AsyncClient:
        app = FastAPI()
        app.include_router(users.router)
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_jwt_service] = lambda: jwt_service
        app.dependency_overrides[users.get_memory_system] = lambda: None
        app.dependency_overrides[get_current_active_user] = lambda: User(
            id=caller_id, is_superuser=False
        )
        return AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )

    return build


async def test_erasure_deletes_dependent_rows_and_keeps_other_users(
    sessions, make_client, jwt_service
) -> None:
    victim = await _seed_account(sessions, "victim")
    await _seed_account(sessions, "bystander")

    async with make_client(victim, jwt_service) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    # Only the bystander's rows remain: one user/project/result/checkpoint, two reports.
    assert await _counts(sessions) == {
        "users": 1,
        "research_projects": 1,
        "research_results": 1,
        "workflow_checkpoints": 1,
        "generated_reports": 2,
        "report_formats": 2,
    }
    deleted = response.json()["deleted"]
    assert deleted["generated_reports"] == 2
    assert deleted["research_results"] == 1
    assert deleted["workflow_checkpoints"] == 1


async def test_erasure_revokes_previously_issued_tokens(
    sessions, make_client, jwt_service
) -> None:
    victim = await _seed_account(sessions, "victim")
    pair = await jwt_service.generate_token_pair(
        user_id=str(victim), email="victim@example.com", organization_id=str(ORG_ID)
    )
    await jwt_service.validate_token(pair.access_token)  # admitted before erasure

    async with make_client(victim, jwt_service) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    with pytest.raises(JWTError, match="revoked"):
        await jwt_service.validate_token(pair.access_token)


async def test_erasure_fails_closed_when_revocation_store_is_down(
    sessions, make_client
) -> None:
    victim = await _seed_account(sessions, "victim")
    before = await _counts(sessions)
    down = JWTService(redis_client=None)

    async def _unavailable(user_id: str) -> int:
        raise JWTServiceUnavailableError("Token revocation store unavailable")

    down.revoke_all_user_tokens = _unavailable  # type: ignore[method-assign]

    async with make_client(victim, down) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 503
    assert await _counts(sessions) == before


async def _seed_similarity(
    sessions: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> None:
    """A document with a check, a match and an egress row, all owned by ``user_id``."""
    document_id, check_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as s:
        s.add(
            UploadedDocument(
                id=document_id,
                organization_id=ORG_ID,
                user_id=user_id,
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
        await s.flush()
        s.add(
            SimilarityCheck(
                id=check_id,
                document_id=document_id,
                organization_id=ORG_ID,
                user_id=user_id,
                status="succeeded",
                options={},
                coverage={},
                policy_versions={},
            )
        )
        await s.flush()
        s.add(
            SimilarityMatch(
                check_id=check_id,
                organization_id=ORG_ID,
                user_id=user_id,
                kind="verbatim",
                attribution="unattributed",
                from_user_upload=False,
                doc_start=0,
                doc_end=1,
                tokens=12,
                source_origin="openalex",
                source_id="W1",
                source_title_enc="t",
                source_licence={},
                source_text_kind="full_text",
                src_start=0,
                src_end=1,
            )
        )
        s.add(
            EgressEvent(
                check_id=check_id,
                organization_id=ORG_ID,
                user_id=user_id,
                host="arxiv.org",
                request_class="pdf",
                term_count=0,
                bytes_received=1,
                duration_ms=1,
                status="200",
            )
        )
        await s.commit()


async def _similarity_counts(sessions: async_sessionmaker[AsyncSession]) -> list[int]:
    async with sessions() as s:
        return [
            (await s.execute(select(func.count()).select_from(model))).scalar_one()
            for model in (SimilarityCheck, SimilarityMatch, EgressEvent)
        ]


async def test_erasure_removes_similarity_rows_and_keeps_other_users(
    sessions, make_client, jwt_service
) -> None:
    victim = await _seed_account(sessions, "victim")
    bystander = await _seed_account(sessions, "bystander")
    await _seed_similarity(sessions, victim)
    await _seed_similarity(sessions, bystander)
    assert await _similarity_counts(sessions) == [2, 2, 2]

    async with make_client(victim, jwt_service) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    assert await _similarity_counts(sessions) == [1, 1, 1]
    assert response.json()["deleted"]["egress_events"] == 1
