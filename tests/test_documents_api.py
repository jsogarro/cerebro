import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
import structlog
from cryptography.fernet import Fernet
from fpdf import FPDF
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from src.api.main import app
from src.api.routes import users as users_routes
from src.auth.models import TokenPayload
from src.core.config import settings
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context, owner_ids
from src.models.db.base import Base
from src.models.db.session import get_session
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.services import document_ingestion as di

TOKEN = "test-token"
KEY = Fernet.generate_key().decode()
ORG_A, USER_A = uuid.uuid4(), uuid.uuid4()
ORG_B, USER_B = uuid.uuid4(), uuid.uuid4()


class _AuditLogger:
    async def log_event(self, **event: Any) -> str:
        return "audit"

    async def flush_buffer(self) -> None:
        return None


class _JWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub=str(USER_A),
            email="a@example.com",
            organization_id=str(ORG_A),
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class Caller:
    user, org = USER_A, ORG_A


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", True)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/docs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(name="client")
async def client_fixture(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    Caller.user, Caller.org = USER_A, ORG_A

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    async def override_tenant() -> TenantContext:
        return TenantContext(user_id=str(Caller.user), organization_id=str(Caller.org))

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_tenant_context] = override_tenant
    app.dependency_overrides[get_jwt_service] = _JWTService
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = _AuditLogger()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http
    for dependency in (get_session, get_tenant_context, get_jwt_service):
        app.dependency_overrides.pop(dependency, None)
    if previous is missing:
        del app.state.audit_logger
    else:
        app.state.audit_logger = previous


def _pdf(pages: list[str]) -> bytes:
    doc = FPDF()
    for text in pages:
        doc.add_page()
        doc.set_font("Helvetica", size=12)
        if text:
            doc.cell(text=text)
    return bytes(doc.output())


async def _upload(
    client: httpx.AsyncClient,
    content: bytes = b"hello world",
    media: str = "text/plain",
    **data: str,
) -> httpx.Response:
    return await client.post(
        "/api/v1/documents",
        files={"file": ("paper.txt", content, media)},
        data=data,
    )


async def _count(factory: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_flag_off_is_404_after_auth_and_unauthenticated_is_401(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", False)
    assert (await _upload(client)).status_code == 404
    bare = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )
    async with bare:
        assert (await bare.post("/api/v1/documents")).status_code == 401
        monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", True)
        assert (await bare.post("/api/v1/documents")).status_code == 401


async def test_missing_or_oversized_content_length_is_refused_before_parsing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed: list[int] = []
    real_form = Request.form

    def spy(self: Request, **kwargs: Any) -> Any:
        parsed.append(1)
        return real_form(self, **kwargs)

    monkeypatch.setattr(Request, "form", spy)

    async def body() -> AsyncIterator[bytes]:
        yield b"--x\r\n"

    chunked = await client.post(
        "/api/v1/documents",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert chunked.status_code == 411

    monkeypatch.setattr(settings, "DOCUMENT_MAX_BYTES", 10)
    too_big = await client.post(
        "/api/v1/documents",
        content=b"x",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(10 + 64 * 1024 + 1),
        },
    )
    assert too_big.status_code == 413
    assert parsed == []


async def test_a_file_larger_than_the_limit_is_413(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_BYTES", 100)
    assert (await _upload(client, b"x" * 200)).status_code == 413


@pytest.mark.parametrize("authors", [["a"] * 21, ["x" * 101], "not json", '"str"'])
async def test_declared_authors_are_bounded(
    client: httpx.AsyncClient, authors: Any
) -> None:
    raw = authors if isinstance(authors, str) else json.dumps(authors)
    assert (await _upload(client, declared_authors=raw)).status_code == 422


async def test_missing_file_field_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/documents", data={"declared_authors": "[]"})
    assert response.status_code == 422


async def test_pdf_upload_returns_metadata_without_text(
    client: httpx.AsyncClient,
) -> None:
    response = await _upload(
        client,
        _pdf(["alpha page one", "bravo page two"]),
        "application/pdf",
        declared_authors='["Ada"]',
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "filename",
        "media_type",
        "char_count",
        "page_count",
        "text_sha256",
        "invisible_chars_removed",
        "expires_at",
    }
    assert body["page_count"] == 2 and body["media_type"] == "pdf"
    assert "alpha" not in response.text


@pytest.mark.parametrize(
    ("content", "media", "status"),
    [
        (b"abc\x00def", "text/plain", 415),
        (b"plain", "application/zip", 415),
        (b"%PDF-1.7 junk", "application/pdf", 422),
    ],
)
async def test_ingestion_errors_map_to_stable_statuses(
    client: httpx.AsyncClient, content: bytes, media: str, status: int
) -> None:
    response = await _upload(client, content, media)
    assert response.status_code == status
    assert response.json()["error"]["code"]


async def test_other_owners_get_404_and_see_only_their_own_documents(
    client: httpx.AsyncClient,
) -> None:
    doc_id = (await _upload(client)).json()["id"]
    assert len((await client.get("/api/v1/documents")).json()) == 1
    assert (await client.get(f"/api/v1/documents/{doc_id}")).status_code == 200

    for user, org in ((USER_B, ORG_A), (USER_A, ORG_B), (USER_B, ORG_B)):
        Caller.user, Caller.org = user, org
        assert (await client.get(f"/api/v1/documents/{doc_id}")).status_code == 404
        assert (await client.delete(f"/api/v1/documents/{doc_id}")).status_code == 404
        assert (await client.get("/api/v1/documents")).json() == []

    Caller.user, Caller.org = USER_A, ORG_A
    assert (await client.get(f"/api/v1/documents/{doc_id}")).status_code == 200
    assert (await client.delete(f"/api/v1/documents/{doc_id}")).status_code == 204
    assert (await client.get(f"/api/v1/documents/{doc_id}")).status_code == 404


async def test_list_limit_is_capped(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/documents?limit=51")).status_code == 422


async def test_the_21st_upload_in_a_day_is_429_even_after_deleting(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    ids = []
    for _ in range(20):
        response = await _upload(client)
        assert response.status_code == 201
        ids.append(response.json()["id"])
    for doc_id in ids:
        assert (await client.delete(f"/api/v1/documents/{doc_id}")).status_code == 204
    assert (await _upload(client)).status_code == 429
    assert await _count(factory, UsageLedger) == 20


async def test_failed_extractions_use_up_the_quota(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_PER_DAY", 3)
    for _ in range(3):
        assert (
            await _upload(client, b"%PDF-1.7 x", "application/pdf")
        ).status_code == 422
    assert (await _upload(client)).status_code == 429
    assert await _count(factory, UsageLedger) == 3
    assert await _count(factory, UploadedDocument) == 0


async def test_a_refused_request_does_not_consume_quota(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_PER_DAY", 1)
    await _upload(client)
    for _ in range(3):
        assert (await _upload(client)).status_code == 429
    assert await _count(factory, UsageLedger) == 1


async def test_gdpr_erasure_removes_documents_and_ledger_rows(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _upload(client)
    Caller.user, Caller.org = USER_B, ORG_A
    await _upload(client)
    assert await _count(factory, UploadedDocument) == 2

    class _Jwt:
        async def revoke_all_user_tokens(self, user_id: str) -> None:
            return None

    async with factory() as session:
        await users_routes.delete_user_gdpr(
            USER_A,
            db=session,
            memory_system=None,
            current_user=SimpleNamespace(id=USER_A, is_superuser=False),  # type: ignore[arg-type]
            jwt_service=_Jwt(),  # type: ignore[arg-type]
        )
        await session.commit()

    async with factory() as session:
        docs = (await session.execute(select(UploadedDocument.user_id))).scalars().all()
        ledger = (await session.execute(select(UsageLedger.user_id))).scalars().all()
    assert docs == [USER_B] and ledger == [USER_B]


async def test_purge_loop_runs_each_interval_and_stops_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[datetime] = []

    class _Repo:
        def __init__(self, *_: Any) -> None: ...

        async def purge_expired(self, now: datetime) -> int:
            calls.append(now)
            return 0

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(di, "UploadedDocumentRepository", _Repo)
    task = asyncio.create_task(di.run_purge_loop(_Session, 0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 3


def test_owner_ids_names_user_and_organization_and_rejects_bad_claims() -> None:
    ids = owner_ids(TenantContext(user_id=str(USER_A), organization_id=str(ORG_A)))
    assert (ids.user_id, ids.organization_id) == (USER_A, ORG_A)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        owner_ids(TenantContext(user_id="x", organization_id=str(ORG_A)))
    assert caught.value.status_code == 403


async def test_startup_refuses_uploads_enabled_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import main
    from src.core.memory_encryption import DocumentEncryptionUnavailable

    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", True)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    with pytest.raises(DocumentEncryptionUnavailable):
        async with main.lifespan(app):
            pass


@pytest_asyncio.fixture(name="uncommitted_client")
async def uncommitted_client_fixture(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose request sessions never commit on teardown."""
    Caller.user, Caller.org = USER_A, ORG_A

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def override_tenant() -> TenantContext:
        return TenantContext(user_id=str(USER_A), organization_id=str(ORG_A))

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_tenant_context] = override_tenant
    app.dependency_overrides[get_jwt_service] = _JWTService
    app.state.audit_logger = _AuditLogger()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http
    for dependency in (get_session, get_tenant_context, get_jwt_service):
        app.dependency_overrides.pop(dependency, None)


async def test_upload_and_delete_commit_before_responding(
    uncommitted_client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await _upload(uncommitted_client)
    assert response.status_code == 201
    assert await _count(factory, UploadedDocument) == 1
    doc_id = response.json()["id"]
    assert (
        await uncommitted_client.delete(f"/api/v1/documents/{doc_id}")
    ).status_code == 204
    assert await _count(factory, UploadedDocument) == 0


async def test_a_chunked_body_cannot_outrun_a_small_content_length(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_BYTES", 100)
    boundary = "xyz"
    filler = b"a" * (2 * 64 * 1024)
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="f.txt"\r\nContent-Type: text/plain\r\n\r\n'
        ).encode()
        + filler
        + f"\r\n--{boundary}--\r\n".encode()
    )
    sent = [body[i : i + 16384] for i in range(0, len(body), 16384)]
    messages = iter(
        [
            {"type": "http.request", "body": chunk, "more_body": i < len(sent) - 1}
            for i, chunk in enumerate(sent)
        ]
    )

    consumed: list[int] = []

    async def receive() -> dict[str, Any]:
        consumed.append(1)
        return next(messages)

    status: list[int] = []

    bodies: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body":
            bodies.append(message["body"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/documents",
        "raw_path": b"/api/v1/documents",
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("test", 1),
        "headers": [
            (b"host", b"test"),
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            (b"content-length", b"100"),
        ],
        "state": {},
    }
    await app(scope, receive, send)  # type: ignore[arg-type]
    assert status == [413]
    assert json.loads(b"".join(bodies))["error"]["code"] == "too_large"
    assert await _count(factory, UploadedDocument) == 0
    assert len(consumed) < len(sent) - 2


async def test_multipart_errors_use_the_error_envelope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/documents",
        content=b"garbage",
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_multipart"


async def test_documents_under_a_rotated_away_key_read_as_missing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = (await _upload(client)).json()["id"]
    monkeypatch.setattr(
        settings, "DOCUMENT_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    fresh = (await _upload(client)).json()["id"]
    assert [d["id"] for d in (await client.get("/api/v1/documents")).json()] == [fresh]
    assert (await client.get(f"/api/v1/documents/{old}")).status_code == 404
    assert (await client.get(f"/api/v1/documents/{fresh}")).status_code == 200


async def test_the_purge_loop_needs_no_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    purged: list[datetime] = []

    class _Repo:
        def __init__(self, *_: Any) -> None: ...

        async def purge_expired(self, now: datetime) -> int:
            purged.append(now)
            return 0

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(di, "UploadedDocumentRepository", _Repo)
    task = asyncio.create_task(di.run_purge_loop(_Session, 0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert purged


async def test_the_purge_task_runs_until_the_stack_closes() -> None:
    from contextlib import AsyncExitStack

    from src.api import main

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_loop(factory: object, interval: float) -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    original = main.run_purge_loop
    main.run_purge_loop = fake_loop  # type: ignore[assignment]
    try:
        async with AsyncExitStack() as stack:
            main._start_document_purge(stack, app, object)  # type: ignore[arg-type]
            await asyncio.wait_for(started.wait(), 2)
            assert not cancelled.is_set()
        await asyncio.wait_for(cancelled.wait(), 2)
    finally:
        main.run_purge_loop = original


async def test_the_lifespan_starts_the_purge_when_a_database_is_available(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from src.api import main
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())
    seen: list[object] = []
    monkeypatch.setattr(
        main,
        "_start_document_purge",
        lambda stack, application, session_factory: seen.append(session_factory),
    )
    async with main.lifespan(app):
        assert seen == [factory]


async def test_the_lifespan_refuses_uploads_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from src.api import main
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(db_session, "_async_session_factory", None)
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())
    async with main.lifespan(app):
        assert app.state.document_purge_active is False


async def test_no_database_connection_is_held_while_a_document_is_extracted(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.routes import documents

    pool = factory.kw["bind"].pool
    checked_out: list[int] = []
    real = documents.extract_upload

    async def spy(*args: Any, **kwargs: Any) -> Any:
        checked_out.append(pool.checkedout())
        return await real(*args, **kwargs)

    monkeypatch.setattr(documents, "extract_upload", spy)
    assert (await _upload(client)).status_code == 201
    assert checked_out == [0]


async def test_a_busy_extractor_is_a_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def busy(*_: Any, **__: Any) -> bytes:
        raise di.DocumentIngestionError("extraction_busy")

    monkeypatch.setattr(di, "_run_child", busy)
    response = await _upload(client, b"%PDF-1.7 x", "application/pdf")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "extraction_busy"


async def test_uploads_are_refused_while_the_retention_sweep_is_not_running(
    client: httpx.AsyncClient,
) -> None:
    app.state.document_purge_active = False
    try:
        response = await _upload(client)
    finally:
        del app.state.document_purge_active
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retention_unavailable"


async def test_users_can_delete_their_documents_without_the_flag_or_the_key(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = (await _upload(client)).json()["id"]
    second = (await _upload(client)).json()["id"]
    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", False)
    assert (await client.delete(f"/api/v1/documents/{first}")).status_code == 204
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    assert (await client.delete(f"/api/v1/documents/{second}")).status_code == 204


async def test_the_purge_loop_sweeps_at_start_and_survives_database_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    class _Repo:
        def __init__(self, *_: Any) -> None: ...

        async def purge_expired(self, now: datetime) -> int:
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("secret-detail", {}, Exception())
            return 0

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(di, "UploadedDocumentRepository", _Repo)
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(di.run_purge_loop(_Session, 0.05))  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
        assert len(calls) == 1
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) >= 2
    assert "OperationalError" in repr(logs) and "secret-detail" not in repr(logs)


async def test_an_unexpected_purge_failure_stops_the_loop_and_closes_uploads(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import AsyncExitStack

    from src.api import main

    async def broken(factory: object, interval: float) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "run_purge_loop", broken)
    with structlog.testing.capture_logs() as logs:
        async with AsyncExitStack() as stack:
            main._start_document_purge(stack, app, object)  # type: ignore[arg-type]
            await asyncio.sleep(0.05)
            assert app.state.document_purge_active is False
            response = await _upload(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retention_unavailable"
    assert "document_purge_stopped" in repr(logs) and "RuntimeError" in repr(logs)


async def test_cancelling_the_purge_at_shutdown_does_not_close_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import AsyncExitStack

    from src.api import main

    async def idle(factory: object, interval: float) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "run_purge_loop", idle)
    async with AsyncExitStack() as stack:
        task = main._start_document_purge(stack, app, object)  # type: ignore[arg-type]
        assert task is not None
        task.cancel()
        await asyncio.sleep(0.05)
        assert app.state.document_purge_active is True


async def test_the_purge_loop_ends_on_an_error_it_does_not_expect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Repo:
        def __init__(self, *_: Any) -> None: ...

        async def purge_expired(self, now: datetime) -> int:
            raise RuntimeError("unexpected")

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(di, "UploadedDocumentRepository", _Repo)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(di.run_purge_loop(_Session, 0.01), 2)  # type: ignore[arg-type]
