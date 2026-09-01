"""
WebSocket authentication utilities.

This module provides authentication and authorization for WebSocket connections.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Query, WebSocket, WebSocketException
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.api.websocket.run_stream import (
    RunStreamAuthorizationError,
    RunStreamEntitlement,
)
from src.auth.jwt_service import JWTService, JWTServiceUnavailableError
from src.core.config import settings
from src.middleware.auth_middleware import (
    AuthenticationServiceUnavailableError,
    resolve_jwt_service,
)
from src.repositories.research_repository import ResearchRepository

logger = get_logger()


@dataclass(frozen=True)
class WebSocketPrincipal:
    """Who a WebSocket connection is, and which tenant it may read.

    ``organization_id`` is the tenant boundary and is derived from the
    authenticated token's claim only — never from a path parameter or query
    string. Both fields are ``None`` for a connection admitted by the
    explicit anonymous-development opt-in, which carries no claims at all.
    """

    user_id: str | None
    organization_id: str | None
    client_type: str


class WebSocketAuthError(Exception):
    """WebSocket authentication error."""

    def __init__(self, message: str, code: int = 1008):
        self.message = message
        self.code = code  # WebSocket close code
        super().__init__(message)


async def verify_websocket_claims(
    token: str | None,
    jwt_service: JWTService,
    *,
    allow_anonymous: bool = True,
) -> tuple[str | None, str | None]:
    """Validate a WebSocket token and return ``(user_id, organization_id)``.

    Both values are ``None`` for a connection admitted by the explicit
    anonymous-development opt-in. ``organization_id`` alone is ``None`` for a
    token that carries no tenant claim; callers that scope anything by tenant
    must treat that as unauthorized rather than as unscoped.

    Args:
        token: JWT token string.
        jwt_service: Shared JWT service (same one used by HTTP middleware)
            so that WebSocket and HTTP requests validate tokens against the
            same RSA key pair and blacklist.

    Raises:
        WebSocketAuthError: If authentication fails.
    """
    if not token:
        # Anonymous access is a narrow, explicit local-development opt-in.
        # ENVIRONMENT alone is not an authorization control, and production
        # can never enable this bypass even if the opt-in is misconfigured.
        if allow_anonymous and (
            settings.ENVIRONMENT == "development"
            and settings.DEV_ALLOW_ANONYMOUS_WEBSOCKETS
        ):
            logger.warning(
                "Allowing anonymous WebSocket connection by explicit development opt-in"
            )
            return None, None

        raise WebSocketAuthError("Authentication token required")

    try:
        # Remove 'Bearer ' prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        # Validate via the shared JWT service so RS256 keys and the Redis
        # blacklist match the rest of the auth stack.
        token_payload = await jwt_service.validate_token(token)

        user_id = token_payload.sub
        if not user_id:
            raise WebSocketAuthError("Invalid token: missing user ID")

        organization_id = token_payload.organization_id

        logger.info(
            "WebSocket authentication successful",
            user_id=user_id,
        )

        return str(user_id), str(organization_id) if organization_id else None

    except WebSocketAuthError:
        raise

    except JWTServiceUnavailableError as e:
        logger.error(
            "WebSocket authentication service unavailable",
            error=str(e),
        )
        raise WebSocketAuthError("Authentication service unavailable", code=1011) from e

    except JWTError as e:
        logger.warning(
            "WebSocket JWT authentication failed",
            error=str(e),
        )
        raise WebSocketAuthError("Invalid authentication token") from e

    except Exception as e:
        logger.error(
            "WebSocket authentication error",
            error=str(e),
        )
        raise WebSocketAuthError("Authentication failed") from e


async def verify_websocket_token(
    token: str | None,
    jwt_service: JWTService,
    *,
    allow_anonymous: bool = True,
) -> str | None:
    """Verify a WebSocket token and return its user id.

    Retained for callers that need identity but no tenant scope; see
    :func:`verify_websocket_claims` for the full claim set.
    """
    user_id, _ = await verify_websocket_claims(
        token, jwt_service, allow_anonymous=allow_anonymous
    )
    return user_id


async def verify_project_access(
    user_id: str | None,
    project_id: str,
    *,
    organization_id: str | None,
    session: AsyncSession | None,
) -> bool:
    """Verify that a caller may read a specific project's live updates.

    Access is the project actually existing inside the caller's tenant
    organization and belonging to the caller — exactly the boundary the HTTP
    project endpoints apply, read through the same org-filtered repository
    query. The repository ``organization_id`` filter is the enforcement;
    Postgres row-level security is not a boundary here (no
    ``FORCE ROW LEVEL SECURITY``, and the application connects as the table
    owner, so policies are bypassed).

    Fails closed on every missing input. A caller with no user identity, no
    tenant claim, or no database session cannot be shown to own anything, and
    "cannot prove" is denied rather than allowed. A project outside the
    caller's tenant is indistinguishable from a project that does not exist.

    Args:
        user_id: Authenticated user id, or ``None`` for an anonymous caller.
        project_id: The project the connection wants to subscribe to.
        organization_id: The caller's tenant claim, from its token only.
        session: Database session used to resolve project ownership.

    Returns:
        True only if the project resolves inside the caller's boundary.
    """
    # Match the explicit anonymous-development opt-in used by token
    # verification. Merely selecting the development environment must not
    # disable project authorization.
    if (
        settings.ENVIRONMENT == "development"
        and settings.DEV_ALLOW_ANONYMOUS_WEBSOCKETS
    ):
        return True

    if not user_id or not organization_id or session is None:
        logger.warning(
            "Denying WebSocket project access without a complete tenant context",
            project_id=project_id,
            has_user=bool(user_id),
            has_organization=bool(organization_id),
            has_session=session is not None,
        )
        return False

    try:
        project_uuid = UUID(project_id)
        organization_uuid = UUID(organization_id)
    except (ValueError, AttributeError, TypeError):
        logger.warning(
            "Denying WebSocket project access for an unusable identifier",
            project_id=project_id,
        )
        return False

    project = await ResearchRepository(session).get_for_user(
        project_uuid,
        user_id=user_id,
        organization_id=organization_uuid,
    )
    if project is None:
        logger.warning(
            "Denying WebSocket project access outside the caller's boundary",
            project_id=project_id,
            user_id=user_id,
        )
        return False
    return True


async def resolve_run_stream_entitlement(
    token: str | None,
    jwt_service: JWTService,
) -> RunStreamEntitlement:
    """Derive a run-stream subscriber's tenant entitlement from its token.

    Deliberately does not honor the anonymous development opt-in that
    ``verify_websocket_token`` allows: an anonymous connection carries no
    organization claim, and the run event stream has no safe unscoped mode.
    Every subscriber must present a token with a tenant organization.

    Raises:
        WebSocketAuthError: The token is missing, invalid, or carries no
            usable organization claim.
    """
    if not token:
        raise WebSocketAuthError("Authentication token required")

    if token.startswith("Bearer "):
        token = token[7:]

    try:
        token_payload = await jwt_service.validate_token(token)
    except WebSocketAuthError:
        raise
    except JWTError as e:
        raise WebSocketAuthError("Invalid authentication token") from e
    except Exception as e:
        raise WebSocketAuthError("Authentication failed") from e

    try:
        return RunStreamEntitlement.from_organization_claim(
            token_payload.organization_id, user_id=str(token_payload.sub)
        )
    except RunStreamAuthorizationError as e:
        raise WebSocketAuthError(str(e), code=1008) from e


def extract_client_type(user_agent: str | None) -> str:
    """
    Extract client type from User-Agent header.

    Args:
        user_agent: User-Agent header value

    Returns:
        Client type string ("cli", "web", etc.)
    """
    if not user_agent:
        return "unknown"

    user_agent_lower = user_agent.lower()

    if "research-cli" in user_agent_lower:
        return "cli"
    elif "python" in user_agent_lower:
        return "api"
    elif "websocket" in user_agent_lower:
        return "websocket"
    else:
        return "web"


async def authenticate_websocket_connection(
    token: str | None,
    user_agent: str | None,
    jwt_service: JWTService,
    *,
    allow_anonymous: bool = True,
) -> tuple[str | None, str]:
    """
    Authenticate a WebSocket connection and determine client type.

    Args:
        token: Authentication token
        user_agent: User-Agent header
        jwt_service: Shared JWT service used to validate the token

    Returns:
        Tuple of (user_id, client_type)

    Raises:
        WebSocketAuthError: If authentication fails
    """
    principal = await authenticate_websocket_principal(
        token,
        user_agent,
        jwt_service,
        allow_anonymous=allow_anonymous,
    )
    return principal.user_id, principal.client_type


async def authenticate_websocket_principal(
    token: str | None,
    user_agent: str | None,
    jwt_service: JWTService,
    *,
    allow_anonymous: bool = True,
) -> WebSocketPrincipal:
    """Authenticate a connection and carry its tenant claim forward.

    Used by endpoints that authorize against a tenant boundary, so the
    organization claim comes from the same single token validation as the
    user id rather than from a second, separately-decoded copy.

    Raises:
        WebSocketAuthError: If authentication fails.
    """
    user_id, organization_id = await verify_websocket_claims(
        token,
        jwt_service,
        allow_anonymous=allow_anonymous,
    )
    client_type = extract_client_type(user_agent)

    logger.info(
        "WebSocket connection authenticated",
        user_id=user_id,
        client_type=client_type,
    )

    return WebSocketPrincipal(
        user_id=user_id,
        organization_id=organization_id,
        client_type=client_type,
    )


async def require_authenticated_websocket(
    websocket: WebSocket,
    token: str | None = Query(None, description="JWT authentication token"),
) -> None:
    """Require query-token JWT claims before a mounted handler runs.

    WebSocket authentication intentionally reads only the ``token`` query
    dependency. An HTTP ``Authorization`` header alone does not authenticate a
    mounted WebSocket connection.
    """
    try:
        jwt_service = await resolve_jwt_service(websocket)
        principal = await authenticate_websocket_principal(
            token,
            websocket.headers.get("user-agent"),
            jwt_service,
            allow_anonymous=False,
        )
        websocket.state.websocket_principal = principal
    except WebSocketAuthError as exc:
        raise WebSocketException(code=exc.code, reason=exc.message) from exc
    except AuthenticationServiceUnavailableError as exc:
        raise WebSocketException(
            code=1011,
            reason="Authentication service unavailable",
        ) from exc
    except Exception as exc:
        logger.error(
            "Mounted WebSocket authentication service unavailable",
            error=str(exc),
        )
        raise WebSocketException(
            code=1011,
            reason="Authentication service unavailable",
        ) from exc
