"""The similarity API: consent gate, order of checks, runs, report, isolation."""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.main import app
from src.api.routes import similarity_checks as similarity_routes
from src.api.routes.similarity_checks import get_job_session_factory, get_retriever
from src.auth.models import TokenPayload
from src.core.config import settings
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.session import get_session
from src.models.db.similarity_check import SimilarityCheck, SimilarityMatch
from src.models.db.uploaded_document import UsageLedger
from src.services import similarity_service as service
from src.services.scholarly_retrieval import CONSENT_RECIPIENTS, SourceCoverage
from tests.similarity_support import (
    ORG_A,
    ORG_B,
    SHARED,
    TEXT,
    USER_A,
    USER_B,
    candidate,
    count,
    factory_fixture,  # noqa: F401
    make_document,
    stub_retrieve,
)

TOKEN = "t"
OPEN_BODY = {
    "own_uploads": True,
    "comparison_document_ids": [],
    "open_sources": {
        "enabled": True,
        "consent": {
            "recipients": list(CONSENT_RECIPIENTS),
            "consent_text_version": service.SIMILARITY_CONSENT_TEXT_VERSION,
        },
    },
    "semantic": False,
}
FORBIDDEN = (r"\boriginal(ity)?\b", r"plagiarism[- ]free", r"\d+(\.\d+)?\s*%")


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


class Retrieval:
    """The injected retriever: records calls and can hold a run open."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.entered = asyncio.Event()
        self.cancelled = False
        self.inner, _ = stub_retrieve()

    async def __call__(self, text: str, **kwargs: Any) -> Any:
        self.calls.append(text)
        self.entered.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await self.inner(text, **kwargs)


@pytest.fixture(name="retrieval")
def retrieval_fixture() -> Retrieval:
    return Retrieval()


@pytest_asyncio.fixture(name="client")
async def client_fixture(
    factory: async_sessionmaker[AsyncSession], retrieval: Retrieval
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
    app.dependency_overrides[get_retriever] = lambda: retrieval
    app.dependency_overrides[get_job_session_factory] = lambda: factory
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = _AuditLogger()
    app.state.document_jobs = set()
    app.state.similarity_semaphore = asyncio.Semaphore(1)
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
        get_retriever,
        get_job_session_factory,
    ):
        app.dependency_overrides.pop(dependency, None)
    if previous is missing:
        del app.state.audit_logger
    else:
        app.state.audit_logger = previous
    del app.state.document_jobs, app.state.similarity_semaphore


async def drain() -> None:
    jobs = list(app.state.document_jobs)
    if jobs:
        await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 10)


def checks_url(document_id: uuid.UUID) -> str:
    return f"/api/v1/documents/{document_id}/similarity-checks"


async def start(
    client: httpx.AsyncClient,
    document_id: uuid.UUID,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.post(checks_url(document_id), json=body or {})


async def run_to_end(
    client: httpx.AsyncClient,
    document_id: uuid.UUID,
    body: dict[str, Any] | None = None,
) -> str:
    response = await start(client, document_id, body)
    assert response.status_code == 202, response.text
    await drain()
    return str(response.json()["id"])


# ---- config -------------------------------------------------------------------


async def test_config_names_the_consent_recipients_and_preview_rule(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/api/v1/similarity-config")).json()
    assert body["recipients"] == list(CONSENT_RECIPIENTS)
    assert body["version"] == service.SIMILARITY_CONSENT_TEXT_VERSION
    assert body["sha256"] == service.consent_sha256()
    assert body["terms_verified_on"] == "2026-11-26"
    assert body["query_preview_rule"] and body["open_sources_enabled"] is True


async def test_config_needs_authentication(client: httpx.AsyncClient) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as bare:
        assert (await bare.get("/api/v1/similarity-config")).status_code == 401


# ---- route and parent scope ----------------------------------------------------


async def test_a_dark_feature_is_404_after_authentication(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "SIMILARITY_CHECKS_ENABLED", False)
    assert (await start(client, document_id)).status_code == 404
    assert (await client.get(checks_url(document_id))).status_code == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as bare:
        assert (await bare.post(checks_url(document_id), json={})).status_code == 401


async def test_a_foreign_document_is_404_on_post_and_on_the_nested_list(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    foreign = await make_document(factory, org=ORG_B, user=USER_B)
    assert (await start(client, foreign)).status_code == 404
    assert (await client.get(checks_url(foreign))).status_code == 404
    assert (await client.get(checks_url(uuid.uuid4()))).status_code == 404


async def test_an_expired_document_reads_as_missing(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    expired = await make_document(factory, expires_in=timedelta(seconds=-1))
    assert (await start(client, expired)).status_code == 404


# ---- comparison caps -------------------------------------------------------------


async def test_comparison_caps_refuse_before_any_row_or_retrieval(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    mine = [str(await make_document(factory)) for _ in range(2)]
    theirs = await make_document(factory, user=USER_B)
    eleven = {"comparison_document_ids": [str(uuid.uuid4()) for _ in range(11)]}
    assert (await start(client, document_id, eleven)).status_code == 422
    foreign = {"comparison_document_ids": [str(theirs)]}
    assert (await start(client, document_id, foreign)).status_code == 404
    missing = {"comparison_document_ids": [str(uuid.uuid4())]}
    assert (await start(client, document_id, missing)).status_code == 404
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(TEXT))
    over = await start(client, document_id, {"comparison_document_ids": mine})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "candidate_budget_exceeded"
    assert await count(factory, SimilarityCheck) == 0
    assert await count(factory, UsageLedger) == 0
    assert retrieval.calls == []


async def test_unknown_body_fields_are_refused(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    assert (await start(client, document_id, {"text": "sneaky"})).status_code == 422


# ---- consent ----------------------------------------------------------------------


def consent(**override: Any) -> dict[str, Any]:
    block = {
        "recipients": list(CONSENT_RECIPIENTS),
        "consent_text_version": service.SIMILARITY_CONSENT_TEXT_VERSION,
    }
    return {"enabled": True, "consent": block | override}


async def test_open_sources_need_a_consent_block(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    response = await start(client, document_id, {"open_sources": {"enabled": True}})
    assert response.status_code == 422
    assert retrieval.calls == [] and await count(factory, SimilarityCheck) == 0


@pytest.mark.parametrize(
    "override",
    [
        {"recipients": ["api.openalex.org"]},
        {"recipients": [*CONSENT_RECIPIENTS, "example.com"]},
        {"consent_text_version": "2020-01-01.0"},
    ],
)
async def test_a_consent_mismatch_is_409_with_the_server_values_and_a_preview(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
    override: dict[str, Any],
) -> None:
    document_id = await make_document(
        factory,
        text="\n\n".join(["quantum the entanglement the lattice the photonic"] * 3),
    )
    response = await start(client, document_id, {"open_sources": consent(**override)})
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["recipients"] == list(CONSENT_RECIPIENTS)
    assert details["consent_text_version"] == service.SIMILARITY_CONSENT_TEXT_VERSION
    assert details["query_preview"]
    assert retrieval.calls == [] and await count(factory, SimilarityCheck) == 0


async def test_the_recipients_are_compared_as_a_set(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    reordered = {"open_sources": consent(recipients=list(reversed(CONSENT_RECIPIENTS)))}
    assert (await start(client, document_id, reordered)).status_code == 202
    await drain()


async def test_unverified_terms_are_409(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "")
    response = await start(client, document_id, {"open_sources": consent()})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_terms_unverified"


async def test_open_sources_can_be_switched_off_separately(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(settings, "SIMILARITY_OPEN_SOURCES_ENABLED", False)
    assert (
        await start(client, document_id, {"open_sources": consent()})
    ).status_code == 409
    assert (await start(client, document_id, {})).status_code == 202
    await drain()


async def test_the_documented_example_body_is_accepted(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    own = await make_document(factory, filename="mine.txt")
    body = OPEN_BODY | {"comparison_document_ids": [str(own)]}
    check_id = await run_to_end(client, document_id, body)
    row = (await client.get(f"/api/v1/similarity-checks/{check_id}")).json()
    assert row["options"]["open_sources"] is True
    assert row["consent_text_version"] == service.SIMILARITY_CONSENT_TEXT_VERSION
    assert len(retrieval.calls) == 1


# ---- quotas -------------------------------------------------------------------------


async def test_the_eleventh_check_in_a_day_is_429_even_after_deleting_earlier_ones(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    for _ in range(10):
        check_id = await run_to_end(client, document_id)
        assert (
            await client.delete(f"/api/v1/similarity-checks/{check_id}")
        ).status_code == 204
    refused = await start(client, document_id)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "user_daily"
    assert await count(factory, UsageLedger) == 10


async def test_a_refused_admission_leaves_no_ledger_row(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    monkeypatch.setattr(
        service, "OPEN_SOURCE_LIMITS", service.JobLimits(global_daily_count=0)
    )
    response = await start(client, document_id, {"open_sources": consent()})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "global_daily_count"
    assert await count(factory, UsageLedger) == 0
    assert await count(factory, SimilarityCheck) == 0


async def test_a_local_check_writes_no_open_source_ledger_row(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    await run_to_end(client, document_id)
    async with factory() as session:
        kinds = (await session.execute(select(UsageLedger.kind))).scalars().all()
    assert kinds == ["similarity_check"]


async def test_an_open_source_check_writes_both_ledger_rows(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    await run_to_end(client, document_id, {"open_sources": consent()})
    async with factory() as session:
        kinds = (await session.execute(select(UsageLedger.kind))).scalars().all()
    assert sorted(kinds) == ["similarity_check", "similarity_open_sources"]


async def test_a_busy_worker_refuses_new_checks_without_reserving_quota(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    sleepers = {
        asyncio.create_task(asyncio.sleep(60), name=f"similarity:{uuid.uuid4()}")
        for _ in range(similarity_routes.MAX_QUEUED_PER_WORKER)
    }
    app.state.document_jobs.update(sleepers)
    refused = await start(client, document_id)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "worker_busy"
    assert await count(factory, UsageLedger) == 0
    for task in sleepers:
        task.cancel()
    app.state.document_jobs.difference_update(sleepers)


async def test_a_third_concurrent_check_in_one_org_is_refused(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    retrieval.gate.clear()
    open_body = {"open_sources": consent()}
    codes = [
        (await start(client, document_id, open_body)).status_code for _ in range(3)
    ]
    assert codes == [202, 202, 429]
    retrieval.gate.set()
    await drain()


# ---- runs, reads and the report --------------------------------------------------------


async def test_a_check_runs_to_a_stored_result_and_a_text_report(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    await make_document(factory, text=SHARED + " tail-only", filename="notes.txt")
    check_id = await run_to_end(client, document_id, {"own_uploads": True})
    body = (await client.get(f"/api/v1/similarity-checks/{check_id}")).json()
    assert body["status"] == "succeeded" and body["match_total"] == 1
    (match,) = body["matches"]
    assert match["from_user_upload"] is True and match["source_origin"] == "own_upload"
    report = await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    assert report.headers["content-type"].startswith("text/plain")
    assert "matches your uploaded document notes.txt" in report.text
    for pattern in FORBIDDEN:
        assert not re.search(pattern, report.text, re.IGNORECASE)
    assert "it is not a finding of misconduct" in report.text


async def test_the_report_of_a_zero_match_run_states_its_coverage(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    retrieval.inner, _ = stub_retrieve(
        (candidate("W1", text="nothing alike " * 30),),
        openalex=SourceCoverage(candidates=1),
    )
    check_id = await run_to_end(client, document_id, {"open_sources": consent()})
    report = await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    assert "No matching text was found in the 1 sources searched." in report.text
    assert "documents of other users and organizations" in report.text


async def test_an_unfinished_check_has_no_report_yet(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    retrieval.gate.clear()
    response = await start(client, document_id, {"open_sources": consent()})
    check_id = response.json()["id"]
    await retrieval.entered.wait()
    report = await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    assert report.status_code == 409
    assert (await client.get(f"/api/v1/similarity-checks/{check_id}")).json()[
        "matches"
    ] == []
    retrieval.gate.set()
    await drain()


async def test_matches_are_paged(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    await make_document(factory, text=SHARED)
    check_id = await run_to_end(client, document_id, {"own_uploads": True})
    page = await client.get(
        f"/api/v1/similarity-checks/{check_id}", params={"offset": 1, "limit": 5}
    )
    assert page.json()["matches"] == [] and page.json()["match_total"] == 1


async def test_the_list_is_capped_at_fifty_and_newest_first(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    first = await run_to_end(client, document_id)
    second = await run_to_end(client, document_id)
    listed = (await client.get(checks_url(document_id), params={"limit": 50})).json()
    assert [row["id"] for row in listed] == [second, first]
    assert (
        await client.get(checks_url(document_id), params={"limit": 51})
    ).status_code == 422


# ---- delete, isolation, lifecycle --------------------------------------------------------


async def test_deleting_a_running_check_cancels_its_task_and_keeps_the_quota_spent(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
) -> None:
    document_id = await make_document(factory)
    retrieval.gate.clear()
    check_id = (await start(client, document_id, {"open_sources": consent()})).json()[
        "id"
    ]
    await retrieval.entered.wait()
    assert (
        await client.delete(f"/api/v1/similarity-checks/{check_id}")
    ).status_code == 204
    assert retrieval.cancelled is True
    assert (
        await client.get(f"/api/v1/similarity-checks/{check_id}")
    ).status_code == 404
    assert await count(factory, SimilarityMatch) == 0
    assert await count(factory, UsageLedger) == 2


async def test_another_user_gets_404_on_every_route(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    check_id = await run_to_end(client, document_id)
    Caller.user, Caller.org = USER_B, ORG_B
    for method, url in (
        ("GET", checks_url(document_id)),
        ("POST", checks_url(document_id)),
        ("GET", f"/api/v1/similarity-checks/{check_id}"),
        ("GET", f"/api/v1/similarity-checks/{check_id}/report"),
        ("DELETE", f"/api/v1/similarity-checks/{check_id}"),
    ):
        response = await client.request(
            method, url, json={} if method == "POST" else None
        )
        assert response.status_code == 404, (method, url)
    Caller.user, Caller.org = USER_A, ORG_A
    assert (
        await client.get(f"/api/v1/similarity-checks/{check_id}")
    ).status_code == 200


async def test_a_check_whose_document_expired_reads_as_missing(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    from src.models.db.uploaded_document import UploadedDocument

    document_id = await make_document(factory)
    check_id = await run_to_end(client, document_id)
    async with factory() as session:
        row = await session.get(UploadedDocument, document_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (
        await client.get(f"/api/v1/similarity-checks/{check_id}")
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    ).status_code == 404


async def test_a_document_cannot_be_its_own_comparison_document(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    body = {"comparison_document_ids": [str(document_id)]}
    response = await start(client, document_id, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "comparison_includes_the_document"
    assert await count(factory, SimilarityCheck) == 0


async def test_only_similarity_tasks_count_toward_the_worker_cap(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    others = {
        asyncio.create_task(asyncio.sleep(60), name="critique:x") for _ in range(5)
    }
    app.state.document_jobs.update(others)
    assert (await start(client, document_id)).status_code == 202
    for task in others:
        task.cancel()
    app.state.document_jobs.difference_update(others)


async def test_concurrent_starts_cannot_pass_the_worker_cap_together(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    retrieval: Retrieval,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "SIMILARITY_LIMITS", service.JobLimits())
    document_id = await make_document(factory)
    retrieval.gate.clear()
    responses = await asyncio.gather(
        *(start(client, document_id, {"open_sources": consent()}) for _ in range(12))
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(202) == similarity_routes.MAX_QUEUED_PER_WORKER
    assert set(codes) == {202, 429}
    retrieval.gate.set()
    await drain()


async def test_error_responses_are_not_cached(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    mismatch = await start(
        client, document_id, {"open_sources": consent(consent_text_version="old")}
    )
    assert mismatch.status_code == 409
    assert mismatch.headers["cache-control"] == "no-store"
    missing = await client.get(f"/api/v1/similarity-checks/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"


async def test_deleting_a_check_that_never_started_gives_its_quota_back(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    await app.state.similarity_semaphore.acquire()
    try:
        check_id = (await start(client, document_id)).json()["id"]
        assert await count(factory, UsageLedger) == 1
        deleted = await client.delete(f"/api/v1/similarity-checks/{check_id}")
        assert deleted.status_code == 204
    finally:
        app.state.similarity_semaphore.release()
    assert await count(factory, UsageLedger) == 0


async def test_responses_are_never_cached(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    document_id = await make_document(factory)
    accepted = await start(client, document_id)
    assert accepted.headers["cache-control"] == "no-store"
    await drain()
    check_id = accepted.json()["id"]
    for url in (
        f"/api/v1/similarity-checks/{check_id}",
        f"/api/v1/similarity-checks/{check_id}/report",
        checks_url(document_id),
    ):
        assert (await client.get(url)).headers["cache-control"] == "no-store", url


async def test_duplicate_comparison_ids_count_once(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    other = await make_document(factory, text=SHARED + " tail")
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(SHARED + " tail"))
    body = {"comparison_document_ids": [str(other), str(other)]}
    check_id = await run_to_end(client, document_id, body)
    row = (await client.get(f"/api/v1/similarity-checks/{check_id}")).json()
    assert row["options"]["comparison_document_ids"] == [str(other)]


async def test_the_comparison_budget_is_inclusive(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(factory)
    other = await make_document(factory, text=SHARED)
    body = {"comparison_document_ids": [str(other)]}
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(SHARED))
    assert (await start(client, document_id, body)).status_code == 202
    await drain()
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(SHARED) - 1)
    assert (await start(client, document_id, body)).status_code == 422


async def test_the_report_lists_every_match_not_just_one_page(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    from src.core.memory_encryption import document_cipher
    from src.repositories.similarity_repository import MatchRecord, SimilarityRepository

    document_id = await make_document(factory)
    check_id = uuid.UUID(await run_to_end(client, document_id))
    records = [
        MatchRecord(
            kind="verbatim",
            attribution="unattributed",
            from_user_upload=False,
            doc_start=0,
            doc_end=5,
            tokens=12,
            source_origin="openalex",
            source_document_id=None,
            source_id=f"W{i}",
            source_title=f"Source number {i}",
            source_year=None,
            source_licence={},
            source_text_kind="full_text",
            src_start=0,
            src_end=5,
            source_excerpt=None,
        )
        for i in range(250)
    ]
    async with factory() as session:
        await SimilarityRepository(session, document_cipher()).add_matches(
            check_id=check_id, organization_id=ORG_A, user_id=USER_A, matches=records
        )
        await session.commit()
    report = await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    assert report.text.count("verbatim, 12 tokens") == 250


async def test_a_report_for_an_expired_document_is_404(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    from src.models.db.uploaded_document import UploadedDocument

    document_id = await make_document(factory)
    check_id = await run_to_end(client, document_id)
    async with factory() as session:
        row = await session.get(UploadedDocument, document_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    report = await client.get(f"/api/v1/similarity-checks/{check_id}/report")
    assert report.status_code == 404
