"""
Authentication middleware for request authentication and authorization.

Provides JWT validation and user context injection.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

import redis.asyncio as redis
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response

from src.api.middleware.error_envelope import build_error_payload
from src.auth.jwt_service import JWTService, JWTServiceUnavailableError
from src.auth.models import PAT_PREFIX, PersonalAccessTokenPayload, TokenPayload
from src.auth.pat_service import (
    PATServiceUnavailableError,
    PATValidationError,
    PersonalAccessTokenService,
)
from src.core.config import settings
from src.models.db.audit_log import AuditEventType
from src.models.db.session import get_maintenance_session_factory, get_session
from src.models.db.user import User
from src.repositories.user_repository import UserRepository
from src.security.audit_middleware import audit_rejection

logger = structlog.get_logger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)


class AuthenticationServiceUnavailableError(RuntimeError):
    """Raised when the service needed to validate a credential is unavailable."""


# Preserve the existing import name for callers that use this compatibility
# exception directly.
AuthenticationServiceUnavailable = AuthenticationServiceUnavailableError


# This is deliberately a method-and-path allowlist. Prefixes and wildcard
# exclusions make newly mounted routes public by accident, so they are not
# supported by the boundary.
PUBLIC_ROUTE_ALLOWLIST: Final[dict[tuple[str, str], str]] = {
    ("GET", "/health"): "liveness and process health probe",
    ("GET", "/live"): "liveness probe",
    ("GET", "/ready"): "readiness probe",
    ("GET", "/ws/health"): "WebSocket service health probe",
    ("POST", "/api/v1/auth/login"): "authentication bootstrap",
    ("POST", "/api/v1/auth/register"): "authentication bootstrap",
    ("POST", "/api/v1/auth/refresh"): "authentication bootstrap",
    ("POST", "/api/v1/auth/forgot-password"): "password recovery bootstrap",
    ("POST", "/api/v1/auth/reset-password"): "password recovery bootstrap",
    ("GET", "/api/v1/auth/verify-email"): "email verification bootstrap",
}


def _set_request_identity(
    request: Request, token_payload: TokenPayload | PersonalAccessTokenPayload
) -> None:
    """Expose the validated identity to audit, rate-limit, and tenant code."""
    request.state.user = token_payload.sub
    request.state.user_id = token_payload.sub
    request.state.token_payload = token_payload
    request.state.organization_id = token_payload.organization_id
    request.state.pat_id = (
        token_payload.pat_id
        if isinstance(token_payload, PersonalAccessTokenPayload)
        else None
    )


def _authentication_response(
    status_code: int,
    message: str,
) -> JSONResponse:
    """Build an auth response without invoking route validation or handlers."""
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    code = (
        "AUTHENTICATION_REQUIRED"
        if status_code == 401
        else "AUTHENTICATION_SERVICE_UNAVAILABLE"
    )
    return JSONResponse(
        status_code=status_code,
        content=build_error_payload(code=code, message=message),
        headers=headers,
    )


def _extract_bearer_token(request: Request) -> str | None:
    """Return a bearer token only for an explicitly bearer authorization header."""
    header = request.headers.get("authorization")
    if not header:
        return None

    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token


async def resolve_jwt_service(connection: HTTPConnection) -> JWTService:
    """Resolve JWT validation while honoring FastAPI test overrides."""
    dependency_overrides = getattr(connection.app, "dependency_overrides", {})
    service_factory: Callable[..., Any] = dependency_overrides.get(
        get_jwt_service, get_jwt_service
    )

    try:
        service = service_factory()
        if inspect.isawaitable(service):
            service = await service
    except Exception as exc:
        logger.error("Authentication service unavailable", error=str(exc))
        raise AuthenticationServiceUnavailableError from exc

    if service is None or not callable(getattr(service, "validate_token", None)):
        logger.error("Authentication service has no token validator")
        raise AuthenticationServiceUnavailableError

    return cast(JWTService, service)


async def get_pat_service() -> PersonalAccessTokenService:
    """Return a PAT validator backed by the maintenance session factory."""
    try:
        return PersonalAccessTokenService(
            session_factory=get_maintenance_session_factory()
        )
    except Exception as exc:
        logger.error("PAT authentication service unavailable", error=str(exc))
        raise AuthenticationServiceUnavailableError from exc


async def resolve_pat_service(
    connection: HTTPConnection,
) -> PersonalAccessTokenService:
    """Resolve PAT validation while honoring FastAPI test overrides."""
    dependency_overrides = getattr(connection.app, "dependency_overrides", {})
    service_factory: Callable[..., Any] = dependency_overrides.get(
        get_pat_service, get_pat_service
    )

    try:
        service = service_factory()
        if inspect.isawaitable(service):
            service = await service
    except Exception as exc:
        logger.error("PAT authentication service unavailable", error=str(exc))
        raise AuthenticationServiceUnavailableError from exc

    if service is None or not callable(getattr(service, "validate_token", None)):
        logger.error("PAT authentication service has no token validator")
        raise AuthenticationServiceUnavailableError

    return cast(PersonalAccessTokenService, service)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware for all requests.

    Validates JWT tokens and adds user context to requests.
    """

    def __init__(self, app: Any, exclude_paths: list[str] | None = None) -> None:
        """
        Initialize authentication middleware.

        ``exclude_paths`` is retained only for constructor compatibility. The
        boundary intentionally ignores it: public access is defined by the
        exact :data:`PUBLIC_ROUTE_ALLOWLIST` above.
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process request with authentication.

        Args:
            request: Incoming request
            call_next: Next middleware or endpoint

        Returns:
            Response from endpoint
        """
        # Initialize state for both public and protected requests. Protected
        # requests replace these values only after a successful JWT validation.
        request.state.user = None
        request.state.user_id = None
        request.state.token_payload = None
        request.state.organization_id = None
        request.state.pat_id = None

        route_signature = (request.method.upper(), request.url.path)
        if route_signature in PUBLIC_ROUTE_ALLOWLIST:
            return await call_next(request)

        token = _extract_bearer_token(request)
        if token is None:
            audit_rejection(request, AuditEventType.UNAUTHORIZED_ACCESS, "no token")
            return _authentication_response(401, "Authentication required")

        if token.startswith(PAT_PREFIX) and request.url.path != "/mcp":
            audit_rejection(
                request,
                AuditEventType.UNAUTHORIZED_ACCESS,
                "pat restricted to /mcp",
            )
            return _authentication_response(
                401, "Personal access tokens are only accepted on /mcp"
            )

        token_payload: TokenPayload | PersonalAccessTokenPayload
        try:
            jwt_service = await resolve_jwt_service(request)
            if token.startswith(PAT_PREFIX):
                pat_service = await resolve_pat_service(request)
                token_payload = await pat_service.validate_token(
                    token, jwt_service=jwt_service
                )
            else:
                token_payload = await jwt_service.validate_token(token)
        except (
            AuthenticationServiceUnavailableError,
            JWTServiceUnavailableError,
            PATServiceUnavailableError,
        ):
            return _authentication_response(503, "Authentication service unavailable")
        except (JWTError, PATValidationError):
            audit_rejection(
                request, AuditEventType.UNAUTHORIZED_ACCESS, "invalid token"
            )
            return _authentication_response(401, "Invalid authentication token")
        except Exception as exc:
            logger.error("Authentication validation unavailable", error=str(exc))
            return _authentication_response(503, "Authentication service unavailable")

        _set_request_identity(request, token_payload)
        response: Response = await call_next(request)
        return response


_shared_jwt_service: JWTService | None = None


async def get_jwt_service() -> JWTService:
    """
    Get the process-wide JWT service (one Redis client, one parsed key pair).

    This is a dependency that can be overridden in tests. Built lazily on first
    use so paths that never enter the app lifespan still work; the lifespan
    calls :func:`close_jwt_service` on shutdown.
    """
    global _shared_jwt_service
    if _shared_jwt_service is not None:
        return _shared_jwt_service
    try:
        redis_client = await redis.from_url(settings.REDIS_URL)
        service = JWTService(
            redis_client=redis_client,
            private_key_path=settings.JWT_PRIVATE_KEY_PATH,
            public_key_path=settings.JWT_PUBLIC_KEY_PATH,
        )
    except Exception as exc:
        logger.error("Authentication service initialization failed", error=str(exc))
        raise AuthenticationServiceUnavailableError from exc
    if _shared_jwt_service is None:
        _shared_jwt_service = service
    else:  # lost a first-request race; keep the winner
        await redis_client.close()
    return _shared_jwt_service


async def close_jwt_service() -> None:
    """Close the shared service's Redis client and drop the cached instance."""
    global _shared_jwt_service
    service, _shared_jwt_service = _shared_jwt_service, None
    if service is not None and service.redis_client is not None:
        await service.redis_client.close()


async def get_current_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> TokenPayload:
    """
    Get current token payload from request.

    Args:
        credentials: HTTP authorization credentials
        jwt_service: JWT service for token validation

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or missing
    """
    cached_payload = getattr(request.state, "token_payload", None)
    if isinstance(cached_payload, PersonalAccessTokenPayload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Personal access tokens are not accepted on this endpoint",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if isinstance(cached_payload, TokenPayload):
        return cached_payload

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    if token.startswith(PAT_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Personal access tokens are not accepted on this endpoint",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_payload = await jwt_service.validate_token(token)

    except JWTError as e:
        logger.warning("Token validation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except JWTServiceUnavailableError as e:
        logger.error("Authentication service unavailable", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from e
    except Exception as e:
        logger.error("Authentication service unavailable", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from e

    _set_request_identity(request, token_payload)
    return token_payload


async def get_current_user(
    token_payload: TokenPayload = Depends(get_current_token),
    db: AsyncSession = Depends(get_session),
) -> User:
    """
    Get current authenticated user.

    Args:
        token_payload: Decoded token payload
        db: Database session

    Returns:
        Current user object

    Raises:
        HTTPException: If user not found
    """
    user_repo = UserRepository(db)
    user = await user_repo.get(token_payload.sub)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current active user.

    Args:
        current_user: Current authenticated user

    Returns:
        Active user object

    Raises:
        HTTPException: If user is not active
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    return current_user


def require_superuser() -> Callable[..., Any]:
    """
    Dependency to require superuser privileges.

    Returns:
        Dependency function
    """

    async def superuser_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user is superuser."""
        if not current_user.is_superuser:
            logger.warning(
                "Access denied - superuser required", user_id=str(current_user.id)
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Superuser privileges required",
            )

        return current_user

    return superuser_checker
