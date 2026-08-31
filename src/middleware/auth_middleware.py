"""
Authentication middleware for request authentication and authorization.

Provides JWT validation, user context injection, and permission checking.
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
from src.auth.models import TokenPayload
from src.core.config import settings
from src.models.db.session import get_session
from src.models.db.user import User
from src.repositories.user_repository import UserRepository

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
    ("POST", "/api/v1/auth/forgot-password"): "password recovery bootstrap",
    ("POST", "/api/v1/auth/reset-password"): "password recovery bootstrap",
    ("GET", "/api/v1/auth/verify-email"): "email verification bootstrap",
}


def _set_request_identity(request: Request, token_payload: TokenPayload) -> None:
    """Expose the validated identity to audit, rate-limit, and tenant code."""
    request.state.user = token_payload.sub
    request.state.user_id = token_payload.sub
    request.state.token_payload = token_payload
    request.state.organization_id = token_payload.organization_id


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

        route_signature = (request.method.upper(), request.url.path)
        if route_signature in PUBLIC_ROUTE_ALLOWLIST:
            return await call_next(request)

        token = _extract_bearer_token(request)
        if token is None:
            return _authentication_response(401, "Authentication required")

        try:
            jwt_service = await resolve_jwt_service(request)
            token_payload = await jwt_service.validate_token(token)
        except (
            AuthenticationServiceUnavailableError,
            JWTServiceUnavailableError,
        ):
            return _authentication_response(503, "Authentication service unavailable")
        except JWTError:
            return _authentication_response(401, "Invalid authentication token")
        except Exception as exc:
            logger.error("Authentication validation unavailable", error=str(exc))
            return _authentication_response(503, "Authentication service unavailable")

        _set_request_identity(request, token_payload)
        response: Response = await call_next(request)
        return response


async def get_jwt_service() -> JWTService:
    """
    Get JWT service instance.

    This is a dependency that can be overridden in tests.
    """
    try:
        redis_client = await redis.from_url(settings.REDIS_URL)
        return JWTService(
            redis_client=redis_client,
            private_key_path=settings.JWT_PRIVATE_KEY_PATH,
            public_key_path=settings.JWT_PUBLIC_KEY_PATH,
        )
    except Exception as exc:
        logger.error("Authentication service initialization failed", error=str(exc))
        raise AuthenticationServiceUnavailableError from exc


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
    if isinstance(cached_payload, TokenPayload):
        return cached_payload

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        # Validate token using injected JWT service
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


def require_roles(roles: list[str]) -> Callable[..., Any]:
    """
    Dependency to require specific roles.

    Args:
        roles: List of required roles (user must have at least one)

    Returns:
        Dependency function
    """

    async def role_checker(
        token_payload: TokenPayload = Depends(get_current_token),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user has required roles."""
        # Superusers bypass role checks
        if current_user.is_superuser:
            return current_user

        # Check if user has any of the required roles
        if not token_payload.has_any_role(roles):
            logger.warning(
                "Access denied - insufficient roles",
                user_id=str(current_user.id),
                required_roles=roles,
                user_roles=token_payload.roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )

        return current_user

    return role_checker


def require_permissions(permissions: list[str]) -> Callable[..., Any]:
    """
    Dependency to require specific permissions.

    Args:
        permissions: List of required permissions (user must have all)

    Returns:
        Dependency function
    """

    async def permission_checker(
        token_payload: TokenPayload = Depends(get_current_token),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user has required permissions."""
        # Superusers have all permissions
        if current_user.is_superuser or "*" in token_payload.permissions:
            return current_user

        # Check if user has all required permissions
        missing_permissions = [
            perm for perm in permissions if not token_payload.has_permission(perm)
        ]

        if missing_permissions:
            logger.warning(
                "Access denied - insufficient permissions",
                user_id=str(current_user.id),
                required_permissions=permissions,
                missing_permissions=missing_permissions,
                user_permissions=token_payload.permissions,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing_permissions)}",
            )

        return current_user

    return permission_checker


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


def optional_user() -> Callable[..., Any]:
    """
    Dependency for optional authentication.

    Returns user if authenticated, None otherwise.

    Returns:
        Dependency function
    """

    async def optional_user_getter(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
        db: AsyncSession = Depends(get_session),
        jwt_service: JWTService = Depends(get_jwt_service),
    ) -> User | None:
        """Get user if authenticated."""
        if not credentials:
            return None

        try:
            # Validate token using injected JWT service
            token_payload = await jwt_service.validate_token(credentials.credentials)

            # Get user
            user_repo = UserRepository(db)
            user = await user_repo.get(token_payload.sub)

            return user if user and user.is_active else None

        except Exception:
            return None

    return optional_user_getter


class RateLimitMiddleware:
    """
    Rate limiting middleware based on user or IP.

    Integrates with authentication to apply user-specific limits.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        """
        Initialize rate limit middleware.

        Args:
            requests_per_minute: Max requests per minute
            requests_per_hour: Max requests per hour
        """
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour

    async def __call__(
        self,
        request: Request,
        current_user: User | None = Depends(optional_user()),
    ) -> None:
        """
        Check rate limits for request.

        Args:
            request: Incoming request
            current_user: Authenticated user if available

        Raises:
            HTTPException: If rate limit exceeded
        """
        # Get identifier (user ID or IP)
        if current_user:
            identifier = f"user:{current_user.id}"
            # Use user-specific limits if configured
            limit = current_user.api_rate_limit or self.requests_per_hour
        else:
            # Use IP address for anonymous users
            client_ip = request.client.host if request.client else "unknown"
            identifier = f"ip:{client_ip}"
            limit = self.requests_per_minute

        # In production, implement actual rate limiting with Redis
        # For now, just log the check
        logger.debug("Rate limit check", identifier=identifier, limit=limit)
