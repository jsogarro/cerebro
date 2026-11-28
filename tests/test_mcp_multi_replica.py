"""Multi-replica statelessness and deployment enablement tests for /mcp."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import yaml
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.routing import Route

from src.api.services.research_kernel import compose_application_research_kernel
from src.auth.jwt_service import JWTService
from src.auth.models import PAT_SCOPE_READ
from src.auth.pat_service import (
    PersonalAccessTokenService,
    generate_personal_access_token,
)
from src.mcp.server import build_mcp_server
from src.middleware.auth_middleware import (
    AuthMiddleware,
    get_jwt_service,
    get_pat_service,
)
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.personal_access_token import PersonalAccessToken
from src.models.db.run_lifecycle import AgentRun, AgentRunTask
from src.models.db.user import User
from src.repositories.personal_access_token_repository import (
    PersonalAccessTokenRepository,
)
from src.security.audit_middleware import AuditTrailMiddleware

REPO_ROOT = Path(__file__).resolve().parents[1]
ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> bool:
        self._store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0


class _SharedAuditLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def log_event(self, **kwargs: Any) -> str:
        self.events.append(kwargs)
        return f"evt-{len(self.events)}"

    async def flush_buffer(self) -> None:
        return None

    def record_rejection(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _SharedBackend:
    def __init__(self) -> None:
        self.executions: dict[str, tuple[str, Any, dict[str, Any]]] = {
            "exec-shared": (
                str(ORG_A),
                SimpleNamespace(
                    execution_id="exec-shared",
                    run_id="run-shared",
                    status="completed",
                    progress_percentage=100.0,
                    current_phase="completed",
                    supervisor_type="research",
                    workers_used=1,
                    routing_decision={"strategy": "balanced"},
                    execution_plan=None,
                    execution_time_seconds=1.2,
                    errors=[],
                ),
                {
                    "summary": "Shared synthesis",
                    "sources": [{"title": "Paper", "url": "https://arxiv.org/abs/1"}],
                },
            )
        }

    async def start_research_execution(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> Any:
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[1]

    async def get_execution_results(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[2]

    async def resume_execution(self, *args: Any, **kwargs: Any) -> str | None:
        return None

    async def restore_active_executions(self) -> int:
        return 0

    async def close(self) -> None:
        return None


class _AlternatingASGITransport(httpx2.AsyncBaseTransport):
    """Routes consecutive HTTP requests across two independent replica apps."""

    def __init__(self, app_a: FastAPI, app_b: FastAPI) -> None:
        self._transports = (
            httpx2.ASGITransport(app=app_a),
            httpx2.ASGITransport(app=app_b),
        )
        self.routed_to: list[str] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        index = len(self.routed_to) % 2
        self.routed_to.append("replica-a" if index == 0 else "replica-b")
        return await self._transports[index].handle_async_request(request)

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()


def _build_replica_app(
    *,
    kernel: Any,
    audit_logger: _SharedAuditLogger,
    session_factory: async_sessionmaker[AsyncSession],
    jwt_service: JWTService,
    pat_service: PersonalAccessTokenService,
) -> tuple[FastAPI, Any]:
    from src.api.main import _mount_mcp_app

    replica = FastAPI()
    replica.state.audit_logger = audit_logger
    replica.middleware("http")(AuditTrailMiddleware())
    replica.add_middleware(AuthMiddleware)
    replica.dependency_overrides[get_jwt_service] = lambda: jwt_service
    replica.dependency_overrides[get_pat_service] = lambda: pat_service

    mcp_server = build_mcp_server(
        kernel=kernel,
        audit_logger=audit_logger,
        session_factory=session_factory,
    )
    mcp_app, mcp_route = _mount_mcp_app(replica, mcp_server)
    assert isinstance(mcp_route, Route)
    return replica, mcp_app


def test_deployments_enable_mcp_server() -> None:
    configmap = yaml.safe_load(
        (REPO_ROOT / "k8s" / "configmap.yaml").read_text(encoding="utf-8")
    )
    assert configmap["data"].get("MCP_SERVER_ENABLED") == "true"

    dev_compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    dev_env = dev_compose["services"]["api"]["environment"]
    assert any(
        entry == "MCP_SERVER_ENABLED=${MCP_SERVER_ENABLED:-true}"
        or entry == "MCP_SERVER_ENABLED=true"
        for entry in dev_env
    ), dev_env

    prod_compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )
    prod_env = prod_compose["services"]["api"]["environment"]
    assert any(
        entry == "MCP_SERVER_ENABLED=${MCP_SERVER_ENABLED:-true}"
        or entry == "MCP_SERVER_ENABLED=true"
        for entry in prod_env
    ), prod_env


async def test_multi_replica_alternating_requests_succeed_without_session_affinity() -> (
    None
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                User.__table__,
                PersonalAccessToken.__table__,
                AgentRun.__table__,
                AgentRunTask.__table__,
                AgentArtifact.__table__,
                AgentEvidence.__table__,
                AgentClaimSupport.__table__,
            ],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    plaintext, digest, prefix = generate_personal_access_token()
    async with factory() as session:
        session.add(
            User(
                id=USER_A,
                email="alice@example.com",
                username="alice",
                hashed_password="x",
                organization_id=ORG_A,
                role="researcher",
                is_active=True,
            )
        )
        session.add_all(
            [
                AgentRun(
                    run_id="run-shared",
                    tenant_id=str(ORG_A),
                    organization_id=ORG_A,
                    workflow_definition_id="research.single-worker",
                    workflow_definition_version="1.0.0",
                    routing_policy_id="masr.default",
                    routing_policy_version="1.0.0",
                    idempotency_key="idem-shared",
                    requested_by=str(USER_A),
                    status="succeeded",
                ),
                AgentRunTask(
                    task_id="task-shared",
                    run_id="run-shared",
                    organization_id=ORG_A,
                    task_key="search",
                    task_type="research",
                    objective="Search",
                    idempotency_key="task-idem-shared",
                    status="succeeded",
                ),
                AgentArtifact(
                    artifact_id="snap-shared",
                    run_id="run-shared",
                    organization_id=ORG_A,
                    kind="source_snapshot",
                    media_type="text/plain",
                    status="final",
                    storage_uri="/tmp/snap",
                    content_sha256="a" * 64,
                    trust="external_untrusted",
                    producer="search-evidence-recorder",
                    producer_kind="system",
                ),
            ]
        )
        await session.flush()
        session.add(
            AgentEvidence(
                id=uuid.UUID(int=1),
                evidence_id="ev-shared-1",
                run_id="run-shared",
                task_id="task-shared",
                organization_id=ORG_A,
                source_type="search_result",
                source_uri="https://arxiv.org/abs/1",
                snapshot_artifact_id="snap-shared",
                content_sha256="a" * 64,
                locator="bytes:0-10",
                locator_scheme="bytes",
                locator_start=0,
                locator_end=10,
                trust="external_untrusted",
                producer_kind="system",
                parent_evidence_ids=[],
                acquired_at=now,
            )
        )
        pat_row = await PersonalAccessTokenRepository(session).create(
            user_id=USER_A,
            organization_id=ORG_A,
            name="multi-replica",
            token_hash=digest,
            token_prefix=prefix,
            scopes=[PAT_SCOPE_READ],
            issued_at_ns=time.time_ns(),
            created_at=now,
            expires_at=now + timedelta(days=30),
        )
        await session.commit()
        pat_id = str(pat_row.id)

    shared_redis = _FakeRedis()
    jwt_service = JWTService(redis_client=shared_redis)  # type: ignore[arg-type]
    pat_service = PersonalAccessTokenService(session_factory=factory)
    shared_audit = _SharedAuditLogger()
    shared_kernel = compose_application_research_kernel(_SharedBackend())

    replica_a, mcp_app_a = _build_replica_app(
        kernel=shared_kernel,
        audit_logger=shared_audit,
        session_factory=factory,
        jwt_service=jwt_service,
        pat_service=pat_service,
    )
    replica_b, mcp_app_b = _build_replica_app(
        kernel=shared_kernel,
        audit_logger=shared_audit,
        session_factory=factory,
        jwt_service=jwt_service,
        pat_service=pat_service,
    )

    alternating = _AlternatingASGITransport(replica_a, replica_b)

    def _client_factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("verify", None)
        return httpx2.AsyncClient(
            transport=alternating,
            base_url="http://testserver",
            **kwargs,
        )

    transport = StreamableHttpTransport(
        url="http://testserver/mcp",
        headers={"Authorization": f"Bearer {plaintext}"},
        httpx_client_factory=_client_factory,
    )

    try:
        async with (
            mcp_app_a.lifespan(replica_a),
            mcp_app_b.lifespan(replica_b),
            httpx2.AsyncClient(
                transport=alternating, base_url="http://testserver"
            ) as raw_client,
        ):
            base_headers = {
                "Authorization": f"Bearer {plaintext}",
                "Accept": "application/json, text/event-stream",
            }
            # 1. initialize -> replica-a (must not mint a per-replica mcp-session-id)
            init_resp = await raw_client.post(
                "/mcp",
                headers=base_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "multi-replica-test", "version": "1.0"},
                    },
                },
            )
            assert init_resp.status_code == 200, init_resp.text
            assert "mcp-session-id" not in init_resp.headers
            session_headers = {**base_headers}
            if sid := init_resp.headers.get("mcp-session-id"):
                session_headers["mcp-session-id"] = sid

            # 2. notifications/initialized -> replica-b
            notif_resp = await raw_client.post(
                "/mcp",
                headers=session_headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            assert notif_resp.status_code in (200, 202), notif_resp.text

            # 3. tools/list -> replica-a
            list_resp = await raw_client.post(
                "/mcp",
                headers=session_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
            assert list_resp.status_code == 200, list_resp.text
            listed_names = {
                tool["name"] for tool in list_resp.json()["result"]["tools"]
            }
            assert listed_names == {
                "get_research_status",
                "get_research_results",
                "get_research_evidence",
            }

            # 4-6. Alternating tool calls across replica-b, replica-a, replica-b
            async with Client(transport) as client:
                status_res = await client.call_tool(
                    "get_research_status", {"execution_id": "exec-shared"}
                )
                results_res = await client.call_tool(
                    "get_research_results", {"execution_id": "exec-shared"}
                )
                evidence_res = await client.call_tool(
                    "get_research_evidence", {"run_id": "run-shared"}
                )

        assert status_res.data["run_id"] == "run-shared"
        assert results_res.data["run_id"] == "run-shared"
        assert [item["evidence_id"] for item in evidence_res.data["items"]] == [
            "ev-shared-1"
        ]
        # Both replicas must have served multiple consecutive requests in the session.
        assert alternating.routed_to.count("replica-a") >= 3
        assert alternating.routed_to.count("replica-b") >= 3
        tool_events = [
            evt
            for evt in shared_audit.events
            if str(evt.get("action", "")).startswith("mcp.tool:")
        ]
        assert len(tool_events) == 3
        assert all(evt["metadata"]["pat_id"] == pat_id for evt in tool_events)
    finally:
        await engine.dispose()
