"""Route-level tests for the run event stream transports.

Covers what the transports own rather than what the reader owns: that a
subscriber without a tenant organization claim is rejected before any event is
read, that a malformed resume cursor is refused rather than silently treated
as "from the beginning", and that the existing WebSocket endpoints keep their
current contract.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.api.routes import websocket as websocket_routes
from src.api.websocket.auth import (
    WebSocketAuthError,
    resolve_run_stream_entitlement,
)
from src.auth.models import TokenPayload

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _payload(organization_id: str | None) -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub="00000000-0000-0000-0000-000000000001",
        email="user@example.com",
        roles=["researcher"],
        permissions=[],
        organization_id=organization_id,
        jti=str(uuid.uuid4()),
        iat=now,
        exp=now + timedelta(minutes=10),
    )


class _StubJWTService:
    def __init__(self, payload: TokenPayload | None, *, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises

    async def validate_token(self, token: str) -> TokenPayload:
        if self._raises or self._payload is None:
            raise ValueError("invalid token")
        return self._payload


class TestRunStreamEntitlementResolution:
    """A stream subscriber must present a token carrying an organization."""

    @pytest.mark.asyncio
    async def test_valid_token_with_org_claim_yields_entitlement(self) -> None:
        service = _StubJWTService(_payload(str(ORG_ID)))

        entitlement = await resolve_run_stream_entitlement("token", service)  # type: ignore[arg-type]

        assert entitlement.organization_id == ORG_ID
        assert entitlement.user_id == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_bearer_prefix_is_accepted(self) -> None:
        service = _StubJWTService(_payload(str(ORG_ID)))

        entitlement = await resolve_run_stream_entitlement("Bearer token", service)  # type: ignore[arg-type]

        assert entitlement.organization_id == ORG_ID

    @pytest.mark.asyncio
    async def test_token_without_org_claim_is_rejected(self) -> None:
        service = _StubJWTService(_payload(None))

        with pytest.raises(WebSocketAuthError):
            await resolve_run_stream_entitlement("token", service)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_missing_token_is_rejected(self) -> None:
        service = _StubJWTService(_payload(str(ORG_ID)))

        with pytest.raises(WebSocketAuthError):
            await resolve_run_stream_entitlement(None, service)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_invalid_token_is_rejected(self) -> None:
        service = _StubJWTService(None, raises=True)

        with pytest.raises(WebSocketAuthError):
            await resolve_run_stream_entitlement("token", service)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_anonymous_development_opt_in_does_not_apply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dev bypass that ``/ws`` allows must not open an unscoped stream."""
        from src.core import config

        monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(
            config.settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", True, raising=False
        )
        service = _StubJWTService(_payload(str(ORG_ID)))

        with pytest.raises(WebSocketAuthError):
            await resolve_run_stream_entitlement(None, service)  # type: ignore[arg-type]


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)


class TestRunStreamWebSocketEndpoint:
    """The endpoint refuses to open a stream it cannot scope or position."""

    @pytest.mark.asyncio
    async def test_missing_org_claim_closes_the_socket(self) -> None:
        socket = _RecordingWebSocket()

        await websocket_routes.run_event_stream_endpoint(
            socket,  # type: ignore[arg-type]
            "run-1",
            token="token",
            cursor=None,
            jwt_service=_StubJWTService(_payload(None)),  # type: ignore[arg-type]
        )

        assert socket.accepted is True
        assert socket.closed is not None
        assert socket.closed[0] == 1008
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_malformed_cursor_closes_the_socket(self) -> None:
        socket = _RecordingWebSocket()

        await websocket_routes.run_event_stream_endpoint(
            socket,  # type: ignore[arg-type]
            "run-1",
            token="token",
            cursor="not-a-cursor",
            jwt_service=_StubJWTService(_payload(str(ORG_ID))),  # type: ignore[arg-type]
        )

        assert socket.closed == (1008, "Invalid resume cursor")
        assert socket.sent == []


class TestExistingWebSocketContract:
    """The endpoints other clients already use are unchanged."""

    def test_existing_endpoints_are_still_registered(self) -> None:
        paths = {
            route.path
            for route in websocket_routes.router.routes
            if hasattr(route, "path")
        }

        assert {"/ws", "/ws/projects/{project_id}", "/ws/cli/{project_id}"} <= paths

    def test_run_stream_endpoint_is_additive(self) -> None:
        paths = {
            route.path
            for route in websocket_routes.router.routes
            if hasattr(route, "path")
        }

        assert "/ws/runs/{run_id}" in paths
