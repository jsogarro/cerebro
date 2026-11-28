"""Integration tests for Streamable HTTP MCP mounted on src.api.main:app."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.services.research_kernel import compose_application_research_kernel
from src.auth.models import TokenPayload
from src.core.config import settings
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.run_lifecycle import AgentRun, AgentRunTask
from src.models.db.session import get_session

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"


def _payload(user_id: str, org_id: str | None) -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub=user_id,
        email="user@example.com",
        roles=["researcher"],
        permissions=["read:projects"],
        organization_id=org_id,
        jti=f"jti-{user_id}",
        iat=now,
        exp=now + timedelta(minutes=15),
    )


class _TokenRouterJWTService:
    def __init__(self, tokens: dict[str, TokenPayload]) -> None:
        self._tokens = tokens

    async def validate_token(self, token: str) -> TokenPayload:
        payload = self._tokens.get(token)
        if payload is None:
            raise JWTError("invalid token")
        return payload


class _StubAuditLogger:
    def __init__(self, **_kwargs: Any) -> None:
        self.events: list[dict[str, Any]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def log_event(self, **kwargs: Any) -> str:
        self.events.append(kwargs)
        return f"evt-{len(self.events)}"

    async def flush_buffer(self) -> None:
        return None

    def record_rejection(self, *args: Any, **kwargs: Any) -> None:
        return None


class _StubBackend:
    def __init__(self) -> None:
        self.executions: dict[str, tuple[str, Any, dict[str, Any] | None]] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    async def start_research_execution(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> Any:
        self.calls.append(("status", execution_id, organization_id))
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[1]

    async def get_execution_results(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        self.calls.append(("results", execution_id, organization_id))
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[2]

    async def resume_execution(self, *args: Any, **kwargs: Any) -> str | None:
        return None

    async def restore_active_executions(self) -> int:
        return 0

    def start_expired_run_reconciliation(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
async def seeded_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Base.metadata.tables[name]
        for name in (
            "agent_runs",
            "agent_run_tasks",
            "agent_artifacts",
            "agent_evidence",
            "agent_claim_supports",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 11, 27, 14, 0, 0, tzinfo=UTC)
    async with factory() as session:
        session.add_all(
            [
                AgentRun(
                    run_id="run-org-a",
                    tenant_id=str(ORG_A),
                    organization_id=ORG_A,
                    workflow_definition_id="wf-1",
                    workflow_definition_version="1.0.0",
                    routing_policy_id="rp-1",
                    routing_policy_version="1.0.0",
                    idempotency_key="idem-a",
                    requested_by=USER_A,
                    status="succeeded",
                ),
                AgentRunTask(
                    task_id="task-org-a",
                    run_id="run-org-a",
                    organization_id=ORG_A,
                    task_key="search",
                    task_type="literature_review",
                    objective="Search literature",
                    idempotency_key="task-idem-a",
                    status="succeeded",
                ),
                AgentArtifact(
                    artifact_id="snap-a",
                    run_id="run-org-a",
                    organization_id=ORG_A,
                    kind="source_snapshot",
                    media_type="text/plain",
                    status="final",
                    storage_uri="/var/lib/cerebro/source_snapshots/aa/" + "a" * 64,
                    content_sha256="a" * 64,
                    trust="external_untrusted",
                    producer="search-evidence-recorder",
                    producer_kind="system",
                ),
                AgentArtifact(
                    artifact_id="report-a",
                    run_id="run-org-a",
                    organization_id=ORG_A,
                    kind="research_report",
                    media_type="application/json",
                    status="final",
                    storage_uri="memory://artifacts/report-a",
                    content_sha256="b" * 64,
                    trust="derived_untrusted",
                    producer="direct-execution-service",
                    producer_kind="system",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AgentEvidence(
                    id=uuid.UUID(int=1),
                    evidence_id="ev-1",
                    run_id="run-org-a",
                    task_id="task-org-a",
                    organization_id=ORG_A,
                    source_type="search_result",
                    source_uri="https://arxiv.org/abs/2401.00001",
                    snapshot_artifact_id="snap-a",
                    content_sha256="a" * 64,
                    locator="bytes:0-42",
                    locator_scheme="bytes",
                    locator_start=0,
                    locator_end=42,
                    trust="external_untrusted",
                    producer_kind="system",
                    parent_evidence_ids=[],
                    acquired_at=now,
                ),
                AgentClaimSupport(
                    id=uuid.UUID(int=2),
                    claim_support_id="cs-1",
                    run_id="run-org-a",
                    artifact_id="report-a",
                    claim_id="claim-1",
                    organization_id=ORG_A,
                    claim_text="Retrieval improves grounding.",
                    status="unsupported",
                    evidence_ids=[],
                    evidence_count=0,
                    absent_evidence_reason="not_attempted",
                    evaluator_id="claim-support-resolver",
                    evaluator_version="1.0.0",
                    producer_kind="system",
                    explanation="No evaluator verdicts supplied.",
                    evaluated_at=now,
                ),
            ]
        )
        await session.commit()

    yield factory
    await engine.dispose()


def _mcp_client(app: Any, token: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("verify", None)
        merged = {**(kwargs.pop("headers", None) or {}), **headers}
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=merged,
            **kwargs,
        )

    transport = StreamableHttpTransport(
        url="http://test/mcp",
        headers=headers,
        httpx_client_factory=_factory,
    )
    return Client(transport)


@pytest.mark.asyncio
async def test_mcp_http_end_to_end_auth_tenancy_audit_and_rest_parity(
    monkeypatch: pytest.MonkeyPatch,
    seeded_db: async_sessionmaker[AsyncSession],
) -> None:
    from src.api import main as main_module
    from src.api.services import direct_execution_service as des_module
    from src.api.services import research_kernel as rk_module

    backend = _StubBackend()
    backend.executions["exec-a"] = (
        str(ORG_A),
        SimpleNamespace(
            execution_id="exec-a",
            run_id="run-org-a",
            status="completed",
            progress_percentage=100.0,
            current_phase="completed",
            supervisor_type="research",
            workers_used=1,
            routing_decision={"strategy": "balanced"},
            execution_plan=None,
            execution_time_seconds=2.5,
            errors=[],
        ),
        {
            "summary": "Synthesis",
            "sources": [{"title": "Paper", "abstract": "Text"}],
        },
    )
    stub_audit = _StubAuditLogger()

    async def _noop() -> None:
        return None

    async def _fake_masr_create(_settings: Any) -> Any:
        return SimpleNamespace(router=SimpleNamespace(), close=_noop)

    async def _session_override():
        async with seeded_db() as session:
            yield session

    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", True)
    monkeypatch.setattr(main_module.MASRRuntime, "create", _fake_masr_create)
    monkeypatch.setattr(main_module.event_publisher, "initialize", _noop)
    monkeypatch.setattr(main_module.event_publisher, "shutdown", _noop)
    monkeypatch.setattr(main_module, "AuditLogger", lambda **_kw: stub_audit)
    monkeypatch.setattr("src.models.db.session.init_db", _noop)
    monkeypatch.setattr("src.models.db.session.close_db", _noop)
    monkeypatch.setattr("src.models.db.session.get_session_factory", lambda: seeded_db)
    monkeypatch.setattr(
        des_module, "configure_direct_execution_service", lambda **_kw: backend
    )
    monkeypatch.setattr(
        rk_module,
        "compose_application_research_kernel",
        lambda _b, _a=None: compose_application_research_kernel(backend),
    )

    jwt_service = _TokenRouterJWTService(
        {
            "token-org-a": _payload(USER_A, str(ORG_A)),
            "token-org-b": _payload(USER_B, str(ORG_B)),
            "token-no-org": _payload(USER_A, None),
        }
    )

    app = main_module.app
    app.dependency_overrides[get_jwt_service] = lambda: jwt_service
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[des_module.get_application_direct_execution_service] = (
        lambda: backend
    )
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as rest:
                # Step 3: unauthenticated POST /mcp returns 401 from AuthMiddleware
                unauth = await rest.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                assert unauth.status_code == 401

                # Step 7: POST /mcp (no trailing slash) returns 200, never 3xx
                init_resp = await rest.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer token-org-a",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1.0"},
                        },
                    },
                )
                assert init_resp.status_code == 200

                # REST baseline for parity checks
                rest_status = await rest.get(
                    "/api/v1/query/execution/exec-a/status",
                    headers={"Authorization": "Bearer token-org-a"},
                )
                assert rest_status.status_code == 200
                rest_status_body = rest_status.json()
                assert rest_status_body["run_id"] == "run-org-a"

                rest_evidence = await rest.get(
                    "/api/v1/runs/run-org-a/evidence",
                    headers={"Authorization": "Bearer token-org-a"},
                )
                assert rest_evidence.status_code == 200
                rest_evidence_body = rest_evidence.json()

                # Foreign run on REST evidence -> 404, never 200 []
                rest_foreign_evidence = await rest.get(
                    "/api/v1/runs/run-org-a/evidence",
                    headers={"Authorization": "Bearer token-org-b"},
                )
                assert rest_foreign_evidence.status_code == 404

            # Authenticated MCP client for Org A
            async with _mcp_client(app, "token-org-a") as client_a:
                mcp_status = await client_a.call_tool(
                    "get_research_status", {"execution_id": "exec-a"}
                )
                assert {
                    k: v for k, v in mcp_status.data.items() if k != "timestamp"
                } == {k: v for k, v in rest_status_body.items() if k != "timestamp"}

                mcp_results = await client_a.call_tool(
                    "get_research_results", {"execution_id": "exec-a"}
                )
                assert mcp_results.data["run_id"] == "run-org-a"
                assert mcp_results.data["content_trust"] == "external_untrusted"
                assert (
                    mcp_results.data["results"]["sources"][0]["trust"]
                    == "external_untrusted"
                )
                verdicts = mcp_results.data["claim_verdicts"]
                assert verdicts["counts"]["unsupported"] == 1
                assert verdicts["claims"][0]["claim_id"] == "claim-1"
                assert (
                    verdicts["claims"][0]["absent_evidence_reason"] == "not_attempted"
                )

                mcp_evidence = await client_a.call_tool(
                    "get_research_evidence", {"run_id": "run-org-a"}
                )
                assert mcp_evidence.data == rest_evidence_body

            # Cross-tenant MCP client for Org B -> byte-identical not-found errors
            async with _mcp_client(app, "token-org-b") as client_b:
                with pytest.raises(ToolError) as err_foreign_ev:
                    await client_b.call_tool(
                        "get_research_evidence", {"run_id": "run-org-a"}
                    )
                with pytest.raises(ToolError) as err_unknown_ev:
                    await client_b.call_tool(
                        "get_research_evidence", {"run_id": "run-does-not-exist"}
                    )
                assert str(err_foreign_ev.value) == str(err_unknown_ev.value)

            # Missing org claim -> refused before kernel/DB
            calls_before = len(backend.calls)
            async with _mcp_client(app, "token-no-org") as client_no_org:
                with pytest.raises(ToolError, match="organization"):
                    await client_no_org.call_tool(
                        "get_research_status", {"execution_id": "exec-a"}
                    )
            assert len(backend.calls) == calls_before

            mcp_audit_actions = [
                (e["action"], e["result"])
                for e in stub_audit.events
                if str(e.get("action", "")).startswith("mcp.tool:")
            ]
            assert ("mcp.tool:get_research_status", "success") in mcp_audit_actions
            assert ("mcp.tool:get_research_results", "success") in mcp_audit_actions
            assert ("mcp.tool:get_research_evidence", "success") in mcp_audit_actions
            assert ("mcp.tool:get_research_evidence", "failure") in mcp_audit_actions
            assert ("mcp.tool:get_research_status", "failure") in mcp_audit_actions
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(
            des_module.get_application_direct_execution_service, None
        )


@pytest.mark.asyncio
async def test_mcp_disabled_by_default_returns_404_and_does_not_import_fastmcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import main as main_module

    assert settings.MCP_SERVER_ENABLED is False
    jwt_service = _TokenRouterJWTService({"token-org-a": _payload(USER_A, str(ORG_A))})
    stub_audit = _StubAuditLogger()

    app = main_module.app
    app.dependency_overrides[get_jwt_service] = lambda: jwt_service
    previous_audit = getattr(app.state, "audit_logger", None)
    app.state.audit_logger = stub_audit
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/mcp",
                headers={"Authorization": "Bearer token-org-a"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        if previous_audit is None:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous_audit

    # Subprocess import guard: importing src.api.main must not import fastmcp
    script = textwrap.dedent(
        """
        import builtins
        _real = builtins.__import__
        def _guard(name, *args, **kwargs):
            if name.split(".")[0] == "fastmcp":
                raise AssertionError("src.api.main eagerly imported fastmcp")
            return _real(name, *args, **kwargs)
        builtins.__import__ = _guard
        import src.api.main
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
