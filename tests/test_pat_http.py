"""HTTP integration tests for personal access tokens and /mcp-only enforcement."""

from __future__ import annotations

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
from src.auth.jwt_service import JWTService
from src.auth.models import PAT_PREFIX, PAT_SCOPE_START, TokenPayload
from src.auth.pat_service import PersonalAccessTokenService
from src.core.config import settings
from src.middleware.auth_middleware import (
    PUBLIC_ROUTE_ALLOWLIST,
    get_jwt_service,
    get_pat_service,
)
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.personal_access_token import PersonalAccessToken
from src.models.db.run_lifecycle import AgentRun, AgentRunTask
from src.models.db.session import get_session
from src.models.db.user import User

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> bool:
        self._store[key] = value
        return True

    async def setex(self, key: str, _ttl: Any, value: str) -> bool:
        self._store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int = 100
    ) -> tuple[int, list[str]]:
        prefix = (match or "").rstrip("*")
        return 0, [key for key in self._store if key.startswith(prefix)]


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

    def start_expired_run_reconciliation(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _HybridJWTService(JWTService):
    """Real revocation storage paired with deterministic test bearer tokens."""

    def __init__(
        self, tokens: dict[str, TokenPayload], redis_client: _FakeRedis
    ) -> None:
        super().__init__(redis_client=redis_client)  # type: ignore[arg-type]
        self._tokens = tokens

    async def validate_token(
        self,
        token: str,
        token_type: str = "access",
        verify_blacklist: bool = True,
    ) -> TokenPayload:
        payload = self._tokens.get(token)
        if payload is None:
            raise JWTError("invalid token")
        return payload


def _jwt_payload(
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    roles: list[str] | None = None,
) -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub=str(user_id),
        email=f"{user_id}@example.com",
        roles=roles or ["researcher"],
        permissions=[],
        organization_id=str(org_id),
        jti=f"jti-{user_id}",
        iat=now,
        exp=now + timedelta(minutes=15),
    )


def _asgi_client_factory(app: Any) -> Any:
    def _factory(
        headers: dict[str, str] | None = None,
        timeout: Any = None,
        auth: Any = None,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        kwargs.pop("verify", None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    return _factory


def _patch_app_lifespan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    audit_logger: _StubAuditLogger,
    backend: _StubBackend,
) -> None:
    from src.api import main as main_module
    from src.api.services import direct_execution_service as des_module
    from src.api.services import research_kernel as rk_module

    async def _noop() -> None:
        return None

    async def _fake_masr_create(_settings: Any) -> Any:
        return SimpleNamespace(router=SimpleNamespace(), close=_noop)

    monkeypatch.setattr(main_module.MASRRuntime, "create", _fake_masr_create)
    monkeypatch.setattr(main_module.event_publisher, "initialize", _noop)
    monkeypatch.setattr(main_module.event_publisher, "shutdown", _noop)
    monkeypatch.setattr(main_module, "AuditLogger", lambda **_kwargs: audit_logger)
    monkeypatch.setattr("src.models.db.session.init_db", _noop)
    monkeypatch.setattr("src.models.db.session.close_db", _noop)
    monkeypatch.setattr(
        "src.models.db.session.get_session_factory", lambda: session_factory
    )
    monkeypatch.setattr(
        "src.models.db.session.get_maintenance_session_factory", lambda: session_factory
    )
    monkeypatch.setattr(
        des_module, "configure_direct_execution_service", lambda **_kw: backend
    )
    monkeypatch.setattr(
        rk_module,
        "compose_application_research_kernel",
        lambda _b, _a=None: compose_application_research_kernel(backend),
    )
    monkeypatch.setattr(main_module, "_start_document_purge", lambda *_a, **_kw: None)


async def test_pat_lifecycle_mcp_only_revocation_audit_and_no_oauth_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.main import app

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

    async with factory() as session:
        alice = User(
            id=USER_A,
            email="alice@example.com",
            username="alice",
            hashed_password="x",
            organization_id=ORG_A,
            role="researcher",
            is_active=True,
        )
        bob_admin = User(
            id=USER_B,
            email="bob@example.com",
            username="bob_admin",
            hashed_password="x",
            organization_id=ORG_B,
            role="admin",
            is_active=True,
            is_superuser=True,
        )
        session.add_all([alice, bob_admin])
        await session.commit()

    backend = _StubBackend()
    backend.executions["exec-org-a"] = (
        str(ORG_A),
        SimpleNamespace(
            execution_id="exec-org-a",
            run_id="run-org-a",
            status="completed",
            progress_percentage=100.0,
            current_phase="complete",
            supervisor_type="research",
            workers_used=1,
            routing_decision={"strategy": "balanced"},
            execution_plan=None,
            execution_time_seconds=1.5,
            errors=[],
        ),
        {"findings": ["f1"]},
    )
    audit = _StubAuditLogger()
    fake_redis = _FakeRedis()
    jwt_service = _HybridJWTService(
        {
            "jwt-alice": _jwt_payload(USER_A, ORG_A, roles=["researcher"]),
            "jwt-bob-admin": _jwt_payload(USER_B, ORG_B, roles=["admin"]),
        },
        redis_client=fake_redis,
    )
    pat_service = PersonalAccessTokenService(session_factory=factory)

    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", True)
    _patch_app_lifespan(
        monkeypatch, session_factory=factory, audit_logger=audit, backend=backend
    )

    async def _override_session() -> Any:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_jwt_service] = lambda: jwt_service
    app.dependency_overrides[get_pat_service] = lambda: pat_service
    app.dependency_overrides[get_session] = _override_session

    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http,
        ):
            # D1: /.well-known/oauth-protected-resource is NOT served or public.
            assert (
                "GET",
                "/.well-known/oauth-protected-resource",
            ) not in PUBLIC_ROUTE_ALLOWLIST
            well_known_unauth = await http.get("/.well-known/oauth-protected-resource")
            assert well_known_unauth.status_code == 401
            well_known_auth = await http.get(
                "/.well-known/oauth-protected-resource",
                headers={"Authorization": "Bearer jwt-alice"},
            )
            assert well_known_auth.status_code == 404

            # Mint PAT with JWT -> plaintext shown once.
            created_resp = await http.post(
                "/api/v1/auth/tokens",
                headers={"Authorization": "Bearer jwt-alice"},
                json={"name": "claude-code", "expires_in_days": 30},
            )
            assert created_resp.status_code == 201, created_resp.text
            created = created_resp.json()
            pat_plaintext = created["token"]
            pat_id = created["id"]
            assert pat_plaintext.startswith(PAT_PREFIX)
            assert created["scopes"] == ["mcp:read"]

            # Listing returns metadata without plaintext or hash.
            listed_resp = await http.get(
                "/api/v1/auth/tokens",
                headers={"Authorization": "Bearer jwt-alice"},
            )
            assert listed_resp.status_code == 200
            listed = listed_resp.json()
            assert len(listed) == 1
            assert "token" not in listed[0]
            assert "token_hash" not in listed[0]
            assert listed[0]["id"] == pat_id

            # PAT is rejected on every non-/mcp route.
            for method, path, body in (
                (
                    "POST",
                    "/api/v1/auth/tokens",
                    {"name": "nested", "expires_in_days": 7},
                ),
                ("GET", "/api/v1/auth/tokens", None),
                ("DELETE", f"/api/v1/auth/tokens/{pat_id}", None),
                (
                    "POST",
                    "/api/v1/auth/change-password",
                    {
                        "current_password": "OldPassword123!",
                        "new_password": "NewPassword456!",
                        "confirm_password": "NewPassword456!",
                    },
                ),
                ("GET", "/api/v1/auth/me", None),
                ("GET", "/openapi.json", None),
                ("GET", "/api/v1/workflows", None),
                ("GET", "/api/v1/query/execution/exec-org-a/status", None),
                ("GET", "/api/v1/runs/run-org-a/evidence", None),
            ):
                rejected = await http.request(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {pat_plaintext}"},
                    json=body,
                )
                assert rejected.status_code == 401, (
                    method,
                    path,
                    rejected.status_code,
                )

            # PAT works on /mcp and records pat_id in audit events + last_used_at.
            transport = StreamableHttpTransport(
                url="http://testserver/mcp",
                headers={"Authorization": f"Bearer {pat_plaintext}"},
                httpx_client_factory=_asgi_client_factory(app),
            )
            async with Client(transport) as mcp_client:
                status_result = await mcp_client.call_tool(
                    "get_research_status", {"execution_id": "exec-org-a"}
                )
            assert status_result.data["run_id"] == "run-org-a"
            mcp_tool_events = [
                evt
                for evt in audit.events
                if evt.get("action") == "mcp.tool:get_research_status"
            ]
            assert mcp_tool_events
            assert mcp_tool_events[-1]["metadata"]["pat_id"] == pat_id
            http_mcp_events = [
                evt
                for evt in audit.events
                if evt.get("action") == "POST /mcp"
                and evt.get("metadata", {}).get("pat_id") == pat_id
            ]
            assert http_mcp_events

            # Cross-org admin (Org B superuser) cannot revoke Org A's PAT -> 404.
            cross_revoke = await http.delete(
                f"/api/v1/auth/tokens/{pat_id}",
                headers={"Authorization": "Bearer jwt-bob-admin"},
            )
            assert cross_revoke.status_code == 404

            # PAT lacking mcp:read scope cannot call read tools on /mcp.
            start_only_resp = await http.post(
                "/api/v1/auth/tokens",
                headers={"Authorization": "Bearer jwt-alice"},
                json={
                    "name": "start-only",
                    "expires_in_days": 7,
                    "scopes": [PAT_SCOPE_START],
                },
            )
            assert start_only_resp.status_code == 201
            start_only_pat = start_only_resp.json()["token"]
            start_only_transport = StreamableHttpTransport(
                url="http://testserver/mcp",
                headers={"Authorization": f"Bearer {start_only_pat}"},
                httpx_client_factory=_asgi_client_factory(app),
            )
            async with Client(start_only_transport) as mcp_client:
                with pytest.raises(ToolError, match="mcp:read"):
                    await mcp_client.call_tool(
                        "get_research_status", {"execution_id": "exec-org-a"}
                    )

            # Owner revokes the first PAT -> 204, subsequent /mcp call -> 401.
            revoked_resp = await http.delete(
                f"/api/v1/auth/tokens/{pat_id}",
                headers={"Authorization": "Bearer jwt-alice"},
            )
            assert revoked_resp.status_code == 204
            post_revoke = await http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {pat_plaintext}",
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert post_revoke.status_code == 401

            # User-wide revoke-all kills a fresh PAT immediately.
            second_resp = await http.post(
                "/api/v1/auth/tokens",
                headers={"Authorization": "Bearer jwt-alice"},
                json={"name": "second", "expires_in_days": 7},
            )
            assert second_resp.status_code == 201
            second_pat = second_resp.json()["token"]
            revoke_all = await http.post(
                "/api/v1/auth/revoke-all",
                headers={"Authorization": "Bearer jwt-alice"},
            )
            assert revoke_all.status_code == 204
            after_revoke_all = await http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {second_pat}",
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert after_revoke_all.status_code == 401

            # Deactivated user's PAT is rejected on /mcp.
            third_resp = await http.post(
                "/api/v1/auth/tokens",
                headers={"Authorization": "Bearer jwt-alice"},
                json={"name": "third", "expires_in_days": 7},
            )
            assert third_resp.status_code == 201
            third_pat = third_resp.json()["token"]
            async with factory() as session:
                db_alice = await session.get(User, USER_A)
                assert db_alice is not None
                db_alice.is_active = False
                await session.commit()
            after_deactivate = await http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {third_pat}",
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert after_deactivate.status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
