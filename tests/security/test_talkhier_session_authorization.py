"""Authorization tests for in-memory TalkHier sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import talkhier_api
from src.api.services.talkhier_session_manager import TalkHierSessionManager
from src.api.services.talkhier_session_service import (
    TalkHierSession,
    TalkHierSessionService,
)
from src.api.websocket.auth import WebSocketPrincipal, require_authenticated_websocket
from src.auth.models import TokenPayload
from src.middleware.tenant_context import TenantContext
from src.models.talkhier_api_models import (
    ConsensusCheckRequest,
    ConsensusType,
    CoordinationRequest,
    CoordinationStatus,
    MessageRole,
    ParticipantInfo,
    ProtocolType,
    RefinementRoundRequest,
    RefinementStrategy,
    SessionCloseRequest,
    SessionStatus,
    TalkHierSessionRequest,
)

OWNER = TenantContext(user_id="user-a", organization_id="org-a")
SAME_TENANT_INTRUDER = TenantContext(user_id="user-b", organization_id="org-a")
CROSS_TENANT_INTRUDER = TenantContext(user_id="user-a", organization_id="org-b")


def _session(
    session_id: str = "session-owned",
    *,
    user_id: str = OWNER.user_id,
    organization_id: str = OWNER.organization_id,
) -> TalkHierSession:
    return TalkHierSession(
        session_id=session_id,
        user_id=user_id,
        organization_id=organization_id,
        query="Compare two research methods",
        domains=["research"],
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(UTC),
        protocol_type=ProtocolType.STANDARD,
        refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
        max_rounds=3,
        min_rounds=1,
        quality_threshold=0.85,
        consensus_type=ConsensusType.WEIGHTED,
        consensus_threshold=0.8,
        timeout_seconds=300,
        participants=[
            ParticipantInfo(
                agent_id="agent-1",
                agent_type="research",
                role=MessageRole.WORKER,
                confidence=0.9,
                rounds_participated=0,
                quality_scores=[],
            )
        ],
        started_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_session_creation_binds_authenticated_owner_and_tenant() -> None:
    service = TalkHierSessionService()
    request = TalkHierSessionRequest(query="Compare retrieval methods")

    with patch.object(service, "_get_routing_decision") as mock_routing:
        mock_routing.return_value = AsyncMock(
            supervisor_allocations=[MagicMock(supervisor_type="research")],
            agent_allocation=MagicMock(
                agents=[
                    MagicMock(agent_type="literature-review"),
                    MagicMock(agent_type="synthesis"),
                ]
            ),
        )

        response = await service.create_session(
            request,
            user_id=OWNER.user_id,
            organization_id=OWNER.organization_id,
        )

    session = service.sessions[response.session_id]
    assert session.user_id == OWNER.user_id
    assert session.organization_id == OWNER.organization_id


@pytest.mark.asyncio
@pytest.mark.parametrize("intruder", [SAME_TENANT_INTRUDER, CROSS_TENANT_INTRUDER])
async def test_service_session_operations_reject_non_owner_or_wrong_tenant(
    intruder: TenantContext,
) -> None:
    service = TalkHierSessionService()
    service.sessions["session-owned"] = _session()
    service.session_metrics["session-owned"] = {
        "rounds_completed": 0,
        "quality_progression": [],
        "consensus_progression": [],
        "message_count": 0,
    }

    async def get_status() -> object:
        return await service.get_session_status(
            "session-owned",
            user_id=intruder.user_id,
            organization_id=intruder.organization_id,
        )

    async def execute_round() -> object:
        return await service.execute_refinement_round(
            "session-owned",
            RefinementRoundRequest(round_number=1),
            user_id=intruder.user_id,
            organization_id=intruder.organization_id,
        )

    async def check_consensus() -> object:
        return await service.check_consensus(
            "session-owned",
            ConsensusCheckRequest(
                round_results=[
                    {"agent": "agent-1", "confidence": 0.9, "content": "result"}
                ]
            ),
            user_id=intruder.user_id,
            organization_id=intruder.organization_id,
        )

    async def close_session() -> object:
        return await service.close_session(
            "session-owned",
            SessionCloseRequest(save_transcript=False, generate_summary=False),
            user_id=intruder.user_id,
            organization_id=intruder.organization_id,
        )

    for call in [get_status, execute_round, check_consensus, close_session]:
        with pytest.raises(ValueError, match="not found"):
            await call()


@pytest.mark.asyncio
async def test_rest_status_endpoint_rejects_client_supplied_session_id_for_other_user() -> (
    None
):
    service = TalkHierSessionService()
    service.sessions["session-owned"] = _session()

    with pytest.raises(HTTPException) as exc_info:
        await talkhier_api.get_session_status(
            service,
            tenant_context=SAME_TENANT_INTRUDER,
            session_id="session-owned",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rest_coordination_rejects_any_foreign_session_id() -> None:
    service = TalkHierSessionService()
    service.sessions["session-owned"] = _session("session-owned")
    service.sessions["session-foreign"] = _session(
        "session-foreign",
        user_id=SAME_TENANT_INTRUDER.user_id,
        organization_id=SAME_TENANT_INTRUDER.organization_id,
    )

    with pytest.raises(HTTPException) as exc_info:
        await talkhier_api.coordinate_multiple_sessions(
            CoordinationRequest(
                session_ids=["session-owned", "session-foreign"],
                coordination_type="parallel",
            ),
            talkhier_service=service,
            tenant_context=OWNER,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_analytics_endpoint_passes_authenticated_tenant_to_manager() -> None:
    analytics = {
        "total_sessions": 1,
        "active_sessions": 1,
        "average_rounds": 1.0,
        "average_quality": 0.9,
        "average_consensus": 0.9,
        "success_rate": 1.0,
        "timeout_rate": 0.0,
        "protocol_usage": {"standard": 1},
        "strategy_performance": {},
        "quality_trends": [],
        "consensus_patterns": {},
    }

    with patch.object(
        talkhier_api.session_manager,
        "get_analytics",
        new=AsyncMock(return_value=analytics),
    ) as get_analytics:
        response = await talkhier_api.get_protocol_analytics(
            tenant_context=OWNER,
            time_range="24h",
            protocol_type=None,
            min_quality=None,
        )

    assert response.total_sessions == 1
    get_analytics.assert_awaited_once_with(
        time_range="24h",
        protocol_type=None,
        min_quality=None,
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )


@pytest.mark.asyncio
async def test_manager_analytics_requires_both_tenant_identities() -> None:
    manager = TalkHierSessionManager()
    await manager.register_session(
        "session-owned",
        {},
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )
    await manager.register_session(
        "session-same-org",
        {},
        user_id=SAME_TENANT_INTRUDER.user_id,
        organization_id=SAME_TENANT_INTRUDER.organization_id,
    )
    await manager.register_session(
        "session-other-org",
        {},
        user_id=CROSS_TENANT_INTRUDER.user_id,
        organization_id=CROSS_TENANT_INTRUDER.organization_id,
    )

    analytics = await manager.get_analytics(
        time_range="24h",
        protocol_type=None,
        min_quality=None,
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )

    assert analytics["total_sessions"] == 1

    with pytest.raises(ValueError, match="Both user and organization are required"):
        await manager.get_analytics(user_id="", organization_id=OWNER.organization_id)


@pytest.mark.asyncio
async def test_manager_rejects_incomplete_identity_when_binding_records() -> None:
    manager = TalkHierSessionManager()

    with pytest.raises(ValueError, match="Both user and organization are required"):
        await manager.register_session(
            "session-invalid",
            {},
            user_id="",
            organization_id=OWNER.organization_id,
        )

    with pytest.raises(ValueError, match="Both user and organization are required"):
        await manager.log_analytics_event(
            "session_created",
            "session-invalid",
            {},
            datetime.now(UTC),
            user_id=OWNER.user_id,
            organization_id="",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("intruder", [SAME_TENANT_INTRUDER, CROSS_TENANT_INTRUDER])
async def test_manager_coordination_status_rejects_other_tenants(
    intruder: TenantContext,
) -> None:
    manager = TalkHierSessionManager()
    await manager.register_session(
        "session-owned",
        {},
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )
    await manager.register_session(
        "session-owned-2",
        {},
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )
    request = CoordinationRequest(
        session_ids=["session-owned", "session-owned-2"],
        coordination_type="parallel",
    )
    coordination = await manager.coordinate_sessions(
        request,
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )

    with pytest.raises(ValueError, match="not found"):
        await manager.get_coordination_status(
            coordination.coordination_id,
            user_id=intruder.user_id,
            organization_id=intruder.organization_id,
        )


@pytest.mark.asyncio
async def test_rest_coordination_passes_authenticated_tenant_to_manager() -> None:
    service = TalkHierSessionService()
    service.sessions["session-owned"] = _session()
    service.sessions["session-owned-2"] = _session("session-owned-2")
    status = CoordinationStatus(
        coordination_id="coord-owned",
        session_statuses={"session-owned": SessionStatus.ACTIVE},
        overall_progress=0.0,
        aggregated_quality=0.0,
    )
    request = CoordinationRequest(
        session_ids=["session-owned", "session-owned-2"],
        coordination_type="parallel",
    )

    with patch.object(
        talkhier_api.session_manager,
        "coordinate_sessions",
        new=AsyncMock(return_value=status),
    ) as coordinate_sessions:
        response = await talkhier_api.coordinate_multiple_sessions(
            request,
            talkhier_service=service,
            tenant_context=OWNER,
        )

    assert response.coordination_id == "coord-owned"
    coordinate_sessions.assert_awaited_once_with(
        request,
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
    )


@pytest.mark.asyncio
async def test_websocket_auth_stores_validated_principal_without_second_decode() -> (
    None
):
    now = datetime.now(UTC)
    token_payload = TokenPayload(
        sub=OWNER.user_id,
        email="owner@example.com",
        roles=[],
        permissions=[],
        organization_id=OWNER.organization_id,
        jti="jti-owner",
        iat=now,
        exp=now,
    )
    jwt_service = AsyncMock()
    jwt_service.validate_token.return_value = token_payload
    websocket = SimpleNamespace(
        headers={"user-agent": "pytest websocket"},
        state=SimpleNamespace(),
    )

    with patch(
        "src.api.websocket.auth.resolve_jwt_service",
        new=AsyncMock(return_value=jwt_service),
    ):
        await require_authenticated_websocket(websocket, token="valid-token")  # type: ignore[arg-type]

    assert jwt_service.validate_token.await_count == 1
    assert websocket.state.websocket_principal == WebSocketPrincipal(
        user_id=OWNER.user_id,
        organization_id=OWNER.organization_id,
        client_type="websocket",
    )


@pytest.mark.asyncio
async def test_live_websocket_rejects_foreign_session_before_registration() -> None:
    service = TalkHierSessionService()
    service.sessions["session-owned"] = _session()
    websocket = AsyncMock()
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(talkhier_session_service=service)
    )
    websocket.state = SimpleNamespace(
        websocket_principal=WebSocketPrincipal(
            user_id=SAME_TENANT_INTRUDER.user_id,
            organization_id=SAME_TENANT_INTRUDER.organization_id,
            client_type="websocket",
        )
    )

    with (
        patch.object(
            talkhier_api.connection_manager,
            "connect",
            new=AsyncMock(return_value="connection-1"),
        ),
        patch.object(
            talkhier_api.connection_manager,
            "disconnect",
            new=AsyncMock(),
        ),
        patch.object(
            talkhier_api.websocket_handler,
            "register_session_connection",
            new=AsyncMock(),
        ) as register,
    ):
        await talkhier_api.websocket_session_updates(websocket, "session-owned")

    register.assert_not_awaited()
    websocket.close.assert_awaited_once_with(code=1008, reason="Session not found")
